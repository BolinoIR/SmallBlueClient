from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sbc.bots import Bot, BotState
from sbc.cli import main
from sbc.media import MediaHealth
from sbc.models import ChatMessage
from sbc.reliability import EnduranceMonitor


class ReliabilityTests(unittest.TestCase):
    def test_monitor_redacts_user_auth_and_records_sample(self) -> None:
        session = Mock(); session.validate.return_value.to_dict.return_value = {"valid": True}
        media = Mock(); media.status.return_value = {"audio": "stopped"}
        client = SimpleNamespace(session=session, media=media)
        monitor = EnduranceMonitor(client, interval=1, monitor_media=False)
        client.session.snapshot = {"current_user": {"user_id": "u1", "auth_token": "secret"}}
        sample, recovered = monitor.sample(0.0)
        self.assertFalse(recovered); self.assertEqual(sample.current_user, {"user_id": "u1"})
        self.assertTrue(sample.session["valid"])

    def test_cli_validate_and_inspect_do_not_connect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.sbc"
            from sbc.core.session import SBCSession
            SBCSession(server="https://bbb.example", websocket_url="wss://bbb.example/graphql",
                       connection_payload={"headers": {"X-Session-Token": "token"}}).save(path)
            self.assertEqual(main(["validate", str(path), "--json"]), 0)
            self.assertEqual(main(["inspect", str(path), "--json"]), 0)


class BotTests(unittest.TestCase):
    def fake_client(self):
        client = Mock(); client.session.user_id = "self"; client._handlers = {}
        def on(event):
            def register(callback): client._handlers[event] = callback; return callback
            return register
        client.on.side_effect = on
        return client

    def test_commands_filter_self_and_enforce_cooldown(self) -> None:
        client = self.fake_client()
        with tempfile.TemporaryDirectory() as directory:
            bot = Bot(client, state_path=Path(directory) / "state.json")
            calls = []
            @bot.command("ping", cooldown=60)
            def ping(ctx): calls.append(ctx.arguments)
            bot.start()
            client._handlers["chat_message"](ChatMessage("1", "!ping one", "Other", "other"))
            client._handlers["chat_message"](ChatMessage("2", "!ping two", "Other", "other"))
            client._handlers["chat_message"](ChatMessage("3", "!ping self", "Self", "self"))
            self.assertEqual(calls, [("one",)])

    def test_state_persists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"; state = BotState(path); state.set("count", 2)
            self.assertEqual(BotState(path).get("count"), 2)


class MediaHealthTests(unittest.TestCase):
    def test_health_serializes(self) -> None:
        self.assertTrue(MediaHealth("bbb", True, packets_sent=1).to_dict()["connected"])
