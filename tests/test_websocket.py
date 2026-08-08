import unittest
from unittest.mock import patch

from sbc.core.exceptions import ConnectionError
from sbc.core.session import SBCSession
from sbc.core.websocket import GraphQLWebSocket

class ReconnectingTransport(GraphQLWebSocket):
    def __init__(self):
        super().__init__(SBCSession(server="https://bbb.example", websocket_url="wss://bbb.example/graphql"), reconnects=None)
        self.opens = 0
    def _open_operation(self, query, variables):
        self.opens += 1
        if self.opens == 1: raise ConnectionError("temporary drop")
        return "1"
    def _receive(self): return {"id": "1", "type": "next", "payload": {"data": {"ok": True}}}
    def _send(self, message): pass

class WebSocketTests(unittest.TestCase):
    def test_each_transport_has_a_stable_connection_liveness_uuid(self):
        transport = GraphQLWebSocket(SBCSession(server="https://bbb.example", websocket_url="wss://bbb.example/graphql"))
        self.assertRegex(transport.client_session_uuid, r"^[0-9a-f-]{36}$")
        self.assertEqual(transport.client_session_uuid, transport.client_session_uuid)

    def test_execute_reconnects_after_a_temporary_disconnect(self):
        transport = ReconnectingTransport()
        with patch("sbc.core.websocket.time.sleep"):
            self.assertEqual(transport.execute("query { ok }", {}), {"data": {"ok": True}})
        self.assertEqual(transport.opens, 2)
