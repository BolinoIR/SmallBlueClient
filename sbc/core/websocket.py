"""GraphQL-over-WebSocket transport used by a captured BBB session."""
from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import websocket

from .exceptions import ConnectionError
from .logging import get_logger
from .session import SBCSession


class GraphQLWebSocket:
    """A reconnecting synchronous ``graphql-transport-ws`` transport.

    A transport is deliberately owned by one :class:`SBCClient`; sessions from
    separate browser tabs never share a socket, authentication, or operation id.
    """

    def __init__(self, session: SBCSession, *, timeout: float = 15, reconnects: int | None = None, max_reconnect_delay: float = 20, operation_timeout: float | None = None):
        self.session = session
        self.timeout = timeout
        self.reconnects = reconnects
        self.max_reconnect_delay = max_reconnect_delay
        self.operation_timeout = operation_timeout
        self._socket: websocket.WebSocket | None = None
        self._counter = 0
        self._lock = threading.RLock()
        self._closed = False
        self._last_pong = time.monotonic()
        # BBB treats X-ClientSessionUUID as a client connection identity. Reusing
        # the browser's UUID can cause a new Python socket to evict the browser
        # socket (or another SBC event socket). Every SBC transport gets one.
        self._connection_payload = json.loads(json.dumps(session.connection_payload or {}))
        payload_headers = self._connection_payload.setdefault("headers", {})
        if isinstance(payload_headers, dict):
            payload_headers["X-ClientSessionUUID"] = str(uuid.uuid4())

    @property
    def client_session_uuid(self) -> str:
        """The per-transport UUID BBB uses for connection-liveness reports."""
        headers = self._connection_payload.get("headers") or {}
        return str(headers.get("X-ClientSessionUUID", "0"))

    def connect(self) -> None:
        with self._lock:
            if self._closed:
                raise ConnectionError("transport has been closed")
            if self._socket and self._socket.connected:
                return
            headers = [f"{key}: {value}" for key, value in self.session.headers.items() if value]
            try:
                get_logger().info("Connecting to BBB GraphQL: %s", self.session.websocket_url)
                self._socket = websocket.create_connection(
                    self.session.websocket_url,
                    subprotocols=[self.session.protocol],
                    header=headers,
                    timeout=self.timeout,
                )
                self._send({"type": "connection_init", "payload": self._connection_payload})
                deadline = time.monotonic() + self.timeout
                while time.monotonic() < deadline:
                    message = self._receive()
                    if message.get("type") == "connection_ack":
                        # ``create_connection`` needs a finite timeout, but a
                        # quiet GraphQL subscription is healthy. Leaving that
                        # timeout in place causes false disconnect/reconnect
                        # loops whenever no participant changes for 15 seconds.
                        self._socket.settimeout(self.operation_timeout)
                        self._last_pong = time.monotonic()
                        get_logger().info("BBB GraphQL connected")
                        return
                    if message.get("type") == "ping":
                        self._send({"type": "pong", "payload": message.get("payload")})
                raise ConnectionError("GraphQL server did not acknowledge the connection")
            except (OSError, websocket.WebSocketException, ValueError) as exc:
                self._socket = None
                get_logger().warning("BBB GraphQL connection failed: %s", exc)
                raise ConnectionError(f"could not connect to BBB GraphQL: {exc}") from exc

    def close(self) -> None:
        self._closed = True
        with self._lock:
            if self._socket:
                try:
                    self._socket.close()
                finally:
                    self._socket = None

    def _send(self, message: dict[str, Any]) -> None:
        if not self._socket:
            raise ConnectionError("WebSocket is not connected")
        self._socket.send(json.dumps(message, separators=(",", ":")))

    def _receive(self) -> dict[str, Any]:
        if not self._socket:
            raise ConnectionError("WebSocket is not connected")
        raw = self._socket.recv()
        if not raw:
            raise ConnectionError("BBB GraphQL WebSocket closed")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConnectionError("BBB GraphQL returned invalid JSON") from exc

    def _next_id(self) -> str:
        self._counter += 1
        return str(self._counter)

    def _open_operation(self, query: str, variables: dict[str, Any]) -> str:
        self.connect()
        operation_id = self._next_id()
        self._send({"id": operation_id, "type": "subscribe", "payload": {"query": query, "variables": variables}})
        return operation_id

    def execute(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Run a query or mutation and wait for its first result."""
        attempts = 0
        while not self._closed:
            operation_id: str | None = None
            try:
                with self._lock:
                    operation_id = self._open_operation(query, variables)
                    while True:
                        message = self._receive()
                        kind = message.get("type")
                        if kind == "ping":
                            self._send({"type": "pong", "payload": message.get("payload")})
                            continue
                        if message.get("id") != operation_id:
                            continue
                        if kind == "next":
                            return message.get("payload", {})
                        if kind == "error":
                            return {"errors": message.get("payload", [])}
                        if kind == "complete":
                            return {}
            except (ConnectionError, OSError, websocket.WebSocketException):
                attempts += 1
                if self.reconnects is not None and attempts > self.reconnects:
                    raise ConnectionError("BBB GraphQL operation disconnected")
                with self._lock:
                    if self._socket: self._socket.close()
                    self._socket = None
                get_logger().warning("BBB GraphQL operation disconnected; reconnecting (%s)", attempts)
                time.sleep(min(2 ** min(attempts, 5), self.max_reconnect_delay))
            finally:
                if operation_id:
                    try:
                        with self._lock: self._send({"id": operation_id, "type": "complete"})
                    except ConnectionError:
                        pass
        raise ConnectionError("transport has been closed")

    def subscribe(self, query: str, variables: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Yield a live subscription, reconnecting it when BBB drops the socket."""
        attempts = 0
        while not self._closed:
            operation_id: str | None = None
            try:
                with self._lock:
                    operation_id = self._open_operation(query, variables)
                attempts = 0
                while not self._closed:
                    with self._lock:
                        message = self._receive()
                        kind = message.get("type")
                        if kind == "ping":
                            self._send({"type": "pong", "payload": message.get("payload")})
                            continue
                    if message.get("id") != operation_id:
                        continue
                    if kind == "next":
                        yield message.get("payload", {})
                    elif kind == "error":
                        yield {"errors": message.get("payload", [])}
                        return
                    elif kind == "complete":
                        return
            except (ConnectionError, OSError, websocket.WebSocketException):
                attempts += 1
                if (self.reconnects is not None and attempts > self.reconnects) or self._closed:
                    raise ConnectionError("BBB GraphQL subscription disconnected")
                with self._lock:
                    if self._socket:
                        self._socket.close()
                    self._socket = None
                get_logger().warning("BBB GraphQL subscription disconnected; reconnecting (%s)", attempts)
                time.sleep(min(2 ** min(attempts, 5), self.max_reconnect_delay))
            finally:
                if operation_id and self._socket:
                    try:
                        with self._lock:
                            self._send({"id": operation_id, "type": "complete"})
                    except ConnectionError:
                        pass


@dataclass(frozen=True, slots=True)
class Subscription:
    """One GraphQL subscription owned by :class:`SubscriptionMultiplexer`."""

    query: str
    variables: dict[str, Any]
    callback: Callable[[dict[str, Any]], None]


class SubscriptionMultiplexer:
    """Run many BBB GraphQL subscriptions through one authenticated socket.

    The graphql-transport-ws protocol supports multiple operation ids on a
    connection.  BBB bots used to open one socket per handler group, which can
    cause BBB to evict a session or repeatedly reconnect.  This dispatcher is
    deliberately synchronous: it is owned by ``SBCClient.run()`` and routes
    incoming frames to the registered stream callback by operation id.
    """

    def __init__(
        self,
        transport: GraphQLWebSocket,
        subscriptions: Sequence[Subscription],
        *,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self.transport = transport
        self.subscriptions = tuple(subscriptions)
        self.on_error = on_error
        self._operation_callbacks: dict[str, Callable[[dict[str, Any]], None]] = {}

    def close(self) -> None:
        self.transport.close()

    def run(self, stop: threading.Event) -> None:
        """Dispatch until ``stop`` is set, reconnecting every operation together."""
        delay = 1.0
        while not stop.is_set() and not self.transport._closed:
            try:
                self.transport.connect()
                self._open_all()
                delay = 1.0
                while not stop.is_set() and not self.transport._closed:
                    message = self.transport._receive()
                    kind = message.get("type")
                    if kind == "ping":
                        self.transport._send({"type": "pong", "payload": message.get("payload")})
                        continue
                    operation_id = message.get("id")
                    callback = self._operation_callbacks.get(operation_id)
                    if callback is None:
                        continue
                    if kind == "next":
                        try:
                            callback(message.get("payload", {}))
                        except Exception as exc:
                            self._error(exc)
                    elif kind == "error":
                        self._error(ConnectionError(f"BBB GraphQL subscription error: {message.get('payload', [])}"))
                    elif kind == "complete":
                        # A single completed subscription is reopened alongside
                        # the others, preserving one shared authenticated socket.
                        self._reopen(operation_id, callback)
            except (ConnectionError, OSError, websocket.WebSocketException) as exc:
                if stop.is_set() or self.transport._closed:
                    break
                self._error(ConnectionError(f"BBB GraphQL event connection disconnected: {exc}"))
                with self.transport._lock:
                    if self.transport._socket:
                        self.transport._socket.close()
                    self.transport._socket = None
                self._operation_callbacks.clear()
                get_logger().warning("BBB GraphQL event connection disconnected; reconnecting in %.0fs", delay)
                stop.wait(delay)
                delay = min(delay * 2, self.transport.max_reconnect_delay)
            finally:
                if stop.is_set() or self.transport._closed:
                    self._complete_all()

    def _open_all(self) -> None:
        self._operation_callbacks.clear()
        with self.transport._lock:
            for subscription in self.subscriptions:
                operation_id = self.transport._open_operation(subscription.query, subscription.variables)
                self._operation_callbacks[operation_id] = subscription.callback

    def _reopen(self, operation_id: str, callback: Callable[[dict[str, Any]], None]) -> None:
        self._operation_callbacks.pop(operation_id, None)
        subscription = next((item for item in self.subscriptions if item.callback is callback), None)
        if subscription is None or self.transport._closed:
            return
        with self.transport._lock:
            new_id = self.transport._open_operation(subscription.query, subscription.variables)
        self._operation_callbacks[new_id] = callback

    def _complete_all(self) -> None:
        with self.transport._lock:
            for operation_id in tuple(self._operation_callbacks):
                try:
                    self.transport._send({"id": operation_id, "type": "complete"})
                except ConnectionError:
                    break
            self._operation_callbacks.clear()

    def _error(self, error: Exception) -> None:
        if self.on_error is not None:
            try:
                self.on_error(error)
                return
            except Exception:
                get_logger().exception("SBC GraphQL event error handler failed")
        get_logger().warning("BBB GraphQL event stream failed: %s", error)
