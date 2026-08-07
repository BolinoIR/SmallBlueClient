import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from sbc.core.session import SBCSession

class ExtensionSessionFormatTests(unittest.TestCase):
    def test_json_sbc_envelope_matches_extension_canonical_format(self):
        session = {"version": 1, "server": "https://bbb.example", "websocket_url": "wss://bbb.example/graphql", "metadata": {"exported_by": "SBC Chrome Extension"}}
        canonical = json.dumps(session, sort_keys=True, separators=(",", ":"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chrome.sbc"
            path.write_text(json.dumps({"session": session, "sha256": hashlib.sha256(canonical.encode()).hexdigest()}), encoding="utf-8")
            self.assertEqual(SBCSession.load(path).server, "https://bbb.example")

    def test_extractor_captures_the_sfu_audio_settings_needed_by_python_media(self):
        source = (Path(__file__).resolve().parents[1] / "extension" / "page-capture.js").read_text(encoding="utf-8")
        for field in (
            "audio_media_server", "listen_only_media_server", "full_audio_offering",
            "listen_only_offering", "transparent_listen_only", "camera_media_server",
            "signal_candidates", "ice_gathering_timeout", "stun_turn_url",
        ):
            self.assertIn(field, source)

    def test_session_loader_preserves_extracted_sfu_negotiation_settings(self):
        session = {
            "version": 1, "server": "https://bbb.example", "websocket_url": "wss://bbb.example/graphql",
            "snapshot": {"bbb_webrtc_sfu": {
                "transparent_listen_only": True, "full_audio_offering": True,
                "audio_media_server": "mediasoup",
            }},
        }
        canonical = json.dumps(session, sort_keys=True, separators=(",", ":"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chrome.sbc"
            path.write_text(json.dumps({"session": session, "sha256": hashlib.sha256(canonical.encode()).hexdigest()}), encoding="utf-8")
            loaded = SBCSession.load(path)
        self.assertEqual(loaded.snapshot["bbb_webrtc_sfu"]["audio_media_server"], "mediasoup")
        self.assertTrue(loaded.snapshot["bbb_webrtc_sfu"]["transparent_listen_only"])
