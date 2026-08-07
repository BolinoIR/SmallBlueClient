"""Pure-Python custom media publisher for BBB's LiveKit bridge."""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import threading
import json
from fractions import Fraction
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, urljoin
from urllib.request import Request, urlopen

from ..core.exceptions import ConnectionError
from ..core.logging import debug_trace, get_logger

LIVEKIT_CREDENTIALS = "subscription SBCLiveKitCredentials{user_current{userId livekit{livekitToken} meeting{audioBridge cameraBridge}}}"


class MediaConnectionError(ConnectionError):
    """BBB did not provide a LiveKit credential for the saved session."""


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
        self._audio_sender = None
        self._silent_track = None
        self._warmed = False
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

        BBB 3.0's ``SFUAudioBridge.getOfferingRole`` makes a full-audio
        publisher answer the SFU offer whenever transparent listen-only is
        enabled.  The server's stock setting enables that feature, so treating
        every publisher as an offerer can establish a peer connection while
        leaving its input media unusable.  New extractor sessions record both
        flags; old sessions use the BBB 3.0 source default.
        """
        # Sessions exported before SBC 0.1.5 lack these flags. BBB 3.0's
        # source defaults transparent listen-only to true, which makes full
        # audio the answerer.  Use that source default rather than treating an
        # old session as an offerer.  The accompanying ``transparentListenOnly``
        # start field below is essential for this answerer route.
        if "transparent_listen_only" not in self._settings and "full_audio_offering" not in self._settings:
            return False
        transparent = bool(self._setting("transparent_listen_only", True))
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
            "transparentListenOnly": bool(self._setting("transparent_listen_only", True)),
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
    def _ice_configuration(self):
        """Fetch the exact TURN credentials used by BBB's SFU audio client."""
        from aiortc import RTCConfiguration, RTCIceServer
        token = self._session_token()
        configured = self._setting("stun_turn_url", "/bigbluebutton/api/stuns")
        endpoint = urlunparse(urlparse(urljoin(f"{self.session.server}/", configured)))
        separator = "&" if "?" in endpoint else "?"
        endpoint = f"{endpoint}{separator}{urlencode({'sessionToken': token})}"
        try:
            request = Request(endpoint, headers=self.session.headers)
            with urlopen(request, timeout=10) as response: data = json.load(response)
            servers = [RTCIceServer(item["url"]) for item in data.get("stunServers", [])]
            turns = [RTCIceServer(item["url"], item.get("username"), item.get("password")) for item in data.get("turnServers", [])]
            # aiortc/aioice supports one TURN endpoint per peer connection. BBB
            # normally sends UDP first and TLS-over-TCP second. Prefer the
            # TLS endpoint: it survives Wi-Fi/VPN UDP filtering and mirrors
            # BBB's own retry-through-relay fallback.
            turns.sort(key=lambda item: 0 if str(item.urls).lower().startswith("turns:") else 1)
            servers.extend(turns)
            debug_trace("media.turn_credentials_loaded", stun_servers=len(data.get("stunServers", [])), turn_servers=len(turns))
            return RTCConfiguration(iceServers=servers)
        except Exception as exc:
            raise MediaConnectionError(f"could not fetch BBB TURN credentials: {exc}") from exc
    async def _play_once(self, filename: str, loop: bool) -> None:
        from aiortc.contrib.media import MediaPlayer
        self.player = MediaPlayer(filename, loop=loop)
        if self.player.audio is None: raise MediaConnectionError("the selected file has no audio stream")
        await self._connect_track(self.player.audio)

    async def _connect_track(self, track) -> None:
        from aiortc import RTCPeerConnection, RTCSessionDescription
        import websockets
        offering = self._full_audio_offering()
        debug_trace("media.sfu_audio_connect_start", url=self._url(), offering=offering)
        self.pc = RTCPeerConnection(self._ice_configuration())
        self._audio_sender = self.pc.addTrack(track)
        connected = asyncio.Event()
        @self.pc.on("connectionstatechange")
        def on_connection_state_change():
            self.connection_state = self.pc.connectionState
            if self.connection_state in ("connected", "failed", "closed"): connected.set()
            if self._ready and self.connection_state in ("failed", "closed"):
                self._schedule_reconnect()
        local_offer = None
        if offering:
            offer = await self.pc.createOffer()
            await self.pc.setLocalDescription(offer)
            while self.pc.iceGatheringState != "complete":
                await asyncio.sleep(0.05)
            local_offer = self.pc.localDescription.sdp
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
                    answer = await self.pc.createAnswer()
                    await self.pc.setLocalDescription(answer)
                    await self.ws.send(json.dumps({
                        "id": "subscriberAnswer", "type": "audio", "role": "sendrecv",
                        "sdpOffer": self.pc.localDescription.sdp,
                    }))
            elif message.get("id") == "iceCandidate" and message.get("candidate"):
                # Needed by BBB installations configured for trickle ICE.
                await self.pc.addIceCandidate(message["candidate"])
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
        debug_trace("media.sfu_audio_connected", connection_state=self.connection_state, ice_state=self.pc.iceConnectionState)
        self._signal_task = asyncio.create_task(self._listen_for_signals())
        # BBB's own BaseBroker uses JSON ``{id: 'ping'}`` heartbeats rather
        # than only WebSocket control-frame pings. Keep that source-defined
        # signalling lease alive for long-running bots.
        self._heartbeat_task = asyncio.create_task(self._heartbeat())

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
                    await self.pc.addIceCandidate(message["candidate"])
                elif message.get("id") in ("webRTCAudioError", "error"):
                    raise MediaConnectionError(message.get("reason", "bbb-webrtc-sfu audio error"))
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
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
                get_logger().warning("BBB media disconnected; retrying in %.0fs", delay)
                await self._dispose()
                if self._stopping or not self._active_file:
                    return
                await asyncio.sleep(delay)
                try:
                    await self._play_once(self._active_file, self._active_loop)
                    get_logger().info("BBB media reconnected")
                    return
                except Exception:
                    continue
        finally:
            self._reconnect_task = None

    def _swap_warmed_track(self, player: Any, filename: str, loop: bool) -> None:
        """Replace the warm-up silence source without terminating sender RTP."""
        if self._audio_sender is None or player.audio is None:
            raise MediaConnectionError("BBB audio sender or file track is unavailable")
        self._audio_sender.replaceTrack(player.audio)
        # Do not call ``stop()`` on the previously attached silence track
        # here. RTCRtpSender can still be awaiting that track's ``recv``;
        # stopping it raises MediaStreamError, terminates the sender's RTP
        # task, and silently leaves the negotiated connection alive with no
        # outgoing audio. Replacing the track is sufficient; the silent source
        # owns no background resources and is released on close.
        self.player, self._silent_track = player, None
        self._warmed = False
        self._active_file, self._active_loop = filename, loop

    async def play(self, filename: str, loop: bool) -> None:
        """Publish audio and retry transient SFU/ICE failures automatically."""
        if self._ready and self._warmed and self.pc and self._audio_sender:
            from aiortc.contrib.media import MediaPlayer
            player = MediaPlayer(filename, loop=loop)
            if player.audio is None:
                raise MediaConnectionError("the selected file has no audio stream")
            self._swap_warmed_track(player, filename, loop)
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
        last_error: Exception | None = None
        delays = (1.0, 2.0, 4.0, 6.0)
        for attempt in range(5):
            try:
                get_logger().info("Starting BBB custom audio (attempt %s/5)", attempt + 1)
                await self._play_once(filename, loop)
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
    async def _dispose(self) -> None:
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

    async def join(self) -> None:
        await self.close()
        self._stopping = False
        self._listener_active = True
        try:
            await asyncio.wait_for(self._connect_listener(), timeout=30)
            get_logger().info("BBB listener audio is connected")
        except Exception:
            self._listener_active = False
            self._stopping = True
            await self._dispose()
            raise

    async def _connect_listener(self) -> None:
        from aiortc import RTCPeerConnection, RTCSessionDescription
        import websockets

        self.pc = RTCPeerConnection(self._ice_configuration())
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
        get_logger().info("Starting BBB listener SFU session (offering=%s)", offering)
        headers = [(key, value) for key, value in self.session.headers.items() if value]
        self.ws = await websockets.connect(self._url(), extra_headers=headers, ping_interval=15, ping_timeout=20, close_timeout=2)
        self._session_number += 1
        message = {
            "id": "start", "type": "audio", "role": "recv",
            "clientSessionNumber": self._session_number,
            "transparentListenOnly": False,
        }
        if local_offer:
            message["sdpOffer"] = local_offer
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
                        "sdpOffer": self.pc.localDescription.sdp,
                    }))
            elif message.get("id") == "iceCandidate" and message.get("candidate"):
                await self.pc.addIceCandidate(message["candidate"])
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
        self._signal_task = asyncio.create_task(self._listen_for_signals())

    async def _reconnect(self) -> None:
        try:
            for delay in (1.0, 2.0, 5.0):
                get_logger().warning("BBB listener disconnected; retrying in %.0fs", delay)
                await self._dispose()
                if self._stopping or not self._listener_active:
                    return
                await asyncio.sleep(delay)
                try:
                    await self._connect_listener()
                    get_logger().info("BBB listener reconnected")
                    return
                except Exception:
                    continue
        finally:
            self._reconnect_task = None

    async def close(self) -> None:
        self._listener_active = False
        await super().close()


