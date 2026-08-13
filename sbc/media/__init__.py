"""Pure-Python custom media publisher for BBB's LiveKit bridge."""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import threading
import json
import math
import time
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, urljoin
from urllib.request import Request, urlopen

from ..core.exceptions import ConnectionError, MediaStalledError
from ..core.logging import debug_trace, get_logger
from .visuals import TextBoard as TextBoard, VisualSurface

LIVEKIT_CREDENTIALS = "subscription SBCLiveKitCredentials{user_current{userId livekit{livekitToken} meeting{audioBridge cameraBridge screenShareBridge}}}"
SCREENSHARE_CONTEXT = "query SBCScreenshareContext{meeting{meetingId screenShareBridge voiceSettings{voiceConf}}}"


def _enable_bbb_legacy_sha1_fingerprint() -> None:
    """Allow validation of legacy BBB SFU ``sha-1`` DTLS fingerprints.

    Some BBB 3.0 deployments still advertise only a ``sha-1`` certificate
    fingerprint in the SFU-generated SDP.  Chromium accepts that WebRTC SDP;
    modern aiortc deliberately supports only SHA-256/384/512 by default and
    therefore rejects an otherwise successful ICE connection with
    ``DTLS handshake failed (fingerprint mismatch)``.  Registering SHA-1 here
    does *not* bypass validation: aiortc still computes SHA-1 from the peer's
    certificate and requires an exact match with the SDP fingerprint.
    """
    import aiortc.rtcdtlstransport as dtls
    from cryptography.hazmat.primitives import hashes

    if "sha-1" not in dtls.X509_DIGEST_ALGORITHMS:
        dtls.X509_DIGEST_ALGORITHMS["sha-1"] = hashes.SHA1()
        debug_trace("media.legacy_sha1_fingerprint_enabled")


class MediaConnectionError(ConnectionError):
    """BBB did not provide a LiveKit credential for the saved session."""


@dataclass(frozen=True, slots=True)
class MediaHealth:
    """Observable custom-audio health based on real outbound RTP counters."""

    backend: str
    connected: bool
    packets_sent: int = 0
    bytes_sent: int = 0
    stale: bool = False
    recovered: bool = False
    reason: str | None = None
    observed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _GainAudioTrack:
    """Apply gain/fade-in in Python while preserving MediaPlayer's audio clock."""

    @staticmethod
    def create(source: Any, *, gain_db: float, fade_in: float):
        if gain_db == 0 and fade_in <= 0:
            return source
        from aiortc import MediaStreamTrack
        from av import AudioFrame
        import numpy as np

        class GainTrack(MediaStreamTrack):
            kind = "audio"
            def __init__(self):
                super().__init__()
                self._started: float | None = None
                self._multiplier = math.pow(10.0, gain_db / 20.0)
            async def recv(self):
                frame = await source.recv()
                now = time.monotonic()
                if self._started is None:
                    self._started = now
                fade = min(1.0, (now - self._started) / fade_in) if fade_in > 0 else 1.0
                multiplier = self._multiplier * fade
                if multiplier == 1.0:
                    return frame
                samples = frame.to_ndarray()
                scaled = np.clip(samples.astype(np.float32) * multiplier, -32768, 32767).astype(np.int16)
                output = AudioFrame.from_ndarray(scaled, format=frame.format.name, layout=frame.layout.name)
                output.pts, output.time_base, output.sample_rate = frame.pts, frame.time_base, frame.sample_rate
                return output
        return GainTrack()


class _SilenceAudioTrack:
    """Lazy aiortc-compatible 48 kHz stereo source for SFU warm-up.

    aiortc's Opus encoder fixes its resampler layout from the first warm-up
    frame. BBB/MediaPlayer file sources use 48 kHz ``s16`` stereo, so the
    silence source must use that same layout. A mono warm-up followed by a
    stereo MP3 makes the RTP task terminate with ``Frame does not match
    AudioResampler setup`` while the WebRTC connection misleadingly remains
    ``connected``.
    """
    # Defined as a wrapper so importing SBC itself does not import aiortc.
    @staticmethod
    def create():
        from aiortc import MediaStreamTrack
        from aiortc.mediastreams import MediaStreamError
        from av import AudioFrame

        class SilenceTrack(MediaStreamTrack):
            kind = "audio"
            def __init__(self):
                super().__init__()
                self.timestamp: int | None = None
                self.started: float | None = None
            async def recv(self):
                if self.readyState != "live":
                    raise MediaStreamError
                sample_rate, samples = 48_000, 960
                loop = asyncio.get_running_loop()
                if self.timestamp is None:
                    self.timestamp, self.started = 0, loop.time()
                else:
                    self.timestamp += samples
                    await asyncio.sleep(max(0, self.started + self.timestamp / sample_rate - loop.time()))
                frame = AudioFrame(format="s16", layout="stereo", samples=samples)
                for plane in frame.planes:
                    plane.update(b"\x00" * plane.buffer_size)
                frame.pts = self.timestamp
                frame.sample_rate = sample_rate
                frame.time_base = Fraction(1, sample_rate)
                return frame
        return SilenceTrack()


