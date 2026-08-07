import asyncio
import unittest
from unittest.mock import patch

from sbc.asyncio import AsyncSBCClient
from sbc.core.client import SBCClient
from sbc.core.session import SBCSession


class Transport:
    def __init__(self):
        self.last_query = None
        self.last_variables = None

    def execute(self, query, variables):
        self.last_query = query
        self.last_variables = variables
        return {"data": {"meeting": []}}


class AsyncClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_context_chat_and_event_iterator(self):
        sync = SBCClient(SBCSession(server="https://bbb.example", websocket_url="wss://bbb.example/graphql"), connect=False)
        sync.graphql.transport = Transport()
        sync.connect = lambda: sync  # type: ignore[method-assign]
        sync.run = lambda: sync._stop.wait()  # type: ignore[method-assign]

        with patch("sbc.asyncio.SBCClient.from_file", return_value=sync):
            async with AsyncSBCClient("ignored.sbc") as bot:
                await bot.chat.send("hello")
                self.assertEqual(sync.graphql.transport.last_variables["chatId"], "MAIN-PUBLIC-GROUP-CHAT")
                event = bot.events.user_joined()
                pending = asyncio.create_task(event.__anext__())
                await asyncio.sleep(0.05)
                sync.emit("user_joined", "Ada")
                self.assertEqual(await asyncio.wait_for(pending, 1), "Ada")
                await event.aclose()