class _SFUVideoPublisher(_SFUAudioPublisher):
    """BBB 3.0.32 VideoProvider publisher for ``bbb-webrtc-sfu`` cameras."""
    def __init__(self, client: Any):
        super().__init__(client.session)
        self.client = client
        self.camera_id: str | None = None

    async def _play_once(self, filename: str, loop: bool) -> None:
        from aiortc import RTCPeerConnection, RTCSessionDescription
        from aiortc.contrib.media import MediaPlayer
        import websockets
        self.player = MediaPlayer(filename, loop=loop)
        if self.player.video is None: raise MediaConnectionError("the selected file has no video stream")
        # VideoService.buildStreamName(): <userId>_<clientSessionUUID>_<deviceId>
        client_uuid = (self.session.connection_payload.get("headers") or {}).get("X-ClientSessionUUID", "sbc")
        self.camera_id = f"{self.session.user_id}_{client_uuid}_sbc-custom-camera"
        self.pc = RTCPeerConnection(self._ice_configuration()); self.pc.addTrack(self.player.video)
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
        message = {"id": "start", "type": "video", "cameraId": self.camera_id, "role": "share", "sdpOffer": self.pc.localDescription.sdp, "bitrate": 200, "record": True}
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
        name = "cameraBroadcastStart" if enabled else "cameraBroadcastStop"
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
        await self.room.connect(url, token, rtc.RoomOptions(auto_subscribe=False))

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
        for kind in tuple(self.tracks): await self._clear(kind)
        if self.room is not None:
            with contextlib.suppress(Exception): await self.room.disconnect()
            self.room = None