class _SFUAudioPublisher:
    """BBB ``bbb-webrtc-sfu`` audio publisher copied from AudioBroker's protocol."""
    def __init__(self, session: Any):
        self.session = session
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True, name="sbc-bbb-sfu")
        self.thread.start()
        self.pc = None; self.ws = None; self.player = None; self.connection_state = "new"
        self._session_number = 0
        self._signal_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._stopping = True
        self._ready = False
        self._active_file: str | None = None
        self._active_loop = True
        self._active_gain_db = 0.0
        self._active_fade_in = 0.0
        self._audio_sender = None
        self._silent_track = None
        self._warmed = False
        # Assigned by MediaController.  BBB's GraphQL input-mode setting is
        # separate from the SFU PeerConnection and must be re-applied whenever
        # a fresh media connection replaces a dropped one.
        self.on_connection_ready: Any = None
        # BBB's browser retries failed ICE/DTLS connections through TURN.  A
        # number of managed BBB installations are reachable only that way
        # from corporate networks, VPNs, and CGNAT connections.  Keep the
        # mode with the active publisher so automatic recovery uses the same
        # successful route.
        self._active_force_relay = False
        # Full-audio connections can receive the same conference mix as a
        # listener. MediaController assigns this when it exposes capture.
        self.on_audio_frame: Any | None = None
        self._receive_tasks: set[asyncio.Task[Any]] = set()
        self._receive_track_ids: set[int] = set()
    def _run(self): asyncio.set_event_loop(self.loop); self.loop.run_forever()
    def submit(self, coroutine): return asyncio.run_coroutine_threadsafe(coroutine, self.loop)
    def _url(self) -> str:
        configured = (self.session.snapshot.get("bbb_webrtc_sfu") or {}).get("url")
        if configured: base = configured
        else:
            parsed = urlparse(self.session.server)
            base = f"wss://{parsed.netloc}/bbb-webrtc-sfu"
        page_url = self.session.metadata.get("page_url", "")
        token = parse_qs(urlparse(page_url).query).get("sessionToken", [None])[0]
        if not token: token = (self.session.connection_payload.get("headers") or {}).get("X-Session-Token")
        if not token: raise MediaConnectionError("the SBC session does not contain a BBB sessionToken for bbb-webrtc-sfu")
        parsed = urlparse(base); query = parse_qs(parsed.query); query["sessionToken"] = [token]
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", urlencode(query, doseq=True), ""))
    @property
    def _settings(self) -> dict[str, Any]:
        return self.session.snapshot.get("bbb_webrtc_sfu") or {}
    def _setting(self, name: str, default: Any = None) -> Any:
        return self._settings.get(name, default)

    def _full_audio_offering(self) -> bool:
        """Return BBB HTML5's actual full-audio SDP-offering mode.

        BBB's ``SFUAudioBridge.getOfferingRole`` makes a full-audio publisher
        answer the SFU offer whenever transparent listen-only is enabled.
        New extractor sessions record both flags.  BBB 3.0's stock source
        defaults are ``transparentListenOnly: false`` and
        ``fullAudioOffering: true``, so older sessions use the offerer route.
        """
        # Sessions exported before SBC 0.1.5 lack these flags.  Preserve the
        # stock HTML5 client defaults instead of guessing based on the
        # listener mode selected by the Python client.
        if "transparent_listen_only" not in self._settings and "full_audio_offering" not in self._settings:
            return True
        transparent = bool(self._setting("transparent_listen_only", False))
        return not transparent and bool(self._setting("full_audio_offering", True))

    def _start_request(self, local_offer: str | None) -> dict[str, Any]:
        """Build BBB HTML5's exact ``AudioBroker.sendStartReq`` payload.

        Keeping this small payload constructor separate makes the critical SFU
        contract regression-testable without a network, TURN server, or
        aiortc peer connection.
        """
        self._session_number += 1
        request: dict[str, Any] = {
            "id": "start",
            "type": "audio",
            "role": "sendrecv",
            "clientSessionNumber": self._session_number,
            "transparentListenOnly": bool(self._setting("transparent_listen_only", False)),
        }
        if local_offer:
            request["sdpOffer"] = local_offer
        media_server = self._setting("audio_media_server")
        if media_server:
            request["mediaServer"] = media_server
        return request

    def _session_token(self) -> str:
        page_url = self.session.metadata.get("page_url", "")
        token = parse_qs(urlparse(page_url).query).get("sessionToken", [None])[0]
        return token or (self.session.connection_payload.get("headers") or {}).get("X-Session-Token") or ""

    @staticmethod
    def _ice_expired(credentials: dict[str, Any]) -> bool:
        """Return whether browser-observed ephemeral ICE credentials are stale.

        BBB TURN usernames conventionally start with a Unix expiry timestamp.
        An explicit ``expires_at`` captured by the extractor takes precedence.
        """
        expires_at = credentials.get("expires_at")
        if isinstance(expires_at, (int, float)):
            return float(expires_at) <= time.time() + 15
        if isinstance(expires_at, str):
            with contextlib.suppress(ValueError):
                return datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp() <= time.time() + 15
        for item in credentials.get("turn_servers", credentials.get("turnServers", [])):
            username = str(item.get("username") or "")
            with contextlib.suppress(ValueError):
                return int(username.split(":", 1)[0]) <= time.time() + 15
        return False

    @staticmethod
    def _ice_items(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Normalize BBB's JSON and browser WebRTC ICE-server representations."""
        stun = list(data.get("stun_servers", data.get("stunServers", [])) or [])
        turn = list(data.get("turn_servers", data.get("turnServers", [])) or [])
        return (
            [item for item in stun if isinstance(item, dict) and (item.get("url") or item.get("urls"))],
            [item for item in turn if isinstance(item, dict) and (item.get("url") or item.get("urls"))],
        )

    def _captured_ice_credentials(self) -> dict[str, Any] | None:
        credentials = self._settings.get("ice_servers") or self.session.snapshot.get("ice_servers")
        if not isinstance(credentials, dict):
            return None
        _stun, turns = self._ice_items(credentials)
        return credentials if turns and not self._ice_expired(credentials) else None

    @staticmethod
    async def _add_remote_candidate(peer: Any, payload: Any) -> None:
        """Translate BBB's JSON candidate into aiortc's candidate object.

        Chromium's ``RTCIceCandidate`` is serialized as JSON by BBB's SFU;
        aiortc deliberately does not accept that browser JSON directly.
        """
        if not isinstance(payload, dict):
            await peer.addIceCandidate(payload)
            return
        candidate_sdp = payload.get("candidate")
        if not candidate_sdp:
            await peer.addIceCandidate(None)
            return
        from aiortc.sdp import candidate_from_sdp
        candidate = candidate_from_sdp(str(candidate_sdp).removeprefix("candidate:"))
        candidate.sdpMid = payload.get("sdpMid")
        candidate.sdpMLineIndex = payload.get("sdpMLineIndex")
        await peer.addIceCandidate(candidate)

    def _ice_configuration(self, *, force_relay: bool = False):
        """Fetch (or reuse) the exact short-lived BBB TURN credentials.

        Content media cannot generally use private host candidates.  Treat a
        missing TURN response as a credential/session problem, never as a
        normal ICE route which would only fail later with an opaque timeout.
        """
        from aiortc import RTCConfiguration, RTCIceServer
        data = self._captured_ice_credentials()
        source = "captured"
        try:
            if data is None:
                token = self._session_token()
                configured = self._setting("stun_turn_url", "/bigbluebutton/api/stuns")
                endpoint = urlunparse(urlparse(urljoin(f"{self.session.server}/", configured)))
                separator = "&" if "?" in endpoint else "?"
                endpoint = f"{endpoint}{separator}{urlencode({'sessionToken': token})}"
                headers = {
                    "Accept": "application/json, text/plain, */*",
                    "Origin": self.session.server,
                    "Referer": self.session.metadata.get("page_url", f"{self.session.server}/"),
                    **{key: value for key, value in self.session.headers.items() if value},
                }
                request = Request(endpoint, headers=headers)
                with urlopen(request, timeout=10) as response:
                    data = json.load(response)
                source = "fetched"
                if not isinstance(data, dict):
                    raise MediaConnectionError("BBB returned an invalid ICE credential response")
                if data.get("returncode") == "FAILED":
                    raise MediaConnectionError(data.get("message") or "BBB did not authorize ICE credential retrieval")
                # Cache only in memory.  Loading/using a session must never
                # rewrite its credential file.
                self._settings["ice_servers"] = data
            stun_items, turn_items = self._ice_items(data)
            servers = [RTCIceServer(item.get("url") or item.get("urls")) for item in stun_items]
            turns = [RTCIceServer(item.get("url") or item.get("urls"), item.get("username"), item.get("password")) for item in turn_items]
            # aiortc/aioice supports one TURN endpoint per peer connection. BBB
            # normally sends UDP first and TLS-over-TCP second. Prefer the
            # TLS endpoint: it survives Wi-Fi/VPN UDP filtering and mirrors
            # BBB's own retry-through-relay fallback.
            turns.sort(key=lambda item: 0 if str(item.urls).lower().startswith("turns:") else 1)
            # aiortc does not expose the browser's ``iceTransportPolicy``.
            # Omitting STUN endpoints and removing non-relay SDP candidates
            # below is the equivalent of BBB's retry-through-relay path.
            selected_servers = turns if force_relay else [*servers, *turns]
            debug_trace(
                "media.turn_credentials_loaded",
                stun_servers=len(servers),
                turn_servers=len(turns),
                force_relay=force_relay,
                source=source,
            )
            if not turns:
                raise MediaConnectionError(
                    "BBB did not return TURN credentials; export a fresh .sbc session while the BBB tab is in the meeting"
                )
            if force_relay and not turns:
                raise MediaConnectionError("BBB did not return a TURN server for relay-only media recovery")
            return RTCConfiguration(iceServers=selected_servers)
        except Exception as exc:
            detail = str(exc)
            if "Could not find conference" in detail:
                detail = (
                    "BBB could not map this HTTP session to an active conference; "
                    "open the BBB tab, join an audio mode once, reload it, and export a fresh .sbc session "
                    "so the extractor can include the browser-observed TURN credentials"
                )
            raise MediaConnectionError(f"could not fetch BBB TURN credentials: {detail}") from exc

    @staticmethod
    def _outgoing_sdp(sdp: str, *, force_relay: bool) -> str:
        """Return SDP suitable for BBB's retry-through-relay fallback.

        ``aioice`` still discovers local candidates even when only TURN
        servers are configured.  BBB receives candidates in the SDP when its
        ``signalCandidates`` setting is disabled, therefore strip host/srflx
        candidates on a relay retry.  The SFU can then select only the TURN
        allocation, matching the browser's relay-only ICE retry.
        """
        if not force_relay:
            return sdp
        lines = sdp.replace("\r\n", "\n").split("\n")
        kept = [line for line in lines if not line.startswith("a=candidate:") or " typ relay" in line]
        return "\r\n".join(line for line in kept if line) + "\r\n"

    async def _play_once(self, filename: str, loop: bool, *, gain_db: float = 0.0,
                         fade_in: float = 0.0, force_relay: bool = False) -> None:
        from aiortc.contrib.media import MediaPlayer
        self.player = MediaPlayer(filename, loop=loop)
        if self.player.audio is None: raise MediaConnectionError("the selected file has no audio stream")
        await self._connect_track(
            _GainAudioTrack.create(self.player.audio, gain_db=gain_db, fade_in=fade_in),
            force_relay=force_relay,
        )

    async def _connect_track(self, track, *, force_relay: bool = False) -> None:
        from aiortc import RTCPeerConnection, RTCSessionDescription
        import websockets
        _enable_bbb_legacy_sha1_fingerprint()
        offering = self._full_audio_offering()
        debug_trace("media.sfu_audio_connect_start", url=self._url(), offering=offering, force_relay=force_relay)
        self.pc = RTCPeerConnection(self._ice_configuration(force_relay=force_relay))
        # Keep the browser AudioBroker's negotiation ordering exactly.  An
        # offerer must add the source before it creates its offer; an answerer
        # first receives the SFU offer, then acquires/attaches the microphone
        # before creating its answer.  Adding a track before an SFU offer can
        # create an unmatched m-line in aiortc and causes the remote DTLS/ICE
        # transport to fail even though candidate gathering completed.
        self._audio_sender = self.pc.addTrack(track) if offering else None
        connected = asyncio.Event()

        @self.pc.on("connectionstatechange")
        def on_connection_state_change():
            self.connection_state = self.pc.connectionState
            get_logger().info(
                "BBB custom audio connection state: %s (ICE %s)",
                self.connection_state,
                self.pc.iceConnectionState,
            )
            if self.connection_state in ("connected", "failed", "closed"): connected.set()
            if self._ready and self.connection_state in ("failed", "closed"):
                self._schedule_reconnect()
        local_offer = None
        if offering:
            offer = await self.pc.createOffer()
            await self.pc.setLocalDescription(offer)
            while self.pc.iceGatheringState != "complete":
                await asyncio.sleep(0.05)
            local_offer = self._outgoing_sdp(self.pc.localDescription.sdp, force_relay=force_relay)
        headers = [(key, value) for key, value in self.session.headers.items() if value]
        self.ws = await websockets.connect(self._url(), extra_headers=headers, ping_interval=15, ping_timeout=20)
        # Exact BBB 3.0.32 AudioBroker.sendStartReq message. Full audio can be
        # either the offerer or answerer depending on transparent listen-only.
        message = self._start_request(local_offer)
        await self.ws.send(json.dumps(message))
        while True:
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=20)
            except TimeoutError as exc:
                raise MediaConnectionError("BBB audio SFU did not answer the start request") from exc
            message = json.loads(raw)
            if message.get("id") == "startResponse":
                if message.get("response") != "accepted": raise MediaConnectionError(message.get("reason", "bbb-webrtc-sfu rejected the audio offer"))
                if offering:
                    answer = message.get("sdpAnswer")
                    if not answer:
                        raise MediaConnectionError("BBB audio response did not contain an SDP answer")
                    await self.pc.setRemoteDescription(RTCSessionDescription(answer, "answer"))
                else:
                    remote_offer = message.get("sdpOffer") or message.get("sdpAnswer")
                    if not remote_offer:
                        raise MediaConnectionError("BBB audio response did not contain an SDP offer")
                    await self.pc.setRemoteDescription(RTCSessionDescription(remote_offer, "offer"))
                    self._audio_sender = self.pc.addTrack(track)
                    answer = await self.pc.createAnswer()
                    await self.pc.setLocalDescription(answer)
                    await self.ws.send(json.dumps({
                        "id": "subscriberAnswer", "type": "audio", "role": "sendrecv",
                        "sdpOffer": self._outgoing_sdp(self.pc.localDescription.sdp, force_relay=force_relay),
                    }))
            elif message.get("id") == "iceCandidate" and message.get("candidate"):
                # Needed by BBB installations configured for trickle ICE.
                await self._add_remote_candidate(self.pc, message["candidate"])
            elif message.get("id") == "webRTCAudioSuccess":
                break
            elif message.get("id") in ("webRTCAudioError", "error"):
                raise MediaConnectionError(message.get("reason", "bbb-webrtc-sfu audio error"))
        try:
            await asyncio.wait_for(connected.wait(), timeout=20)
        except TimeoutError as exc:
            raise MediaConnectionError("bbb-webrtc-sfu accepted the audio offer but WebRTC did not connect within 20 seconds") from exc
        if self.connection_state != "connected":
            raise MediaConnectionError(f"bbb-webrtc-sfu WebRTC connection failed (state={self.connection_state}, ice={self.pc.iceConnectionState})")
        self._ready = True
        self._active_force_relay = force_relay
        # Do not install a media receiver while BBB is still negotiating the
        # audio m-line.  The 0.3.2 implementation negotiated first and only
        # then consumed the remote track.  Keeping that ordering is critical
        # for SFUs which reject a receiver that starts pulling RTP before the
        # ``webRTCAudioSuccess`` acknowledgement.
        await self._activate_audio_capture()
        debug_trace("media.sfu_audio_connected", connection_state=self.connection_state, ice_state=self.pc.iceConnectionState)
        self._signal_task = asyncio.create_task(self._listen_for_signals())
        # BBB's own BaseBroker uses JSON ``{id: 'ping'}`` heartbeats rather
        # than only WebSocket control-frame pings. Keep that source-defined
        # signalling lease alive for long-running bots.
        self._heartbeat_task = asyncio.create_task(self._heartbeat())
        self._notify_connection_ready()

    async def _consume_incoming_audio_track(self, track: Any) -> None:
        """Forward a full-audio conference mix to the public capture layer."""
        try:
            while not self._stopping:
                frame = await track.recv()
                callback = self.on_audio_frame
                if callback is not None:
                    callback(frame)
        except Exception as exc:
            if not self._stopping:
                get_logger().debug("BBB full-audio receive track ended: %s", exc)

    async def _activate_audio_capture(self) -> None:
        """Begin consuming already-negotiated remote audio tracks.

        This intentionally runs *after* the source-compatible BBB WebRTC
        handshake completes.  It has no effect until ``on_audio_frame`` is
        configured by :meth:`MediaController.start_audio_capture`.
        """
        if self.on_audio_frame is None or self.pc is None:
            return
        for receiver in self.pc.getReceivers():
            track = getattr(receiver, "track", None)
            track_id = id(track)
            if track is None or getattr(track, "kind", None) != "audio" or track_id in self._receive_track_ids:
                continue
            task = asyncio.create_task(self._consume_incoming_audio_track(track))
            self._receive_tasks.add(task)
            self._receive_track_ids.add(track_id)
            task.add_done_callback(lambda completed, track_id=track_id: (
                self._receive_tasks.discard(completed), self._receive_track_ids.discard(track_id)
            ))

    def _notify_connection_ready(self) -> None:
        """Reapply the BBB input-mode UI state after an SFU reconnection."""
        callback = self.on_connection_ready
        if not callable(callback):
            return
        # Controllers use synchronous GraphQL mutations. Do not block aiortc's
        # event loop while BBB acknowledges that UI/state mutation.
        def restore() -> None:
            try:
                callback()
            except Exception as exc:
                get_logger().warning("Could not restore BBB media input mode after reconnect: %s", exc)
        threading.Thread(target=restore, daemon=True, name="sbc-media-mode-restore").start()

    async def warmup(self) -> None:
        """Connect a muted full-audio session now for instant later playback."""
        if self._ready and self._warmed:
            return
        await self.close()
        self._stopping = False
        self._silent_track = None
        self._warmed = True
        self._active_file = None
        last_error: Exception | None = None
        for attempt, delay in enumerate((0.0, 1.0, 2.0), start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                get_logger().info("Warming BBB microphone connection (attempt %s/3)", attempt)
                self._silent_track = _SilenceAudioTrack.create()
                await self._connect_track(self._silent_track)
                get_logger().info("BBB microphone is warmed and muted")
                return
            except Exception as exc:
                last_error = exc
                await self._dispose()
        self._stopping = True
        self._warmed = False
        raise MediaConnectionError(f"could not warm BBB microphone: {last_error}") from last_error

    async def _listen_for_signals(self) -> None:
        """Keep the SFU signalling socket alive after initial media startup."""
        try:
            assert self.ws is not None
            async for raw in self.ws:
                message = json.loads(raw)
                if message.get("id") == "iceCandidate" and message.get("candidate") and self.pc:
                    await self._add_remote_candidate(self.pc, message["candidate"])
                elif message.get("id") in ("webRTCAudioError", "error"):
                    raise MediaConnectionError(message.get("reason", "bbb-webrtc-sfu audio error"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            close_code = getattr(self.ws, "close_code", None) if self.ws else None
            close_reason = getattr(self.ws, "close_reason", None) if self.ws else None
            get_logger().warning(
                "BBB media signalling ended (code=%s, reason=%s, error=%s)",
                close_code,
                close_reason or "none",
                exc,
            )
        finally:
            if self._ready:
                self._schedule_reconnect()

    async def _send_heartbeat(self) -> None:
        if self.ws is None or self.ws.closed:
            return
        await self.ws.send(json.dumps({"id": "ping"}))
        debug_trace("media.sfu_audio_heartbeat")

    async def _heartbeat(self) -> None:
        """Mirror BBB BaseBroker's application-level audio heartbeat."""
        try:
            # BBB's HTML5 BaseBroker runs every 15 seconds. Send slightly
            # before that deadline so a quiet long-running publisher cannot be
            # dropped when a deployment has a strict signalling timeout.
            while self._ready and not self._stopping:
                await asyncio.sleep(12)
                await self._send_heartbeat()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            debug_trace("media.sfu_audio_heartbeat_failed", error=str(exc))

    def _schedule_reconnect(self) -> None:
        if self._stopping or self._reconnect_task is not None:
            return
        self._reconnect_task = asyncio.create_task(self._reconnect())

    async def _outbound_audio_stats(self) -> dict[str, int]:
        """Return WebRTC's sender counters for a live custom-audio track."""
        if self.pc is None or self._audio_sender is None:
            return {}
        report = await self.pc.getStats()
        for item in report.values():
            if getattr(item, "type", None) != "outbound-rtp":
                continue
            if getattr(item, "kind", None) != "audio":
                continue
            return {
                "packets_sent": int(getattr(item, "packetsSent", 0) or 0),
                "bytes_sent": int(getattr(item, "bytesSent", 0) or 0),
            }
        return {}

    def outbound_audio_stats(self) -> dict[str, int]:
        """Synchronously read live outbound RTP counters for diagnostics."""
        if self.pc is None or self.connection_state != "connected":
            return {}
        try:
            return self.submit(self._outbound_audio_stats()).result(timeout=3)
        except Exception as exc:
            debug_trace("media.outbound_audio_stats_failed", error=str(exc))
            return {}

    async def _reconnect(self) -> None:
        try:
            # BBB's AudioBroker retries connection failures. Retain the same
            # file source and create a fresh PeerConnection/session number.
            for delay in (1.0, 2.0, 5.0):
                get_logger().warning(
                    "BBB media disconnected (state=%s, ICE=%s); retrying in %.0fs",
                    self.connection_state,
                    self.pc.iceConnectionState if self.pc else "closed",
                    delay,
                )
                await self._dispose()
                if self._stopping or not self._active_file:
                    return
                await asyncio.sleep(delay)
                try:
                    await self._play_once(self._active_file, self._active_loop,
                                          gain_db=self._active_gain_db, fade_in=self._active_fade_in,
                                          force_relay=self._active_force_relay)
                    get_logger().info("BBB media reconnected")
                    return
                except Exception:
                    continue
        finally:
            self._reconnect_task = None

    def _swap_warmed_track(self, player: Any, filename: str, loop: bool, *, gain_db: float = 0.0,
                           fade_in: float = 0.0) -> None:
        """Replace the warm-up silence source without terminating sender RTP."""
        if self._audio_sender is None or player.audio is None:
            raise MediaConnectionError("BBB audio sender or file track is unavailable")
        self._audio_sender.replaceTrack(_GainAudioTrack.create(player.audio, gain_db=gain_db, fade_in=fade_in))
        # Do not call ``stop()`` on the previously attached silence track
        # here. RTCRtpSender can still be awaiting that track's ``recv``;
        # stopping it raises MediaStreamError, terminates the sender's RTP
        # task, and silently leaves the negotiated connection alive with no
        # outgoing audio. Replacing the track is sufficient; the silent source
        # owns no background resources and is released on close.
        self.player, self._silent_track = player, None
        self._warmed = False
        self._active_file, self._active_loop = filename, loop
        self._active_gain_db, self._active_fade_in = gain_db, fade_in

    async def play(self, filename: str, loop: bool, *, gain_db: float = 0.0,
                   fade_in: float = 0.0) -> None:
        """Publish audio and retry transient SFU/ICE failures automatically."""
        if self._ready and self._warmed and self.pc and self._audio_sender:
            from aiortc.contrib.media import MediaPlayer
            player = MediaPlayer(filename, loop=loop)
            if player.audio is None:
                raise MediaConnectionError("the selected file has no audio stream")
            self._swap_warmed_track(player, filename, loop, gain_db=gain_db, fade_in=fade_in)
            # Give aiortc one paced audio frame before the BBB mute command is
            # lifted. This mirrors browser AudioBroker's input-stream swap and
            # avoids deployments dropping the first source frame while the
            # muted state is being updated.
            await asyncio.sleep(0.5)
            get_logger().info("Publishing prepared BBB custom audio source")
            return
        await self.close()
        self._stopping = False
        self._active_file, self._active_loop = filename, loop
        self._active_gain_db, self._active_fade_in = gain_db, fade_in
        last_error: Exception | None = None
        delays = (1.0, 2.0, 4.0, 6.0)
        for attempt in range(5):
            # The first attempt follows the deployment's normal ICE policy.
            # On a failure retry through TURN, as BBB's AudioBroker does.
            force_relay = attempt >= 1
            try:
                route = " via TURN relay" if force_relay else ""
                get_logger().info("Starting BBB custom audio (attempt %s/5)%s", attempt + 1, route)
                await self._play_once(filename, loop, gain_db=gain_db, fade_in=fade_in, force_relay=force_relay)
                get_logger().info("BBB custom audio is publishing")
                return
            except Exception as exc:
                last_error = exc
                get_logger().warning("BBB custom audio attempt %s failed: %s", attempt + 1, exc)
                await self._dispose()
                if attempt < len(delays): await asyncio.sleep(delays[attempt])
        self._stopping = True
        raise MediaConnectionError(f"could not establish BBB audio after 5 attempts: {last_error}") from last_error
    async def mute(self, muted: bool) -> None:
        # BBB's userSetMuted mutation controls the participant mute state. The
        # source track remains alive so unmute does not require renegotiation.
        return

    async def reconnect_now(self, reason: str = "media health recovery") -> None:
        """Replace a stale sender with a fresh BBB SFU/WebRTC session."""
        if not self._active_file or not self._active_loop:
            raise MediaStalledError("audio is not a looping active source", recoverable=False)
        get_logger().warning("Restarting BBB audio sender: %s", reason)
        await self._dispose()
        self._stopping = False
        await self._play_once(self._active_file, self._active_loop,
                              gain_db=self._active_gain_db, fade_in=self._active_fade_in,
                              force_relay=self._active_force_relay)
        get_logger().info("BBB audio sender recovered")
    async def _dispose(self) -> None:
        tasks = tuple(self._receive_tasks)
        self._receive_tasks.clear()
        self._receive_track_ids.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._ready = False
        task = self._signal_task
        self._signal_task = None
        if task and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception): await task
        heartbeat = self._heartbeat_task
        self._heartbeat_task = None
        if heartbeat and heartbeat is not asyncio.current_task():
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception): await heartbeat
        if self.ws: await self.ws.close(); self.ws = None
        if self.pc: await self.pc.close(); self.pc = None
        self._audio_sender = None
        if self._silent_track is not None:
            self._silent_track.stop()
            self._silent_track = None
        if self.player: self.player = None

    async def close(self) -> None:
        self._stopping = True
        self._active_file = None
        self._warmed = False
        task = self._reconnect_task
        self._reconnect_task = None
        if task and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception): await task
        await self._dispose()


