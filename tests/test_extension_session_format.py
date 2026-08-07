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
