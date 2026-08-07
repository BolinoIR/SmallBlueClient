from __future__ import annotations

import asyncio
import unittest

from sbc.asyncio.transport import AsyncGraphQLClient, AsyncGraphQLTransport
from sbc.core.exceptions import GraphQLError
from sbc.core.session import SBCSession


class AsyncTransportTests(unittest.TestCase):
    def transport(self) -> AsyncGraphQLTransport:
        return AsyncGraphQLTransport(SBCSession(
            server="https://bbb.example", websocket_url="wss://bbb.example/graphql",
            connection_payload={"headers": {"X-ClientSessionUUID": "browser-id"}},
        ))

    def test_transport_generates_independent_operation_and_client_identity(self) -> None:
        transport = self.transport()
        self.assertEqual((transport._next_id(), transport._next_id()), ("1", "2"))
        self.assertNotEqual(transport._connection_payload["headers"]["X-ClientSessionUUID"], "browser-id")

    def test_async_client_normalizes_graphql_errors(self) -> None:
        async def run() -> None:
            with self.assertRaises(GraphQLError):
                AsyncGraphQLClient._data({"errors": [{"message": "nope"}]})
        asyncio.run(run())