class _SFUListener(_SFUAudioPublisher):
    """BBB AudioBroker-compatible recv-only audio session.

    A ``userSetListenOnlyInput`` mutation is only a UI preference. BBB shows
    the listener icon after this actual SFU ``recv`` session is connected.
    """
    def __init__(self, session: Any):
        super().__init__(session)
        self._listener_active = False
        # Assigned by ``MediaController``.  The standard BBB listener stream
        # is a conference mix; deployments with participant-labelled tracks
        # can replace this callback with their own metadata resolver.
        self.on_audio_frame: Any | None = None
        self._receive_tasks: set[asyncio.Task[Any]] = set()
        self._receive_track_ids: set[int] = set()

    async def join(self) -> None:
        await self.close()
        self._stopping = False
        self._listener_active = True
        try:
            await self._connect_listener_with_retries()
            get_logger().info("BBB listener audio is connected")
        except Exception:
            self._listener_active = False
            self._stopping = True
            await self._dispose()
            raise

    async def _connect_listener_with_retries(self) -> None:
        """Retry listener media with BBB's relay-only fallback route."""
        last_error: Exception | None = None
        for attempt in range(3):
            force_relay = attempt > 0
            try:
                route = " via TURN relay" if force_relay else ""
                get_logger().info("Starting BBB listener SFU session (attempt %s/3)%s", attempt + 1, route)
                await asyncio.wait_for(self._connect_listener(force_relay=force_relay), timeout=30)
                return
            except Exception as exc:
                last_error = exc
                get_logger().warning("BBB listener attempt %s failed: %s", attempt + 1, exc)
                await self._dispose()
                if attempt < 2:
                    await asyncio.sleep((1.0, 3.0)[attempt])
        raise MediaConnectionError(f"could not establish BBB listener audio: {last_error}") from last_error

    async def _connect_listener(self, *, force_relay: bool = False) -> None:
        from aiortc import RTCPeerConnection, RTCSessionDescription
        import websockets
        _enable_bbb_legacy_sha1_fingerprint()

        self.pc = RTCPeerConnection(self._ice_configuration(force_relay=force_relay))
        connected = asyncio.Event()

        @self.pc.on("connectionstatechange")
        def on_connection_state_change():
            self.connection_state = self.pc.connectionState
            if self.connection_state in ("connected", "failed", "closed"):
                connected.set()
            if self._ready and self.connection_state in ("failed", "closed"):
                self._schedule_reconnect()

        # BBB 3.0.32 defaults ``media.listenOnlyOffering`` to false: the SFU
        # sends an offer and the client replies with ``subscriberAnswer``.
        # Some installations flip it to true, so support both source modes.
        offering = bool(self._setting("listen_only_offering", False))
        local_offer = None
        if offering:
            self.pc.addTransceiver("audio", direction="recvonly")
            offer = await self.pc.createOffer()
            await self.pc.setLocalDescription(offer)
            while self.pc.iceGatheringState != "complete":
                await asyncio.sleep(0.05)
            local_offer = self.pc.localDescription.sdp
        get_logger().info("Negotiating BBB listener SFU session (offering=%s)", offering)
        headers = [(key, value) for key, value in self.session.headers.items() if value]
        self.ws = await websockets.connect(self._url(), extra_headers=headers, ping_interval=15, ping_timeout=20, close_timeout=2)
        self._session_number += 1
        message = {
            "id": "start", "type": "audio", "role": "recv",
            "clientSessionNumber": self._session_number,
            "transparentListenOnly": False,
        }
        if local_offer:
            message["sdpOffer"] = self._outgoing_sdp(local_offer, force_relay=force_relay)
        media_server = self._setting("listen_only_media_server")
        if media_server:
            message["mediaServer"] = media_server
        await self.ws.send(json.dumps(message))
        get_logger().info("Sent BBB listener start request")
        while True:
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=20)
            except TimeoutError as exc:
                raise MediaConnectionError("BBB listener SFU did not answer the start request") from exc
            message = json.loads(raw)
            get_logger().info("Received BBB listener signal: %s", message.get("id", "unknown"))
            if message.get("id") == "startResponse":
                if message.get("response") != "accepted":
                    raise MediaConnectionError(message.get("reason", "bbb-webrtc-sfu rejected the listener offer"))
                if offering:
                    await self.pc.setRemoteDescription(RTCSessionDescription(message["sdpAnswer"], "answer"))
                else:
                    sdp_offer = message.get("sdpOffer") or message.get("sdpAnswer")
                    if not sdp_offer:
                        raise MediaConnectionError("BBB listener response did not contain an SDP offer")
                    await self.pc.setRemoteDescription(RTCSessionDescription(sdp_offer, "offer"))
                    answer = await self.pc.createAnswer()
                    await self.pc.setLocalDescription(answer)
                    await self.ws.send(json.dumps({
                        "id": "subscriberAnswer", "type": "audio", "role": "recv",
                        "sdpOffer": self._outgoing_sdp(self.pc.localDescription.sdp, force_relay=force_relay),
                    }))
            elif message.get("id") == "iceCandidate" and message.get("candidate"):
                await self._add_remote_candidate(self.pc, message["candidate"])
            elif message.get("id") == "webRTCAudioSuccess":
                break
            elif message.get("id") in ("webRTCAudioError", "error"):
                raise MediaConnectionError(message.get("reason", "bbb-webrtc-sfu listener error"))
        try:
            await asyncio.wait_for(connected.wait(), timeout=20)
        except TimeoutError as exc:
            raise MediaConnectionError("BBB listener WebRTC connection did not establish within 20 seconds") from exc
        if self.connection_state != "connected":
            raise MediaConnectionError(f"BBB listener WebRTC connection failed (state={self.connection_state}, ice={self.pc.iceConnectionState})")
        self._ready = True
        self._active_force_relay = force_relay
        self._signal_task = asyncio.create_task(self._listen_for_signals())
        self._notify_connection_ready()
        await self._activate_audio_capture()

    async def _consume_audio_track(self, track: Any) -> None:
        """Forward the decoded listener track without blocking WebRTC I/O."""
        try:
            while self._listener_active and not self._stopping:
                frame = await track.recv()
                callback = self.on_audio_frame
                if callback is not None:
                    callback(frame)
        except Exception as exc:
            # Remote tracks normally end during reconnect/close.  Only log an
            # unexpected receiver failure while the session is still live.
            if self._listener_active and not self._stopping:
                get_logger().debug("BBB listener receive track ended: %s", exc)

    async def _activate_audio_capture(self) -> None:
        """Attach capture after BBB has accepted the listener session.

        ``track.recv()`` must not be called during the listener's SDP/ICE
        exchange.  Some BBB SFUs immediately tear down the peer when a
        receive loop is started before their ``webRTCAudioSuccess`` signal.
        """
        if self.on_audio_frame is None or self.pc is None:
            return
        for receiver in self.pc.getReceivers():
            track = getattr(receiver, "track", None)
            track_id = id(track)
            if track is None or getattr(track, "kind", None) != "audio" or track_id in self._receive_track_ids:
                continue
            task = asyncio.create_task(self._consume_audio_track(track))
            self._receive_tasks.add(task)
            self._receive_track_ids.add(track_id)
            task.add_done_callback(lambda completed, track_id=track_id: (
                self._receive_tasks.discard(completed), self._receive_track_ids.discard(track_id)
            ))

    async def _dispose(self) -> None:
        tasks = tuple(self._receive_tasks)
        self._receive_tasks.clear()
        self._receive_track_ids.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await super()._dispose()

    async def _reconnect(self) -> None:
        try:
            for delay in (1.0, 2.0, 5.0):
                get_logger().warning("BBB listener disconnected; retrying in %.0fs", delay)
                await self._dispose()
                if self._stopping or not self._listener_active:
                    return
                await asyncio.sleep(delay)
                try:
                    await self._connect_listener(force_relay=self._active_force_relay)
                    get_logger().info("BBB listener reconnected")
                    return
                except Exception:
                    continue
        finally:
            self._reconnect_task = None

    async def close(self) -> None:
        self._listener_active = False
        await super().close()