class _Source:
    def __init__(self, media: "MediaController", kind: str): self.media, self.kind = media, kind
    def play(self, file: str | Path, *, loop: bool = True, frame_rate: int = 30) -> None:
        path = Path(file).expanduser()
        if not path.is_file(): raise FileNotFoundError(path)
        self.media._play(self.kind, path, loop, frame_rate)
    def prepare(self, file: str | Path) -> Path:
        """Decode-check a file before it is needed by an event handler."""
        path = Path(file).expanduser()
        if not path.is_file(): raise FileNotFoundError(path)
        return self.media._prepare(self.kind, path)
    def warmup(self) -> None:
        """Pre-connect a muted BBB microphone for low-latency ``play()``."""
        if self.kind != "audio": raise MediaConnectionError("only audio can be warmed")
        self.media._warm_audio()
    def mute(self) -> None: self.media._mute(self.kind, True)
    def unmute(self) -> None: self.media._mute(self.kind, False)
    def stop(self) -> None: self.media._stop(self.kind)


class _ListenerSource:
    def __init__(self, media: "MediaController"): self.media = media
    def join(self) -> None: self.media._join_listener()
    def leave(self) -> None: self.media._leave_listener()


class _MicrophoneSource:
    def __init__(self, media: "MediaController"): self.media = media
    def join(self) -> None: self.media._warm_audio()
    def leave(self) -> None: self.media.audio.stop()


