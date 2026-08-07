"""Unit coverage for the repository capability diagnostic commands."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from examples.library_diagnostic import Diagnostic, _action_safety, _redact, load_action_plan, write_action_plan
from sbc.core.client import SBCClient
from sbc.core.session import SBCSession


class DiagnosticTests(unittest.TestCase):
    def test_generated_action_plan_covers_all_actions_and_starts_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "action-plan.json"
            write_action_plan(path)
            plan = json.loads(path.read_text(encoding="utf-8"))
            actions = load_action_plan(path)

        self.assertEqual(plan["version"], 1)
        self.assertEqual(len(actions), 109)
        self.assertTrue(all(not item["enabled"] for item in actions))
        ending = next(item for item in actions if item["name"] == "meetingEnd")
        self.assertEqual(ending["safety"], "excluded")

    def test_inventory_compiles_every_action_and_redacts_secrets(self) -> None:
        client = SBCClient(SBCSession(server="https://bbb.example", websocket_url="wss://bbb.example/graphql"), connect=False)
        diagnostic = Diagnostic(client)
        inventory = diagnostic.inventory_actions()

        self.assertEqual(len(inventory), 109)
        self.assertTrue(all(item["local_compile"]["ok"] for item in inventory))
        last_seen = next(item for item in inventory if item["name"] == "chatSetLastSeen")
        self.assertEqual([arg["name"] for arg in last_seen["arguments"]], ["chatId", "lastSeenAt"])
        self.assertEqual(_redact({"token": "secret", "Cookie": "browser", "normal": 1}), {"token": "<redacted>", "Cookie": "<redacted>", "normal": 1})
        self.assertEqual(_action_safety("meetingEnd"), "excluded")

    def test_plan_validation_rejects_invalid_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(SystemExit):
                load_action_plan(path)