class _DynamicVisualTrack:
    """Turn a mutable :class:`VisualSurface` into a paced aiortc video track."""

    @staticmethod
    def create(surface: VisualSurface):
        from aiortc import MediaStreamTrack
        from aiortc.mediastreams import MediaStreamError
        from av import VideoFrame

        class DynamicVisualTrack(MediaStreamTrack):
            kind = "video"

            def __init__(self) -> None:
                super().__init__()
                self._timestamp: int | None = None
                self._started: float | None = None

            async def recv(self):
                if self.readyState != "live":
                    raise MediaStreamError
                clock_rate = 90_000
                step = max(1, int(clock_rate / surface.frame_rate))
                loop = asyncio.get_running_loop()
                if self._timestamp is None:
                    self._timestamp, self._started = 0, loop.time()
                else:
                    self._timestamp += step
                    await asyncio.sleep(max(0, self._started + self._timestamp / clock_rate - loop.time()))
                # The surface lock is released before converting the image, so
                # command handlers can update text/graphics while aiortc sends
                # the previous frame.
                image = surface.render()
                frame = VideoFrame.from_ndarray(image.toarray() if hasattr(image, "toarray") else __import__("numpy").asarray(image), format="rgba")
                frame.pts = self._timestamp
                frame.time_base = Fraction(1, clock_rate)
                return frame

        return DynamicVisualTrack()