class MediaController:
    """Publish local media files directly to BBB's configured media backend."""
    def __init__(self, client: Any):
        self.client = client; self.audio = _Source(self, "audio"); self.camera = _Source(self, "video"); self.listener = _ListenerSource(self); self.microphone = _MicrophoneSource(self); self._instance: _LiveKitPublisher | None = None; self._sfu: _SFUAudioPublisher | None = None; self._sfu_video: _SFUVideoPublisher | None = None; self._listener: _SFUListener | None = None; self._backend: dict[str, Any] | None = None
    def _publisher(self) -> _LiveKitPublisher:
        if self._instance is None: self._instance = _LiveKitPublisher()
        return self._instance
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
    def _prepare(self, kind: str, path: Path) -> Path:
        import av
        with av.open(str(path)) as container:
            if not any(stream.type == kind for stream in container.streams):
                raise MediaConnectionError(f"the selected file has no {kind} stream")
        return path
    def _join_listener(self) -> None:
        backend = self._media_backend()
        if backend.get("audioBridge") != "bbb-webrtc-sfu":
            raise MediaConnectionError(f"BBB listener sessions are unavailable for audio backend {backend.get('audioBridge')!r}")
        if self._listener is None: self._listener = _SFUListener(self.client.session)
        self._listener.submit(self._listener.join()).result()
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
        self.client.actions.userSetListenOnlyInput(listenOnlyInputDevice=False)
        self._leave_listener()
        if self._sfu is None: self._sfu = _SFUAudioPublisher(self.client.session)
        self._sfu.submit(self._sfu.warmup()).result()
        # The connection is real full audio, but its silent source must remain
        # muted until a script explicitly plays audio.
        self.client.users.mute(self.client.session.user_id or "")
    def _play(self, kind: str, path: Path, loop: bool, frame_rate: int) -> None:
        backend = self._media_backend()
        debug_trace("media.publish_requested", kind=kind, filename=path.name, loop=loop, backend=backend.get("audioBridge") if kind == "audio" else backend.get("cameraBridge"))
        if backend.get("livekit_token"):
            self._publisher().submit(self._publisher().play(kind, str(path), loop, frame_rate, *self._credentials())).result(); return
        if backend.get("audioBridge") == "bbb-webrtc-sfu" and kind == "audio":
            # BBB allows one audio role per identity. Replace recv-only with
            # the file publisher before broadcasting the warning clip.
            self._leave_listener()
            if self._sfu is None: self._sfu = _SFUAudioPublisher(self.client.session)
            try:
                self._sfu.submit(self._sfu.play(str(path), loop)).result()
            except MediaConnectionError:
                # BBB's own UserJoinMeetingReq handler has an explicit
                # reconnect branch. Re-run that source-defined recovery once,
                # then create a fresh SFU/ICE connection with new TURN creds.
                get_logger().warning("All SFU attempts failed; requesting a BBB user reconnection and retrying once")
                self.client.ensure_joined(force=True)
                self._sfu.submit(self._sfu.close()).result()
                self._sfu = _SFUAudioPublisher(self.client.session)
                self._sfu.submit(self._sfu.play(str(path), loop)).result()
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
        }
    def close(self) -> None:
        if self._instance is not None: self._instance.submit(self._instance.close()).result(); self._instance = None
        if self._sfu is not None: self._sfu.submit(self._sfu.close()).result(); self._sfu = None
        if self._sfu_video is not None: self._sfu_video.submit(self._sfu_video.close()).result(); self._sfu_video = None
        self._leave_listener()
