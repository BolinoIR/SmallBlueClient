import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from sbc import SBCSession
from sbc.core.exceptions import SessionError

class SessionTests(unittest.TestCase):
    def test_session_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            original = SBCSession(server="https://bbb.example", websocket_url="wss://bbb.example/graphql", meeting_id="m1", user_name="Teacher", headers={"Cookie": "session=yes"})
            path = original.save(Path(directory) / "teacher")
            loaded = SBCSession.load(path)
            self.assertEqual(path.suffix, ".sbc")
            self.assertEqual(loaded.meeting_id, "m1")
            self.assertEqual(loaded.headers["Cookie"], "session=yes")

    def test_session_rejects_changed_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            path = SBCSession(server="https://bbb.example", websocket_url="wss://bbb.example/graphql").save(Path(directory) / "bad.sbc")
            with zipfile.ZipFile(path, "a") as archive:
                archive.writestr("session.json", json.dumps({"server": "https://evil", "websocket_url": "wss://evil"}))
            with self.assertRaisesRegex(SessionError, "integrity"):
                SBCSession.load(path)

    def test_legacy_camel_case_session_is_upgraded(self):
        session = SBCSession.from_dict({"server": "https://bbb.example", "websocketUrl": "wss://bbb.example/graphql", "meetingId": "m"})
        self.assertTrue(session.websocket_url.endswith("graphql"))

    def test_browser_json_envelope_accepts_unicode_canonical_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = {"server": "https://bbb.example", "websocket_url": "wss://bbb.example/graphql", "user_name": "رایان"}
            canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            path = Path(directory) / "browser.sbc"
            path.write_text(json.dumps({"session": manifest, "sha256": __import__("hashlib").sha256(canonical).hexdigest()}), encoding="utf-8")
            self.assertEqual(SBCSession.load(path).user_name, "رایان")