class _SFUScreensharePublisher(_SFUAudioPublisher):
    """Source-matched ``screenshare`` presenter for BBB's WebRTC SFU.

    BBB's HTML5 ``ScreenshareBroker`` sends a different contract from webcam
    publishing: ``type=screenshare``, ``role=send``, and the meeting's voice
    bridge are required.  This publisher implements that contract while its
    source track reads a mutable :class:`VisualSurface`.
    """

    def __init__(self, client: Any, source: VisualSurface | str | Path, context: dict[str, Any], *, loop: bool = True) -> None:
        super().__init__(client.session)
        self.client = client
        self.surface = source if isinstance(source, VisualSurface) else None
        self.source_path = None if isinstance(source, VisualSurface) else Path(source)
        self.source_loop = loop
        self.context = context
        self._screen_track: Any | None = None
        self._sharing_active = False

    def _start_request(self, sdp_offer: str) -> dict[str, Any]:
        message: dict[str, Any] = {
            "id": "start",
            "type": "screenshare",
            "role": "send",
            "internalMeetingId": self.context["meeting_id"],
            "voiceBridge": self.context["voice_bridge"],
            "userName": self.context["user_name"],
            "callerName": self.context["user_id"],
            "sdpOffer": sdp_offer,
            "hasAudio": False,
            "contentType": "screenshare",
            "bitrate": self.context["bitrate"],
        }
        media_server = self.context.get("media_server")
        if media_server:
            message["mediaServer"] = media_server
        return message

    async def start(self) -> None:
        """Open a source-defined BBB screenshare presenter connection."""
        await self.close()
        self._stopping = False
        self._sharing_active = True
        last_error: Exception | None = None
        for attempt in range(3):
            force_relay = attempt > 0
            try:
                route = " via TURN relay" if force_relay else ""
                get_logger().info("Starting BBB visual screenshare (attempt %s/3)%s", attempt + 1, route)
                await self._connect_screenshare(force_relay=force_relay)
                get_logger().info("BBB visual screenshare is publishing")
                return
            except Exception as exc:
                last_error = exc
                get_logger().warning("BBB visual screenshare attempt %s failed: %s", attempt + 1, exc)
                await self._dispose()
                if attempt < 2:
                    await asyncio.sleep((1.0, 3.0)[attempt])
        self._stopping = True
        self._sharing_active = False
        raise MediaConnectionError(f"could not establish BBB visual screenshare: {last_error}") from last_error

    async def _connect_screenshare(self, *, force_relay: bool) -> None:
        from aiortc import RTCPeerConnection, RTCSessionDescription
        import websockets

        _enable_bbb_legacy_sha1_fingerprint()
        self.pc = RTCPeerConnection(self._ice_configuration(force_relay=force_relay))
        if self.surface is not None:
            if self._screen_track is None or self._screen_track.readyState != "live":
                self._screen_track = _DynamicVisualTrack.create(self.surface)
        else:
            from aiortc.contrib.media import MediaPlayer

            # File tracks are tied to their decoder/player. Recreate both for
            # every fresh peer connection so an SFU reconnect starts a valid
            # new source rather than reusing a stopped RTP track.
            self.player = MediaPlayer(str(self.source_path), loop=self.source_loop)
            self._screen_track = self.player.video
            if self._screen_track is None:
                raise MediaConnectionError("the selected screenshare media has no video stream")
        self.pc.addTrack(self._screen_track)
        connected = asyncio.Event()

        @self.pc.on("connectionstatechange")
        def on_connection_state_change() -> None:
            self.connection_state = self.pc.connectionState
            get_logger().info(
                "BBB visual screenshare connection state: %s (ICE %s)",
                self.connection_state,
                self.pc.iceConnectionState,
            )
            if self.connection_state in ("connected", "failed", "closed"):
                connected.set()
            if self._ready and self.connection_state in ("failed", "closed"):
                self._schedule_reconnect()

        # Match ScreenshareBroker: establish its signalling identity before
        # producing the local browser-equivalent offer.
        headers = [(key, value) for key, value in self.session.headers.items() if value]
        self.ws = await websockets.connect(self._url(), extra_headers=headers, ping_interval=15, ping_timeout=20)
        offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(offer)
        while self.pc.iceGatheringState != "complete":
            await asyncio.sleep(0.05)
        await self.ws.send(json.dumps(self._start_request(
            self._outgoing_sdp(self.pc.localDescription.sdp, force_relay=force_relay),
        )))
        # ``playStart`` is optional in BBB (and disabled in several stock
        # deployments), so readiness is the accepted SDP answer + connected
        # PeerConnection, not that optional media-flow notification.
        answer_received = False
        pending_candidates: list[dict[str, Any]] = []
        while not answer_received:
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=25)
            except TimeoutError as exc:
                raise MediaConnectionError("BBB screenshare SFU did not answer the start request") from exc
            message = json.loads(raw)
            signal = message.get("id")
            if signal == "startResponse":
                if message.get("response") != "accepted":
                    raise MediaConnectionError(message.get("reason", "bbb-webrtc-sfu rejected the screenshare offer"))
                answer = message.get("sdpAnswer")
                if not answer:
                    raise MediaConnectionError("BBB screenshare response did not contain an SDP answer")
                await self.pc.setRemoteDescription(RTCSessionDescription(answer, "answer"))
                answer_received = True
                for candidate in pending_candidates:
                    await self._add_remote_candidate(self.pc, candidate)
                pending_candidates.clear()
            elif signal == "iceCandidate" and message.get("candidate"):
                if answer_received:
                    await self._add_remote_candidate(self.pc, message["candidate"])
                else:
                    pending_candidates.append(message["candidate"])
            elif signal == "playStart":
                get_logger().debug("BBB visual screenshare media flow started")
            elif signal in ("error", "stopSharing"):
                raise MediaConnectionError(message.get("reason", "bbb-webrtc-sfu screenshare stopped"))
        try:
            await asyncio.wait_for(connected.wait(), timeout=20)
        except TimeoutError as exc:
            raise MediaConnectionError("BBB screenshare was accepted but WebRTC did not connect within 20 seconds") from exc
        if self.connection_state != "connected":
            raise MediaConnectionError(
                f"bbb-webrtc-sfu screenshare connection failed "
                f"(state={self.connection_state}, ice={self.pc.iceConnectionState})"
            )
        self._ready = True
        self._active_force_relay = force_relay
        self._signal_task = asyncio.create_task(self._listen_for_screenshare_signals())
        self._heartbeat_task = asyncio.create_task(self._heartbeat())

    async def _listen_for_screenshare_signals(self) -> None:
        try:
            assert self.ws is not None
            async for raw in self.ws:
                message = json.loads(raw)
                signal = message.get("id")
                if signal == "iceCandidate" and message.get("candidate") and self.pc:
                    await self._add_remote_candidate(self.pc, message["candidate"])
                elif signal == "stopSharing":
                    get_logger().info("BBB stopped the visual screenshare")
                    self._sharing_active = False
                    self._stopping = True
                    return
                elif signal == "error":
                    raise MediaConnectionError(message.get("reason", "bbb-webrtc-sfu screenshare error"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            get_logger().warning("BBB visual screenshare signalling ended: %s", exc)
        finally:
            if self._ready and self._sharing_active:
                self._schedule_reconnect()

    async def _reconnect(self) -> None:
        try:
            for delay in (1.0, 2.0, 5.0):
                get_logger().warning("BBB visual screenshare disconnected; retrying in %.0fs", delay)
                await self._dispose()
                if self._stopping or not self._sharing_active:
                    return
                await asyncio.sleep(delay)
                try:
                    await self._connect_screenshare(force_relay=self._active_force_relay)
                    get_logger().info("BBB visual screenshare reconnected")
                    return
                except Exception:
                    continue
        finally:
            self._reconnect_task = None

    async def close(self) -> None:
        self._sharing_active = False
        await super().close()
        if self._screen_track is not None:
            self._screen_track.stop()
            self._screen_track = None


class _SFUVideoPublisher(_SFUAudioPublisher):
    """BBB 3.0.32 VideoProvider publisher for ``bbb-webrtc-sfu`` cameras."""
    def __init__(self, client: Any):
        super().__init__(client.session)
        self.client = client
        self.camera_id: str | None = None

    async def _play_once(self, filename: str, loop: bool, *, gain_db: float = 0.0,
                         fade_in: float = 0.0, force_relay: bool = False) -> None:
        from aiortc import RTCPeerConnection, RTCSessionDescription
        from aiortc.contrib.media import MediaPlayer
        import websockets
        _enable_bbb_legacy_sha1_fingerprint()
        self.player = MediaPlayer(filename, loop=loop)
        if self.player.video is None: raise MediaConnectionError("the selected file has no video stream")
        # VideoService.buildStreamName(): <userId>_<clientSessionUUID>_<deviceId>
        client_uuid = (self.session.connection_payload.get("headers") or {}).get("X-ClientSessionUUID", "sbc")
        self.camera_id = f"{self.session.user_id}_{client_uuid}_sbc-custom-camera"
        self.pc = RTCPeerConnection(self._ice_configuration(force_relay=force_relay)); self.pc.addTrack(self.player.video)
        connected = asyncio.Event()
        @self.pc.on("connectionstatechange")
        def on_connection_state_change():
            self.connection_state = self.pc.connectionState
            if self.connection_state in ("connected", "failed", "closed"):
                connected.set()
        offer = await self.pc.createOffer(); await self.pc.setLocalDescription(offer)
        while self.pc.iceGatheringState != "complete": await asyncio.sleep(0.05)
        headers = [(key, value) for key, value in self.session.headers.items() if value]
        self.ws = await websockets.connect(self._url(), extra_headers=headers, ping_interval=15, ping_timeout=20)
        # Exact VideoProvider start fields: type, cameraId, role, sdpOffer,
        # bitrate, record and optional mediaServer.
        message = {"id": "start", "type": "video", "cameraId": self.camera_id, "role": "share", "sdpOffer": self._outgoing_sdp(self.pc.localDescription.sdp, force_relay=force_relay), "bitrate": 200, "record": True}
        media_server = self._setting("camera_media_server")
        if media_server: message["mediaServer"] = media_server
        await self.ws.send(json.dumps(message))
        async for raw in self.ws:
            message = json.loads(raw)
            if message.get("id") == "startResponse":
                if message.get("response") not in (None, "accepted"):
                    raise MediaConnectionError(message.get("reason", "bbb-webrtc-sfu rejected the camera offer"))
                await self.pc.setRemoteDescription(RTCSessionDescription(message["sdpAnswer"], "answer"))
            elif message.get("id") == "iceCandidate" and message.get("candidate"):
                await self.pc.addIceCandidate(message["candidate"])
            elif message.get("id") == "playStart" and message.get("cameraId") == self.camera_id:
                # This is the exact ordering used by VideoProvider/container:
                # only publish GraphQL camera state after the SFU reports that
                # the media flow has started.
                try:
                    await asyncio.wait_for(connected.wait(), timeout=20)
                except TimeoutError as exc:
                    raise MediaConnectionError("bbb-webrtc-sfu did not establish the camera connection") from exc
                if self.connection_state != "connected":
                    raise MediaConnectionError(f"bbb-webrtc-sfu camera connection failed (state={self.connection_state}, ice={self.pc.iceConnectionState})")
                self.client.graphql.mutation("mutation CameraBroadcastStart($cameraId:String!,$contentType:String!){cameraBroadcastStart(stream:$cameraId,contentType:$contentType)}", {"cameraId": self.camera_id, "contentType": "camera"})
                self._ready = True
                self._signal_task = asyncio.create_task(self._listen_for_signals())
                return
            if message.get("id") == "error": raise MediaConnectionError(message.get("reason", "bbb-webrtc-sfu video error"))
        raise MediaConnectionError("bbb-webrtc-sfu closed before camera media started")

    async def play(self, filename: str, loop: bool) -> None:
        """Publish a camera source with the same automatic SFU recovery as audio."""
        await super().play(filename, loop)

    async def set_broadcast(self, enabled: bool) -> None:
        if not self.camera_id: return
        if enabled:
            query = "mutation CameraBroadcastStart($cameraId:String!,$contentType:String!){cameraBroadcastStart(stream:$cameraId,contentType:$contentType)}"; variables = {"cameraId": self.camera_id, "contentType": "camera"}
        else:
            query = "mutation CameraBroadcastStop($cameraId:String!){cameraBroadcastStop(stream:$cameraId)}"; variables = {"cameraId": self.camera_id}
        self.client.graphql.mutation(query, variables)

    async def _dispose(self) -> None:
        if self.camera_id:
            with contextlib.suppress(Exception): await self.set_broadcast(False)
            if self.ws:
                with contextlib.suppress(Exception):
                    await self.ws.send(json.dumps({"id": "stop", "type": "video", "cameraId": self.camera_id, "role": "share"}))
        await super()._dispose(); self.camera_id = None


class _LiveKitPublisher:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True, name="sbc-livekit")
        self.thread.start()
        self.room = None
        self.sources: dict[str, Any] = {}
        self.tracks: dict[str, Any] = {}
        self.publications: dict[str, Any] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.receive_tasks: set[asyncio.Task[Any]] = set()
        self.on_audio_frame: Any | None = None

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coroutine) -> concurrent.futures.Future:
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    async def _connect(self, url: str, token: str) -> None:
        from livekit import rtc
        if self.room is not None:
            return
        self.room = rtc.Room()
        @self.room.on("track_subscribed")
        def on_track_subscribed(track: Any, _publication: Any, participant: Any) -> None:
            if int(getattr(track, "kind", 0)) != int(rtc.TrackKind.Value("KIND_AUDIO")):
                return
            task = asyncio.create_task(self._consume_remote_audio(track, participant))
            self.receive_tasks.add(task)
            task.add_done_callback(self.receive_tasks.discard)
        await self.room.connect(url, token, rtc.RoomOptions(auto_subscribe=True))

    async def receive(self, url: str, token: str) -> None:
        """Connect only for remote participant audio capture."""
        await self._connect(url, token)

    async def _consume_remote_audio(self, track: Any, participant: Any) -> None:
        """Forward individual LiveKit participant audio tracks to SBC."""
        from livekit import rtc
        stream = rtc.AudioStream(track, sample_rate=48_000, num_channels=1)
        try:
            async for event in stream:
                frame = getattr(event, "frame", event)
                callback = self.on_audio_frame
                if callback is not None:
                    callback(
                        bytes(frame.data),
                        sample_rate=int(frame.sample_rate),
                        channels=int(frame.num_channels),
                        user_id=getattr(participant, "identity", None),
                        user_name=getattr(participant, "name", None) or getattr(participant, "identity", None),
                    )
        except Exception as exc:
            get_logger().debug("LiveKit incoming audio track ended: %s", exc)
        finally:
            with contextlib.suppress(Exception):
                await stream.aclose()

    async def _clear(self, kind: str) -> None:
        task = self.tasks.pop(kind, None)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError): await task
        track = self.tracks.pop(kind, None)
        publication = self.publications.pop(kind, None)
        self.sources.pop(kind, None)
        if publication is not None and self.room is not None:
            with contextlib.suppress(Exception): await self.room.local_participant.unpublish_track(publication.sid)
        if track is not None and hasattr(track, "close"):
            with contextlib.suppress(Exception): track.close()

    async def play(self, kind: str, filename: str, loop: bool, frame_rate: int, url: str, token: str) -> None:
        from livekit import rtc
        import av
        await self._connect(url, token)
        await self._clear(kind)
        if kind == "audio":
            source = rtc.AudioSource(48000, 1)
            track = rtc.LocalAudioTrack.create_audio_track("sbc-custom-audio", source)
            options = rtc.TrackPublishOptions(); options.source = rtc.TrackSource.SOURCE_MICROPHONE
            publication = await self.room.local_participant.publish_track(track, options)
            task = asyncio.create_task(self._pump_audio(Path(filename), source, loop, av))
        else:
            # Dimensions are updated by the first decoded frame. BBB's LiveKit
            # bridge accepts the source dimensions declared at track creation.
            source = rtc.VideoSource(1280, 720)
            track = rtc.LocalVideoTrack.create_video_track("sbc-custom-camera", source)
            options = rtc.TrackPublishOptions(); options.source = rtc.TrackSource.SOURCE_CAMERA
            publication = await self.room.local_participant.publish_track(track, options)
            task = asyncio.create_task(self._pump_video(Path(filename), source, loop, frame_rate, av, rtc))
        self.sources[kind], self.tracks[kind], self.publications[kind], self.tasks[kind] = source, track, publication, task

    async def share_surface(self, surface: VisualSurface, url: str, token: str) -> None:
        """Publish a mutable SBC visual surface as a LiveKit screenshare."""
        from livekit import rtc

        await self._connect(url, token)
        kind = "screenshare"
        await self._clear(kind)
        source = rtc.VideoSource(surface.width, surface.height)
        track = rtc.LocalVideoTrack.create_video_track("sbc-visual-screenshare", source)
        options = rtc.TrackPublishOptions()
        options.source = rtc.TrackSource.SOURCE_SCREENSHARE
        publication = await self.room.local_participant.publish_track(track, options)
        task = asyncio.create_task(self._pump_surface(surface, source, rtc))
        self.sources[kind], self.tracks[kind], self.publications[kind], self.tasks[kind] = source, track, publication, task

    async def share_file(self, filename: str, loop: bool, frame_rate: int, url: str, token: str) -> None:
        """Publish a local video file using LiveKit's screenshare track source."""
        from livekit import rtc
        import av

        await self._connect(url, token)
        kind = "screenshare"
        await self._clear(kind)
        source = rtc.VideoSource(1280, 720)
        track = rtc.LocalVideoTrack.create_video_track("sbc-media-screenshare", source)
        options = rtc.TrackPublishOptions()
        options.source = rtc.TrackSource.SOURCE_SCREENSHARE
        publication = await self.room.local_participant.publish_track(track, options)
        task = asyncio.create_task(self._pump_video(Path(filename), source, loop, frame_rate, av, rtc))
        self.sources[kind], self.tracks[kind], self.publications[kind], self.tasks[kind] = source, track, publication, task

    async def _pump_surface(self, surface: VisualSurface, source: Any, rtc: Any) -> None:
        import numpy as np

        while True:
            image = surface.render()
            rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
            source.capture_frame(rtc.VideoFrame(surface.width, surface.height, rtc.VideoBufferType.RGBA, rgba.tobytes()))
            await asyncio.sleep(1 / surface.frame_rate)

    async def _pump_audio(self, filename: Path, source: Any, loop: bool, av: Any) -> None:
        from livekit import rtc
        while True:
            container = av.open(str(filename))
            resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=48000)
            try:
                for frame in container.decode(audio=0):
                    for output in resampler.resample(frame):
                        samples = output.to_ndarray()
                        frame_out = rtc.AudioFrame(samples.tobytes(), 48000, 1, output.samples)
                        source.capture_frame(frame_out)
                        await asyncio.sleep(output.samples / 48000)
            finally:
                container.close()
            if not loop: return

    async def _pump_video(self, filename: Path, source: Any, loop: bool, frame_rate: int, av: Any, rtc: Any) -> None:
        interval = 1 / max(frame_rate, 1)
        while True:
            container = av.open(str(filename))
            try:
                for frame in container.decode(video=0):
                    rgba = frame.to_ndarray(format="rgba")
                    source.capture_frame(rtc.VideoFrame(frame.width, frame.height, rtc.VideoBufferType.RGBA, rgba.tobytes()))
                    await asyncio.sleep(interval)
            finally:
                container.close()
            if not loop: return

    async def mute(self, kind: str, muted: bool) -> None:
        track = self.tracks.get(kind)
        if track is None: return
        (track.mute if muted else track.unmute)()

    async def close(self) -> None:
        tasks = tuple(self.receive_tasks)
        self.receive_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for kind in tuple(self.tracks): await self._clear(kind)
        if self.room is not None:
            with contextlib.suppress(Exception): await self.room.disconnect()
            self.room = None


