"""Source-backed unit tests for BBB media negotiation decisions."""
from __future__ import annotations

from types import SimpleNamespace
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

from sbc.media import (
    MediaController,
    _SFUAudioPublisher,
    _SFUScreensharePublisher,
    _DynamicVisualTrack,
    _SilenceAudioTrack,
    _enable_bbb_legacy_sha1_fingerprint,
)
from sbc.media.visuals import TextBoard, VisualSurface


class MediaTests(unittest.TestCase):
    @staticmethod
    def publisher(settings: dict[str, object]) -> _SFUAudioPublisher:
        # Avoid starting a real aiortc loop: this helper is pure configuration
        # logic and must remain unit-testable without a BBB server.
        publisher = object.__new__(_SFUAudioPublisher)
        publisher.session = SimpleNamespace(snapshot={"bbb_webrtc_sfu": settings})
        return publisher

    def test_full_audio_offering_follows_bbb_transparent_listen_only_rules(self) -> None:
        # BBB 3.0's stock defaults are transparentListenOnly=False and
        # fullAudioOffering=True. Legacy exports therefore use the offerer
        # route unless their captured deployment settings say otherwise.
        self.assertTrue(self.publisher({})._full_audio_offering())
        self.assertFalse(self.publisher({"transparent_listen_only": True, "full_audio_offering": True})._full_audio_offering())
        self.assertTrue(self.publisher({"transparent_listen_only": False, "full_audio_offering": True})._full_audio_offering())
        self.assertFalse(self.publisher({"transparent_listen_only": False, "full_audio_offering": False})._full_audio_offering())

    def test_relay_retry_sdp_only_advertises_turn_candidates(self) -> None:
        sdp = (
            "v=0\r\n"
            "a=candidate:host 1 udp 1 192.168.1.4 1234 typ host\r\n"
            "a=candidate:server 1 udp 1 203.0.113.8 1234 typ srflx\r\n"
            "a=candidate:turn 1 udp 1 198.51.100.9 1234 typ relay\r\n"
        )
        self.assertEqual(self.publisher({})._outgoing_sdp(sdp, force_relay=False), sdp)
        relay_sdp = self.publisher({})._outgoing_sdp(sdp, force_relay=True)
        self.assertIn("typ relay", relay_sdp)
        self.assertNotIn("typ host", relay_sdp)
        self.assertNotIn("typ srflx", relay_sdp)

    def test_legacy_bbb_sha1_fingerprint_is_validated_not_ignored(self) -> None:
        import aiortc.rtcdtlstransport as dtls
        _enable_bbb_legacy_sha1_fingerprint()
        # The compatibility path extends aiortc's normal certificate digest
        # validation map; it never disables remote identity validation.
        self.assertIn("sha-1", dtls.X509_DIGEST_ALGORITHMS)

    def test_new_session_snapshot_preserves_all_audio_negotiation_settings(self) -> None:
        settings = {
            "audio_media_server": "mediasoup",
            "listen_only_media_server": "mediasoup",
            "full_audio_offering": False,
            "listen_only_offering": True,
            "transparent_listen_only": True,
            "signal_candidates": True,
        }
        publisher = self.publisher(settings)
        self.assertEqual(publisher._setting("audio_media_server"), "mediasoup")
        self.assertTrue(publisher._setting("signal_candidates"))

    def test_fresh_extractor_turn_credentials_are_used_without_a_second_http_request(self) -> None:
        publisher = self.publisher({
            "ice_servers": {
                "expires_at": "2099-01-01T00:00:00Z",
                "stun_servers": [{"url": "stun:stun.example:3478"}],
                "turn_servers": [{"url": "turns:turn.example:5349", "username": "x", "password": "y"}],
            },
        })
        configuration = publisher._ice_configuration(force_relay=True)
        self.assertEqual(len(configuration.iceServers), 1)
        self.assertEqual(configuration.iceServers[0].urls, "turns:turn.example:5349")

    def test_browser_json_ice_candidate_is_converted_for_aiortc(self) -> None:
        peer = SimpleNamespace(addIceCandidate=AsyncMock())
        asyncio.run(_SFUAudioPublisher._add_remote_candidate(peer, {
            "candidate": "candidate:1 1 udp 2130706431 192.0.2.1 5000 typ host",
            "sdpMid": "0", "sdpMLineIndex": 0,
        }))
        candidate = peer.addIceCandidate.await_args.args[0]
        self.assertEqual((candidate.sdpMid, candidate.sdpMLineIndex), ("0", 0))

    def test_audio_start_request_matches_bbb_audiobroker_for_answerer_and_offerer(self) -> None:
        answerer = self.publisher({})
        answerer._session_number = 0
        self.assertEqual(answerer._start_request(None), {
            "id": "start", "type": "audio", "role": "sendrecv",
            "clientSessionNumber": 1, "transparentListenOnly": False,
        })

        offerer = self.publisher({
            "transparent_listen_only": False,
            "full_audio_offering": True,
            "audio_media_server": "mediasoup",
        })
        offerer._session_number = 8
        self.assertEqual(offerer._start_request("v=0\\r\\n"), {
            "id": "start", "type": "audio", "role": "sendrecv",
            "clientSessionNumber": 9, "transparentListenOnly": False,
            "sdpOffer": "v=0\\r\\n", "mediaServer": "mediasoup",
        })

    def test_media_status_exposes_outbound_audio_diagnostics_without_a_connection(self) -> None:
        media = object.__new__(MediaController)
        media._backend = {"audioBridge": "bbb-webrtc-sfu"}
        media._sfu = None
        media._sfu_video = None
        media._sfu_screenshare = None
        media._listener = None
        self.assertEqual(media.status(), {
            "backend": "bbb-webrtc-sfu", "audio": "stopped", "audio_stats": {},
            "camera": "stopped", "listener": "stopped", "screenshare": "stopped",
        })

    def test_warm_track_swap_does_not_stop_the_track_an_rtp_sender_may_still_be_reading(self) -> None:
        """Stopping it would end aiortc's RTP loop before the MP3 is sent."""
        silent = Mock()
        sender = Mock()
        publisher = self.publisher({})
        publisher._warmed = True
        publisher._audio_sender = sender
        publisher._silent_track = silent
        new_track = Mock()
        player = SimpleNamespace(audio=new_track)
        publisher._swap_warmed_track(player, "warning.mp3", False)
        sender.replaceTrack.assert_called_once_with(new_track)
        silent.stop.assert_not_called()
        self.assertIs(publisher.player, player)
        self.assertFalse(publisher._warmed)
        self.assertEqual((publisher._active_file, publisher._active_loop), ("warning.mp3", False))

    def test_warmup_silence_uses_the_same_stereo_format_as_mediaplayer_audio(self) -> None:
        async def receive_one():
            track = _SilenceAudioTrack.create()
            try:
                return await track.recv()
            finally:
                track.stop()

        frame = asyncio.run(receive_one())
        self.assertEqual((frame.format.name, frame.layout.name, frame.sample_rate, frame.samples),
                         ("s16", "stereo", 48_000, 960))

    def test_long_running_sfu_publisher_uses_bbb_application_ping(self) -> None:
        publisher = self.publisher({})
        publisher.ws = SimpleNamespace(closed=False, send=AsyncMock())
        asyncio.run(publisher._send_heartbeat())
        publisher.ws.send.assert_awaited_once_with('{"id": "ping"}')

    def test_text_board_changes_are_rendered_without_replacing_the_surface(self) -> None:
        board = TextBoard("Waiting", title="Status", width=640, height=360)
        first_revision = board.revision
        first = board.render()
        board.set_text("Round two").append("Go!")
        second = board.render()

        self.assertEqual(first.size, (640, 360))
        self.assertEqual(second.size, (640, 360))
        self.assertGreater(board.revision, first_revision)
        self.assertEqual(board.text, "Round two\nGo!")

    def test_text_board_adapts_persian_rtl_text_and_font_fallback(self) -> None:
        board = TextBoard("سلام دنیا", title="وضعیت جلسه", width=640, height=360, direction="auto", language="fa")
        image = board.render()
        self.assertEqual(board.direction, "auto")
        self.assertEqual(image.size, (640, 360))
        with self.assertRaises(ValueError):
            board.set_direction("sideways")

    def test_generic_visual_surface_supports_imperative_painting(self) -> None:
        surface = VisualSurface(320, 180, background="#000000")
        surface.paint(lambda image, draw: draw.rectangle((0, 0, 20, 20), fill="#ff0000"))
        image = surface.render()
        self.assertEqual(image.size, (320, 180))
        self.assertEqual(image.getpixel((10, 10))[:3], (255, 0, 0))

    def test_dynamic_visual_track_emits_a_paced_rgba_video_frame(self) -> None:
        async def receive_one():
            track = _DynamicVisualTrack.create(VisualSurface(160, 90, frame_rate=30))
            try:
                return await track.recv()
            finally:
                track.stop()

        frame = asyncio.run(receive_one())
        self.assertEqual((frame.width, frame.height), (160, 90))

    def test_screenshare_start_request_matches_bbb_screenshare_broker(self) -> None:
        publisher = object.__new__(_SFUScreensharePublisher)
        publisher.context = {
            "meeting_id": "internal-meeting",
            "voice_bridge": "voice-conf",
            "user_id": "user-1",
            "user_name": "SBC User",
            "bitrate": 1500,
            "media_server": "mediasoup",
        }
        self.assertEqual(publisher._start_request("v=0\r\n"), {
            "id": "start",
            "type": "screenshare",
            "role": "send",
            "internalMeetingId": "internal-meeting",
            "voiceBridge": "voice-conf",
            "userName": "SBC User",
            "callerName": "user-1",
            "sdpOffer": "v=0\r\n",
            "hasAudio": False,
            "contentType": "screenshare",
            "bitrate": 1500,
            "mediaServer": "mediasoup",
        })

    def test_listener_mode_is_restored_after_a_media_reconnect(self) -> None:
        """The SFU session alone does not restore BBB's listener UI state."""
        actions = Mock()
        client = SimpleNamespace(actions=actions, users=Mock(), session=SimpleNamespace(user_id="bot"))
        media = object.__new__(MediaController)
        media.client = client
        media._desired_input_mode = "listener"
        media._sfu = None

        media._restore_input_mode()

        actions.userSetListenOnlyInput.assert_called_once_with(listenOnlyInputDevice=True)
        client.users.unmute.assert_not_called()

    def test_active_microphone_mode_is_restored_and_unmuted_after_reconnect(self) -> None:
        """An active file source must return as a microphone, not a listener."""
        actions = Mock()
        users = Mock()
        client = SimpleNamespace(actions=actions, users=users, session=SimpleNamespace(user_id="bot"))
        media = object.__new__(MediaController)
        media.client = client
        media._desired_input_mode = "microphone"
        media._sfu = SimpleNamespace(_active_file="warning.mp3")

        media._restore_input_mode()

        actions.userSetListenOnlyInput.assert_called_once_with(listenOnlyInputDevice=False)
        users.unmute.assert_called_once_with("bot")

    def test_warmed_microphone_stays_muted_after_reconnect(self) -> None:
        actions = Mock()
        users = Mock()
        client = SimpleNamespace(actions=actions, users=users, session=SimpleNamespace(user_id="bot"))
        media = object.__new__(MediaController)
        media.client = client
        media._desired_input_mode = "microphone"
        media._sfu = SimpleNamespace(_active_file=None)

        media._restore_input_mode()

        actions.userSetListenOnlyInput.assert_called_once_with(listenOnlyInputDevice=False)
        users.unmute.assert_not_called()
