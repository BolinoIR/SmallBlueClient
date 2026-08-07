import unittest
from datetime import datetime, timedelta, timezone

from sbc import SBCSession


class SessionHealthTests(unittest.TestCase):
    def test_valid_session_health_report(self):
        session = SBCSession(
            server="https://bbb.example",
            websocket_url="wss://bbb.example/graphql",
            connection_payload={"headers": {"X-Session-Token": "token"}},
        )

        health = session.validate()

        self.assertTrue(health.valid)
        self.assertFalse(health.requires_reexport)
        self.assertIsNone(health.expires_at)

    def test_expired_snapshot_marks_session_for_reexport(self):
        expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        session = SBCSession(
            server="https://bbb.example",
            websocket_url="wss://bbb.example/graphql",
            connection_payload={"headers": {"X-Session-Token": "token"}},
            snapshot={"session_expires_at": expired},
        )

        health = session.validate()

        self.assertFalse(health.valid)
        self.assertTrue(health.expired)
        self.assertTrue(session.requires_reexport)
        self.assertIn("expired", " ".join(health.reasons))

    def test_invalid_connection_is_reported_without_raising(self):
        health = SBCSession(server="", websocket_url="").validate()

        self.assertFalse(health.valid)
        self.assertGreaterEqual(len(health.reasons), 2)