class _Source:
    def __init__(self, media: "MediaController", kind: str): self.media, self.kind = media, kind
    def play(self, file: str | Path, *, loop: bool = True, frame_rate: int = 30,
             gain_db: float = 0.0, fade_in: float = 0.0) -> None:
        """Publish a local source, optionally changing level and fading it in.

        ``gain_db`` is applied before frames enter WebRTC. ``fade_in`` is in
        seconds and avoids an abrupt clip start. It is supported for audio.
        """
        path = Path(file).expanduser()
        if not path.is_file(): raise FileNotFoundError(path)
        if fade_in < 0: raise ValueError("fade_in must be zero or positive")
        self.media._play(self.kind, path, loop, frame_rate, gain_db=gain_db, fade_in=fade_in)
    def prepare(self, file: str | Path) -> Path:
        """Decode-check a file before it is needed by an event handler."""
        path = Path(file).expanduser()
        if not path.is_file(): raise FileNotFoundError(path)
        return self.media._prepare(self.kind, path)
    def warmup(self) -> None:
        """Pre-connect a muted BBB microphone for low-latency ``play()``."""
        if self.kind != "audio": raise MediaConnectionError("only audio can be warmed")
        self.media._warm_audio()
    def health(self, *, stall_after: float = 20.0, recover: bool = True) -> MediaHealth:
        """Inspect outbound RTP and optionally repair a stalled looping source."""
        if self.kind != "audio":
            raise MediaConnectionError("health checks are currently available for audio only")
        return self.media.audio_health(stall_after=stall_after, recover=recover)
    def enqueue(self, file: str | Path, *, gain_db: float = 0.0, fade_in: float = 0.0,
                duration: float | None = None) -> int:
        """Append one non-looping clip to the serialized audio playlist."""
        if self.kind != "audio": raise MediaConnectionError("only audio supports a playlist")
        return self.media.playlist.enqueue(file, gain_db=gain_db, fade_in=fade_in, duration=duration)
    def schedule(self, file: str | Path, *, delay: float = 0.0, loop: bool = False,
                 gain_db: float = 0.0, fade_in: float = 0.0) -> threading.Timer:
        """Schedule a source after ``delay`` seconds and return its cancelable timer."""
        path = Path(file).expanduser()
        if not path.is_file(): raise FileNotFoundError(path)
        if delay < 0: raise ValueError("delay must be zero or positive")
        timer = threading.Timer(delay, self.play, kwargs={"file": path, "loop": loop,
                                                         "gain_db": gain_db, "fade_in": fade_in})
        timer.daemon = True; timer.start()
        self.media._timers.append(timer)
        return timer
    def mute(self) -> None: self.media._mute(self.kind, True)
    def unmute(self) -> None: self.media._mute(self.kind, False)
    def stop(self) -> None: self.media._stop(self.kind)


@dataclass(frozen=True, slots=True)
class AudioQueueItem:
    """One serial playlist item; ``duration`` overrides decoded duration."""
    file: Path
    gain_db: float = 0.0
    fade_in: float = 0.0
    duration: float | None = None


class AudioPlaylist:
    """Threaded, deterministic serial playback queue for short BBB clips."""
    def __init__(self, media: "MediaController") -> None:
        self._media = media
        self._items: deque[AudioQueueItem] = deque()
        self._condition = threading.Condition()
        self._closed = False
        self._thread: threading.Thread | None = None

    @property
    def pending(self) -> int:
        with self._condition: return len(self._items)

    def enqueue(self, file: str | Path, *, gain_db: float = 0.0,
                fade_in: float = 0.0, duration: float | None = None) -> int:
        path = self._media._prepare("audio", Path(file).expanduser())
        if duration is not None and duration <= 0: raise ValueError("duration must be positive")
        with self._condition:
            self._items.append(AudioQueueItem(path, gain_db, fade_in, duration))
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, daemon=True, name="sbc-audio-playlist")
                self._thread.start()
            self._condition.notify_all()
            return len(self._items)

    def clear(self) -> None:
        with self._condition: self._items.clear()

    def close(self) -> None:
        with self._condition:
            self._closed = True; self._items.clear(); self._condition.notify_all()

    @staticmethod
    def _duration(item: AudioQueueItem) -> float:
        if item.duration is not None: return item.duration
        try:
            import av
            with av.open(str(item.file)) as container:
                return max(0.1, float(container.duration or 1_000_000) / 1_000_000)
        except Exception:
            return 1.0

    def _run(self) -> None:
        while True:
            with self._condition:
                if not self._items:
                    return
                if self._closed: return
                item = self._items.popleft()
            try:
                self._media.audio.play(item.file, loop=False, gain_db=item.gain_db, fade_in=item.fade_in)
                # Wait for the source duration before sending the next clip.
                # A caller can override duration when a container omits it.
                time.sleep(self._duration(item))
            except Exception as exc:
                get_logger().warning("SBC audio playlist item failed (%s): %s", item.file.name, exc)


