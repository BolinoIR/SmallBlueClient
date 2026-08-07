"""Source-backed unit tests for BBB media negotiation decisions."""
from __future__ import annotations

from types import SimpleNamespace
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

from sbc.media import MediaController, _SFUAudioPublisher, _SilenceAudioTrack


class MediaTests(unittest.TestCase):
    @staticmethod
    def publisher(settings: dict[str, object]) -> _SFUAudioPublisher:
        # Avoid starting a real aiortc loop: this helper is pure configuration
        # logic and must remain unit-testable without a BBB server.
        publisher = object.__new__(_SFUAudioPublisher)
        publisher.session = SimpleNamespace(snapshot={"bbb_webrtc_sfu": settings})
        return publisher

    def test_full_audio_offering_follows_bbb_transparent_listen_only_rules(self) -> None:
        # SBC 0.1.5 uses BBB 3.0's stock transparent-listen-only default for
        # legacy sessions that did not record the negotiation settings.
        self.assertFalse(self.publisher({})._full_audio_offering())
        self.assertFalse(self.publisher({"transparent_listen_only": True, "full_audio_offering": True})._full_audio_offering())
        self.assertTrue(self.publisher({"transparent_listen_only": False, "full_audio_offering": True})._full_audio_offering())
        self.assertFalse(self.publisher({"transparent_listen_only": False, "full_audio_offering": False})._full_audio_offering())

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

    def test_audio_start_request_matches_bbb_audiobroker_for_answerer_and_offerer(self) -> None:
        answerer = self.publisher({})
        answerer._session_number = 0
        self.assertEqual(answerer._start_request(None), {
            "id": "start", "type": "audio", "role": "sendrecv",
            "clientSessionNumber": 1, "transparentListenOnly": True,
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
        media._listener = None
        self.assertEqual(media.status(), {
            "backend": "bbb-webrtc-sfu", "audio": "stopped", "audio_stats": {},
            "camera": "stopped", "listener": "stopped",
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
