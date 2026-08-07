"""Native asyncio GraphQL-over-WebSocket transport for advanced SBC bots."""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import websockets

from ..core.exceptions import ConnectionError, GraphQLError
from ..core.logging import get_logger
from ..core.session import SBCSession


class AsyncGraphQLTransport:
    """Async ``graphql-transport-ws`` client using no worker threads.

    It is intended for raw queries, mutations, and custom subscriptions. SBC's
    high-level compatibility controllers remain available through
    :class:`AsyncSBCClient` while their operation mappings are shared with the
    synchronous client.
    """
    def __init__(self, session: SBCSession, *, timeout: float = 15,
                 reconnects: int | None = None, max_reconnect_delay: float = 20) -> None:
        self.session = session; self.timeout = timeout; self.reconnects = reconnects
        self.max_reconnect_delay = max_reconnect_delay; self._socket: Any = None
        self._counter = 0; self._closed = False; self._operation_lock = asyncio.Lock()
        self._connection_payload = json.loads(json.dumps(session.connection_payload or {}))
        headers = self._connection_payload.setdefault("headers", {})
        if isinstance(headers, dict): headers["X-ClientSessionUUID"] = str(uuid.uuid4())

    async def connect(self) -> None:
        if self._closed: raise ConnectionError("async transport has been closed")
        if self._socket is not None: return
        headers = [(key, value) for key, value in self.session.headers.items() if value]
        try:
            get_logger().info("Connecting to BBB GraphQL asynchronously: %s", self.session.websocket_url)
            self._socket = await websockets.connect(self.session.websocket_url,
                                                    subprotocols=[self.session.protocol],
                                                    extra_headers=headers,
                                                    ping_interval=15, ping_timeout=20,
                                                    close_timeout=2)
            await self._send({"type": "connection_init", "payload": self._connection_payload})
            while True:
                message = await self._receive(timeout=self.timeout)
                if message.get("type") == "connection_ack":
                    get_logger().info("BBB async GraphQL connected")
                    return
                if message.get("type") == "ping": await self._send({"type": "pong", "payload": message.get("payload")})
        except Exception as exc:
            self._socket = None
            raise ConnectionError(f"could not asynchronously connect to BBB GraphQL: {exc}", recoverable=True) from exc

    async def close(self) -> None:
        self._closed = True
        socket, self._socket = self._socket, None
        if socket is not None:
            await socket.close()

    async def _send(self, message: dict[str, Any]) -> None:
        if self._socket is None: raise ConnectionError("async WebSocket is not connected")
        await self._socket.send(json.dumps(message, separators=(",", ":")))

    async def _receive(self, *, timeout: float | None = None) -> dict[str, Any]:
        if self._socket is None: raise ConnectionError("async WebSocket is not connected")
        try:
            raw = await asyncio.wait_for(self._socket.recv(), timeout=timeout) if timeout else await self._socket.recv()
            if not raw: raise ConnectionError("BBB async GraphQL WebSocket closed", recoverable=True)
            return json.loads(raw)
        except (asyncio.TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise ConnectionError(f"BBB async GraphQL receive failed: {exc}", recoverable=True) from exc

    def _next_id(self) -> str:
        self._counter += 1; return str(self._counter)

    async def execute(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute one query/mutation with exponential reconnect handling."""
        async with self._operation_lock:
            attempt = 0
            while not self._closed:
                operation_id = None
                try:
                    await self.connect(); operation_id = self._next_id()
                    await self._send({"id": operation_id, "type": "subscribe", "payload": {"query": query, "variables": variables or {}}})
                    while True:
                        message = await self._receive(timeout=self.timeout)
                        if message.get("type") == "ping":
                            await self._send({"type": "pong", "payload": message.get("payload")}); continue
                        if message.get("id") != operation_id: continue
                        if message.get("type") == "next": return message.get("payload", {})
                        if message.get("type") == "error": return {"errors": message.get("payload", [])}
                        if message.get("type") == "complete": return {}
                except ConnectionError:
                    attempt += 1
                    if self.reconnects is not None and attempt > self.reconnects: raise
                    await self._reset(); await asyncio.sleep(min(2 ** min(attempt, 5), self.max_reconnect_delay))
                finally:
                    if operation_id and self._socket is not None:
                        try: await self._send({"id": operation_id, "type": "complete"})
                        except ConnectionError: pass
        raise ConnectionError("async transport has been closed")

    async def subscribe(self, query: str, variables: dict[str, Any] | None = None) -> AsyncIterator[dict[str, Any]]:
        """Yield one native async subscription, reconnecting after a drop.

        A subscription holds this transport's operation lock. Use a separate
        ``AsyncGraphQLTransport`` instance for independent custom streams.
        """
        async with self._operation_lock:
            attempt = 0
            while not self._closed:
                operation_id = None
                try:
                    await self.connect(); operation_id = self._next_id()
                    await self._send({"id": operation_id, "type": "subscribe", "payload": {"query": query, "variables": variables or {}}})
                    attempt = 0
                    while not self._closed:
                        message = await self._receive()
                        if message.get("type") == "ping":
                            await self._send({"type": "pong", "payload": message.get("payload")}); continue
                        if message.get("id") != operation_id: continue
                        if message.get("type") == "next": yield message.get("payload", {})
                        elif message.get("type") == "error": yield {"errors": message.get("payload", [])}; return
                        elif message.get("type") == "complete": return
                except ConnectionError:
                    attempt += 1
                    if self.reconnects is not None and attempt > self.reconnects: raise
                    await self._reset(); await asyncio.sleep(min(2 ** min(attempt, 5), self.max_reconnect_delay))
                finally:
                    if operation_id and self._socket is not None:
                        try: await self._send({"id": operation_id, "type": "complete"})
                        except ConnectionError: pass

    async def _reset(self) -> None:
        socket, self._socket = self._socket, None
        if socket is not None:
            try: await socket.close()
            except Exception: pass


class AsyncGraphQLClient:
    """Error-normalizing native async GraphQL facade."""
    def __init__(self, transport: AsyncGraphQLTransport) -> None: self.transport = transport
    @staticmethod
    def _data(response: dict[str, Any]) -> dict[str, Any]:
        if response.get("errors"):
            raise GraphQLError("; ".join(item.get("message", str(item)) if isinstance(item, dict) else str(item) for item in response["errors"]))
        return response.get("data", response)
    async def execute(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._data(await self.transport.execute(query, variables))
    async def mutation(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.execute(query, variables)
    async def subscribe(self, query: str, variables: dict[str, Any] | None = None) -> AsyncIterator[dict[str, Any]]:
        async for response in self.transport.subscribe(query, variables): yield self._data(response)