class _ListenerSource:
    def __init__(self, media: "MediaController"): self.media = media
    def join(self) -> None: self.media._join_listener()
    def leave(self) -> None: self.media._leave_listener()


class _MicrophoneSource:
    def __init__(self, media: "MediaController"): self.media = media
    def join(self) -> None: self.media._warm_audio()
    def leave(self) -> None: self.media.audio.stop()


class _ScreenshareSource:
    """Media-facing handle for a mutable visual screenshare."""

    def __init__(self, media: "MediaController") -> None:
        self.media = media

    def start(self, surface: VisualSurface) -> None:
        self.media._start_screenshare(surface)

    def play(self, file: str | Path, *, loop: bool = True, frame_rate: int = 15) -> None:
        """Publish a local video file as BBB screenshare media."""
        self.media._play_screenshare_file(Path(file), loop=loop, frame_rate=frame_rate)

    def stop(self) -> None:
        self.media._stop_screenshare()

    @property
    def active(self) -> bool:
        return self.media._screenshare_active()


class MediaController:
    """Publish local media files directly to BBB's configured media backend."""
    def __init__(self, client: Any):
        self.client = client; self.audio = _Source(self, "audio"); self.camera = _Source(self, "video"); self.listener = _ListenerSource(self); self.microphone = _MicrophoneSource(self); self.screenshare = _ScreenshareSource(self); self._instance: _LiveKitPublisher | None = None; self._sfu: _SFUAudioPublisher | None = None; self._sfu_video: _SFUVideoPublisher | None = None; self._sfu_screenshare: _SFUScreensharePublisher | None = None; self._listener: _SFUListener | None = None; self._backend: dict[str, Any] | None = None
        self._desired_input_mode: str | None = None
        self._audio_observation: tuple[float, int, int] | None = None
        self._timers: list[threading.Timer] = []
        self.playlist = AudioPlaylist(self)

    def set_input_mode(self, mode: str) -> None:
        """Remember the last BBB audio-input mode for media reconnections."""
        if mode not in {"listener", "microphone"}:
            raise ValueError("BBB input mode must be 'listener' or 'microphone'")
        self._desired_input_mode = mode

    def _restore_input_mode(self) -> None:
        """Restore the GraphQL participant mode after a new SFU connection."""
        mode = self._desired_input_mode
        if mode == "listener":
            get_logger().info("Restoring BBB listener mode after media reconnect")
            self.client.actions.userSetListenOnlyInput(listenOnlyInputDevice=True)
            return
        if mode == "microphone":
            get_logger().info("Restoring BBB microphone mode after media reconnect")
            self.client.actions.userSetListenOnlyInput(listenOnlyInputDevice=False)
            # A file publisher needs to resume the exact unmuted state users
            # expect after the SFU peer is recreated. Warmed silent audio stays
            # muted until a clip is explicitly attached.
            if self._sfu is not None and self._sfu._active_file:
                self.client.users.unmute(self.client.session.user_id or "")
    def _publisher(self) -> _LiveKitPublisher:
        if self._instance is None:
            self._instance = _LiveKitPublisher()
            self._instance.on_audio_frame = self._capture_livekit_audio
        return self._instance

    def _capture_livekit_audio(
        self,
        pcm: bytes,
        *,
        sample_rate: int,
        channels: int,
        user_id: str | None,
        user_name: str | None,
    ) -> None:
        """Adapt participant-labelled LiveKit PCM into ``client.audio``."""
        try:
            self.client.audio.ingest(
                pcm, sample_rate=sample_rate, channels=channels,
                user_id=user_id, user_name=user_name, source="livekit",
            )
        except Exception as exc:
            get_logger().debug("Could not capture LiveKit audio frame: %s", exc)
    def _credentials(self) -> tuple[str, str]:
        livekit = self.client.session.snapshot.get("livekit") or {}
        cached = livekit.get("token") or self.client.session.snapshot.get("livekit_token")
        if not cached:
            debug_trace("media.livekit_token_fetch")
            data = self.client.graphql.execute(LIVEKIT_CREDENTIALS)
            current = (data.get("user_current") or [{}])[0]
            cached = (current.get("livekit") or {}).get("livekitToken")
        if not cached:
            meeting = current.get("meeting") or {}
            audio_bridge = meeting.get("audioBridge", "unknown")
            camera_bridge = meeting.get("cameraBridge", "unknown")
            raise MediaConnectionError(
                f"this BBB meeting uses audioBridge={audio_bridge!r}, cameraBridge={camera_bridge!r}; "
                "it does not have a LiveKit room token"
            )
        configured = livekit.get("url") or self.client.session.snapshot.get("livekit_url")
        if configured:
            self.client.session.snapshot["livekit"] = {"token": cached, "url": configured}
            debug_trace("media.livekit_credentials_ready", url=configured, cached=bool(livekit.get("token")))
            return configured, cached
        parsed = urlparse(self.client.session.server)
        url = f"wss://{parsed.netloc}/livekit"
        self.client.session.snapshot["livekit"] = {"token": cached, "url": url}
        debug_trace("media.livekit_credentials_ready", url=url, cached=bool(livekit.get("token")))
        return url, cached
    def _media_backend(self) -> dict[str, Any]:
        if self._backend is None:
            data = self.client.graphql.execute(LIVEKIT_CREDENTIALS)
            current = (data.get("user_current") or [{}])[0]
            self._backend = {"livekit_token": (current.get("livekit") or {}).get("livekitToken"), **(current.get("meeting") or {})}
            debug_trace("media.backend_detected", audio_backend=self._backend.get("audioBridge"), camera_backend=self._backend.get("cameraBridge"), livekit=bool(self._backend.get("livekit_token")))
        return self._backend

    def _screenshare_context(self) -> dict[str, Any]:
        """Read the BBB fields used by HTML5's ``ScreenshareBroker``."""
        data = self.client.graphql.execute(SCREENSHARE_CONTEXT)
        meeting_rows = data.get("meeting") or []
        meeting = meeting_rows[0] if isinstance(meeting_rows, list) and meeting_rows else meeting_rows
        if not isinstance(meeting, dict):
            meeting = {}
        meeting_id = meeting.get("meetingId") or self.client.session.meeting_id
        voice_bridge = (meeting.get("voiceSettings") or {}).get("voiceConf")
        user_id = self.client.session.user_id
        if not meeting_id:
            raise MediaConnectionError("BBB did not expose the internal meeting id required for screenshare")
        if not voice_bridge:
            raise MediaConnectionError("BBB did not expose the voice bridge required for screenshare")
        if not user_id:
            raise MediaConnectionError("the SBC session does not contain a BBB user id for screenshare")
        settings = self.client.session.snapshot.get("bbb_webrtc_sfu") or {}
        return {
            "meeting_id": meeting_id,
            "voice_bridge": voice_bridge,
            "bridge": meeting.get("screenShareBridge"),
            "user_id": user_id,
            "user_name": self.client.session.user_name or user_id,
            "bitrate": int(settings.get("screenshare_bitrate", 1500)),
            "media_server": settings.get("screenshare_media_server"),
        }

    def _start_screenshare(self, surface: VisualSurface) -> None:
        if not isinstance(surface, VisualSurface):
            raise TypeError("screenshare.start() requires a VisualSurface or TextBoard")
        backend = self._media_backend()
        configured = str(self.client.session.snapshot.get("screenshare_backend", "")).lower()
        use_livekit = configured == "livekit" or (
            not configured
            and backend.get("screenShareBridge") == "livekit"
            and bool(backend.get("livekit_token"))
        )
        if use_livekit:
            url, token = self._credentials()
            get_logger().info("Starting LiveKit visual screenshare (%sx%s @ %sfps)", surface.width, surface.height, surface.frame_rate)
            self._publisher().submit(self._publisher().share_surface(surface, url, token)).result()
            return
        context = self._screenshare_context()
        if context.get("bridge") == "livekit":
            raise MediaConnectionError("BBB selected LiveKit screenshare but did not expose a LiveKit media token")
        if self._sfu_screenshare is not None:
            self._sfu_screenshare.submit(self._sfu_screenshare.close()).result()
        self._sfu_screenshare = _SFUScreensharePublisher(self.client, surface, context)
        self._sfu_screenshare.submit(self._sfu_screenshare.start()).result()

    def _play_screenshare_file(self, path: Path, *, loop: bool, frame_rate: int) -> None:
        path = self._prepare("video", path)
        backend = self._media_backend()
        configured = str(self.client.session.snapshot.get("screenshare_backend", "")).lower()
        use_livekit = configured == "livekit" or (
            not configured
            and backend.get("screenShareBridge") == "livekit"
            and bool(backend.get("livekit_token"))
        )
        if use_livekit:
            url, token = self._credentials()
            get_logger().info("Starting LiveKit media screenshare: %s", path.name)
            self._publisher().submit(
                self._publisher().share_file(str(path), loop, frame_rate, url, token),
            ).result()
            return
        context = self._screenshare_context()
        if context.get("bridge") == "livekit":
            raise MediaConnectionError("BBB selected LiveKit screenshare but did not expose a LiveKit media token")
        if self._sfu_screenshare is not None:
            self._sfu_screenshare.submit(self._sfu_screenshare.close()).result()
        self._sfu_screenshare = _SFUScreensharePublisher(self.client, path, context, loop=loop)
        self._sfu_screenshare.submit(self._sfu_screenshare.start()).result()

    def _stop_screenshare(self) -> None:
        if self._instance is not None:
            self._instance.submit(self._instance._clear("screenshare")).result()
        if self._sfu_screenshare is not None:
            self._sfu_screenshare.submit(self._sfu_screenshare.close()).result()
            self._sfu_screenshare = None

    def _screenshare_active(self) -> bool:
        if self._sfu_screenshare is not None:
            return bool(self._sfu_screenshare._ready and self._sfu_screenshare._sharing_active)
        return bool(self._instance is not None and "screenshare" in self._instance.publications)
    def _prepare(self, kind: str, path: Path) -> Path:
        import av
        with av.open(str(path)) as container:
            if not any(stream.type == kind for stream in container.streams):
                raise MediaConnectionError(f"the selected file has no {kind} stream")
        return path
    def _join_listener(self, *, capture: bool = False) -> None:
        """Join BBB's listener room without altering its WebRTC handshake.

        The normal automatic listener join deliberately leaves capture off.
        Incoming audio is attached only when the public ``client.audio`` API
        explicitly requests it, after the server has accepted the session.
        """
        backend = self._media_backend()
        if backend.get("audioBridge") != "bbb-webrtc-sfu":
            raise MediaConnectionError(f"BBB listener sessions are unavailable for audio backend {backend.get('audioBridge')!r}")
        self.set_input_mode("listener")
        if self._listener is None:
            self._listener = _SFUListener(self.client.session)
            self._listener.on_connection_ready = self._restore_input_mode
        self._listener.submit(self._listener.join()).result()
        if capture:
            self._listener.on_audio_frame = self._capture_listener_audio
            self._listener.submit(self._listener._activate_audio_capture()).result()

    def start_audio_capture(self) -> None:
        """Connect the correct BBB receive backend for ``client.audio``."""
        backend = self._media_backend()
        if backend.get("audioBridge") == "bbb-webrtc-sfu":
            # A full-audio peer already receives the mix after it is active;
            # otherwise use BBB's actual listener endpoint.
            if self._sfu is not None and self._sfu._ready:
                self._sfu.on_audio_frame = self._capture_listener_audio
                self._sfu.submit(self._sfu._activate_audio_capture()).result()
                return
            if self._listener is None or not self._listener._ready:
                self._join_listener(capture=True)
            else:
                self._listener.on_audio_frame = self._capture_listener_audio
                self._listener.submit(self._listener._activate_audio_capture()).result()
            return
        if backend.get("livekit_token"):
            url, token = self._credentials()
            self._publisher().submit(self._publisher().receive(url, token)).result()
            return
        raise MediaConnectionError(
            f"incoming audio capture is unavailable for BBB audio backend {backend.get('audioBridge')!r}"
        )

    def _capture_listener_audio(self, frame: Any) -> None:
        """Adapt BBB's decoded listener mix into the public audio API."""
        try:
            self.client.audio.ingest_av_frame(
                frame,
                user_name="Conference mix",
                mixed=True,
                source="bbb-webrtc-sfu",
            )
        except Exception as exc:
            get_logger().debug("Could not capture BBB listener audio frame: %s", exc)
    def _leave_listener(self) -> None:
        if self._listener is not None:
            self._listener.submit(self._listener.close()).result()
            self._listener = None
    def _warm_audio(self) -> None:
        backend = self._media_backend()
        if backend.get("audioBridge") != "bbb-webrtc-sfu":
            raise MediaConnectionError(f"BBB microphone warm-up is unavailable for audio backend {backend.get('audioBridge')!r}")
        # This setting is independent from the WebRTC role. Clear a retained
        # listener preference before attempting to publish a microphone track.
        self.set_input_mode("microphone")
        self.client.actions.userSetListenOnlyInput(listenOnlyInputDevice=False)
        self._leave_listener()
        if self._sfu is None:
            self._sfu = _SFUAudioPublisher(self.client.session)
            self._sfu.on_connection_ready = self._restore_input_mode
            self._sfu.on_audio_frame = self._capture_listener_audio
        self._sfu.submit(self._sfu.warmup()).result()
        # The connection is real full audio, but its silent source must remain
        # muted until a script explicitly plays audio.
        self.client.users.mute(self.client.session.user_id or "")
    def _play(self, kind: str, path: Path, loop: bool, frame_rate: int,
              *, gain_db: float = 0.0, fade_in: float = 0.0) -> None:
        backend = self._media_backend()
        debug_trace("media.publish_requested", kind=kind, filename=path.name, loop=loop, backend=backend.get("audioBridge") if kind == "audio" else backend.get("cameraBridge"))
        if backend.get("livekit_token"):
            self._publisher().submit(self._publisher().play(kind, str(path), loop, frame_rate, *self._credentials())).result(); return
        if backend.get("audioBridge") == "bbb-webrtc-sfu" and kind == "audio":
            # BBB allows one audio role per identity. Replace recv-only with
            # the file publisher before broadcasting the warning clip.
            self._leave_listener()
            self.set_input_mode("microphone")
            if self._sfu is None:
                self._sfu = _SFUAudioPublisher(self.client.session)
                self._sfu.on_connection_ready = self._restore_input_mode
                self._sfu.on_audio_frame = self._capture_listener_audio
            try:
                self._sfu.submit(self._sfu.play(str(path), loop, gain_db=gain_db, fade_in=fade_in)).result()
            except MediaConnectionError:
                # BBB's own UserJoinMeetingReq handler has an explicit
                # reconnect branch. Re-run that source-defined recovery once,
                # then create a fresh SFU/ICE connection with new TURN creds.
                get_logger().warning("All SFU attempts failed; requesting a BBB user reconnection and retrying once")
                self.client.ensure_joined(force=True)
                self._sfu.submit(self._sfu.close()).result()
                self._sfu = _SFUAudioPublisher(self.client.session)
                self._sfu.on_connection_ready = self._restore_input_mode
                self._sfu.on_audio_frame = self._capture_listener_audio
                self._sfu.submit(self._sfu.play(str(path), loop, gain_db=gain_db, fade_in=fade_in)).result()
            self._mute("audio", False)
            self._verify_audio_input_state()
            return
        if backend.get("cameraBridge") == "bbb-webrtc-sfu" and kind == "video":
            if self._sfu_video is None: self._sfu_video = _SFUVideoPublisher(self.client)
            self._sfu_video.submit(self._sfu_video.play(str(path), loop)).result()
            return
        raise MediaConnectionError(f"custom {kind} publishing is unavailable for BBB media backend {backend.get('cameraBridge' if kind == 'video' else 'audioBridge')!r}")
    def _mute(self, kind: str, muted: bool) -> None:
        backend = self._media_backend()
        if backend.get("livekit_token"):
            self._publisher().submit(self._publisher().mute(kind, muted)).result(); return
        if kind == "audio" and backend.get("audioBridge") == "bbb-webrtc-sfu":
            if muted: self.client.users.mute(self.client.session.user_id or "")
            else: self.client.users.unmute(self.client.session.user_id or "")
        if kind == "video" and backend.get("cameraBridge") == "bbb-webrtc-sfu" and self._sfu_video is not None:
            self._sfu_video.submit(self._sfu_video.set_broadcast(not muted)).result()

    def _verify_audio_input_state(self) -> None:
        """Log BBB's authoritative post-publish microphone state.

        A connected WebRTC peer alone is not proof that BBB has accepted the
        sender as a microphone.  Querying the user table makes a retained
        listener preference visible and automatically retries the two BBB
        state mutations once before warning the caller.
        """
        user_id = self.client.session.user_id
        if not user_id:
            return
        try:
            user = next((item for item in self.client.users.list() if item.id == user_id), None)
            if user is None:
                get_logger().warning("BBB could not verify the custom audio input state for the saved user")
                return
            if user.muted or user.listen_only or user.listen_only_input_device:
                get_logger().warning(
                    "BBB still reports custom audio as muted/listener; retrying microphone activation "
                    "(muted=%s, listen_only=%s, listen_only_input=%s)",
                    user.muted, user.listen_only, user.listen_only_input_device,
                )
                self.client.actions.userSetListenOnlyInput(listenOnlyInputDevice=False)
                self.client.users.unmute(user_id)
                return
            get_logger().info("BBB confirmed the custom audio sender is active")
        except Exception as exc:
            # Publishing has already succeeded. A verification query is useful
            # diagnostics, not a reason to discard a working media session.
            debug_trace("media.audio_input_verify_failed", error=str(exc))
    def _stop(self, kind: str) -> None:
        if self._sfu is not None and kind == "audio": self._sfu.submit(self._sfu.close()).result(); self._sfu = None
        if self._sfu_video is not None and kind == "video": self._sfu_video.submit(self._sfu_video.close()).result(); self._sfu_video = None
        if self._instance is not None: self._instance.submit(self._instance._clear(kind)).result()
        if kind == "audio" and self.client.listen_only and self._media_backend().get("audioBridge") == "bbb-webrtc-sfu":
            self._join_listener()
    def credentials(self) -> dict[str, str]:
        """Fetch the BBB LiveKit room credential for this running client."""
        backend = self._media_backend()
        if not backend.get("livekit_token"):
            return {"backend": backend.get("audioBridge", "unknown"), "camera_backend": backend.get("cameraBridge", "unknown")}
        url, token = self._credentials()
        return {"url": url, "token": token}
    def status(self) -> dict[str, Any]:
        """Return live publishing state without exposing transport internals."""
        def sfu_state(publisher: _SFUAudioPublisher | None) -> str:
            if publisher is None or publisher.pc is None:
                return "stopped"
            return publisher.connection_state
        return {
            "backend": self._media_backend().get("audioBridge", "unknown"),
            "audio": sfu_state(self._sfu),
            # Non-zero counters prove that aiortc is clocking file frames onto
            # the negotiated BBB sender; callers can inspect them directly.
            "audio_stats": self._sfu.outbound_audio_stats() if self._sfu else {},
            "camera": sfu_state(self._sfu_video),
            "listener": sfu_state(self._listener),
            "screenshare": sfu_state(self._sfu_screenshare),
        }
    def audio_health(self, *, stall_after: float = 20.0, recover: bool = True) -> MediaHealth:
        """Check that a connected looping SFU source is actually emitting RTP.

        A peer connection can be ``connected`` while an encoder has stopped.
        This method detects that condition from counter progress and makes one
        fresh SFU connection when recovery is enabled.
        """
        if stall_after <= 0: raise ValueError("stall_after must be positive")
        state = self.status(); stats = state["audio_stats"]
        now = time.monotonic(); packets = int(stats.get("packets_sent", 0)); bytes_sent = int(stats.get("bytes_sent", 0))
        connected = state["audio"] == "connected"
        stale = False; reason = None; recovered = False
        previous = self._audio_observation
        if not connected and self._sfu and self._sfu._active_file:
            stale, reason = True, f"connection state is {state['audio']}"
        elif connected and previous and (packets, bytes_sent) == previous[1:] and now - previous[0] >= stall_after:
            stale, reason = True, f"outbound RTP did not advance for {now - previous[0]:.1f}s"
        self._audio_observation = (now, packets, bytes_sent)
        if stale and recover and self._sfu is not None:
            try:
                self._sfu.submit(self._sfu.reconnect_now(reason or "stalled RTP")).result(timeout=35)
                recovered = True
                self._audio_observation = None
            except Exception as exc:
                raise MediaStalledError("BBB audio appears stalled and recovery failed", recoverable=True,
                                       context={"reason": reason, "error": str(exc)}) from exc
        return MediaHealth(backend=str(state["backend"]), connected=connected,
                           packets_sent=packets, bytes_sent=bytes_sent, stale=stale,
                           recovered=recovered, reason=reason,
                           observed_at=datetime.now(timezone.utc).isoformat())
    def close(self) -> None:
        self.playlist.close()
        for timer in self._timers: timer.cancel()
        self._timers.clear()
        self._stop_screenshare()
        if self._instance is not None: self._instance.submit(self._instance.close()).result(); self._instance = None
        if self._sfu is not None: self._sfu.submit(self._sfu.close()).result(); self._sfu = None
        if self._sfu_video is not None: self._sfu_video.submit(self._sfu_video.close()).result(); self._sfu_video = None
        self._leave_listener()
