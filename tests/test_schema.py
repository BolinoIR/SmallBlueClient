import unittest
from pathlib import Path

import sbc
from sbc.core.client import SBCClient
from sbc.schema import BBB_TABLE_NAMES, TABLE_EVENTS
from sbc.core.session import SBCSession


class SchemaTests(unittest.TestCase):
    def test_every_embedded_bbb_table_has_an_enum_and_event_name(self):
        self.assertEqual(len(BBB_TABLE_NAMES), len(sbc.BBBTable))
        self.assertEqual(len(BBB_TABLE_NAMES), len(TABLE_EVENTS))
        query = sbc.schema.subscription(sbc.BBBTable.NOTIFICATION, "messageId notificationType")
        self.assertIn("notification{messageId notificationType}", query)

    def test_watch_table_turns_a_source_table_into_an_event(self):
        client = SBCClient(
            SBCSession(server="https://bbb.example", websocket_url="wss://bbb.example/graphql"),
            connect=False,
        )
        received = []
        event = client.watch_table(sbc.BBBTable.NOTIFICATION, "messageId", event="notice")
        client.on(event, received.append)
        _, _, handler = client._custom_streams[0]
        handler({"notification": [{"messageId": "n1"}]})
        self.assertEqual(received, [[{"messageId": "n1"}]])

    def test_versioned_catalogs_parse_old_or_future_bbb_schema_source(self):
        source = Path(__file__).resolve().parent.parent / "bigbluebutton-3.0.32" / "bbb-graphql-server" / "bbb-graphql-schema.md"
        catalog = sbc.SchemaCatalog.from_markdown(source, version="2.7")
        self.assertEqual(catalog.version, "2.7")
        self.assertIn("notification", catalog.table_names)
        self.assertIn("messageId", catalog.fields("notification"))
        self.assertIn("notification{createdAt icon", catalog.subscription("notification"))
