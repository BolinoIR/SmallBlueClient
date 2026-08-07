"""Native asyncio facade for the synchronous SBC transport."""
from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Callable

from ..core.client import SBCClient
from ..types import Event, StringEnum


class AsyncEventStream(AsyncIterator[Any]):
    """An async iterator backed by an SBC event handler and an asyncio queue."""

    _CLOSE = object()

    def __init__(self, client: "AsyncSBCClient", event: str | StringEnum) -> None:
        self._client = client
        self._event = str(event)
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._handler: Callable[..., None] | None = None
        self._closed = False

    def __aiter__(self) -> "AsyncEventStream":
        return self

    async def __anext__(self) -> Any:
        if self._closed:
            raise StopAsyncIteration
        if self._handler is None:
            self._start()
        item = await self._queue.get()
        if item is self._CLOSE:
            raise StopAsyncIteration
        return item

    def _start(self) -> None:
        self._loop = asyncio.get_running_loop()

        def receive(*args: Any) -> None:
            value: Any = args[0] if len(args) == 1 else args
            if self._loop is not None and not self._closed:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, value)

        self._handler = receive
        self._client._sync.on(self._event, receive)
        self._client._start_event_worker()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._handler is not None:
            await asyncio.to_thread(self._client._sync.off, self._event, self._handler)
        await self._queue.put(self._CLOSE)


class AsyncEvents:
    """Create event iterators with ``bot.events.user_joined()`` syntax."""

    def __init__(self, client: "AsyncSBCClient") -> None:
        self._client = client

    def __getattr__(self, event: str) -> Callable[[], AsyncEventStream]:
        if event.startswith("_"):
            raise AttributeError(event)
        return lambda: AsyncEventStream(self._client, event)

    def __getitem__(self, event: str | Event) -> AsyncEventStream:
        return AsyncEventStream(self._client, event)


class _AsyncProxy:
    """Convert synchronous controller method calls into awaitable methods."""

    def __init__(self, target: Any) -> None:
        self._target = target

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._target, name)
        if not callable(value):
            if value.__class__.__module__.startswith("sbc."):
                return _AsyncProxy(value)
            return value

        async def call(*args: Any, **kwargs: Any) -> Any:
            return await asyncio.to_thread(value, *args, **kwargs)

        return call


class AsyncSBCClient:
    """Async context-manager and controller facade around :class:`SBCClient`."""

    def __init__(self, session_file: str | Path, *, auto_join: bool = True,
                 listen_only: bool = True) -> None:
        self._sync = SBCClient.from_file(
            session_file, connect=False, auto_join=auto_join, listen_only=listen_only,
        )
        # Expose the same read-only session metadata as SBCClient. This makes
        # self-filtering straightforward in async event loops.
        self.session = self._sync.session
        self.events = AsyncEvents(self)
        self._event_thread: threading.Thread | None = None
        self._event_thread_lock = threading.Lock()

        # Async wrappers intentionally mirror the synchronous public surface.
        self.chat = _AsyncProxy(self._sync.chat)
        self.users = _AsyncProxy(self._sync.users)
        self.meeting = _AsyncProxy(self._sync.meeting)
        self.presentation = self.presentations = _AsyncProxy(self._sync.presentation)
        self.media = _AsyncProxy(self._sync.media)
        self.polls = _AsyncProxy(self._sync.polls)
        self.breakouts = self.breakout_rooms = _AsyncProxy(self._sync.breakouts)
        self.cameras = _AsyncProxy(self._sync.cameras)
        self.captions = _AsyncProxy(self._sync.captions)
        self.notes = self.shared_notes = _AsyncProxy(self._sync.notes)
        self.recording = self.recordings = _AsyncProxy(self._sync.recording)
        self.whiteboard = self.whiteboards = _AsyncProxy(self._sync.whiteboard)
        self.guests = self.guest_lobby = _AsyncProxy(self._sync.guests)
        self.timer = self.timers = _AsyncProxy(self._sync.timer)
        self.external_video = self.external_videos = _AsyncProxy(self._sync.external_video)
        self.plugins = _AsyncProxy(self._sync.plugins)
        self.media_groups = _AsyncProxy(self._sync.media_groups)
        self.settings = _AsyncProxy(self._sync.settings)
        self.locks = _AsyncProxy(self._sync.locks)
        self.screenshare = _AsyncProxy(self._sync.screenshare)
        self.reactions = _AsyncProxy(self._sync.reactions)

    async def __aenter__(self) -> "AsyncSBCClient":
        await asyncio.to_thread(self._sync.connect)
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await asyncio.to_thread(self._sync.close)
        thread = self._event_thread
        if thread is not None:
            await asyncio.to_thread(thread.join, 2)

    async def mutation(self, name: str, /, **variables: Any) -> dict[str, Any]:
        return await asyncio.to_thread(self._sync.mutation, name, **variables)

    async def watch_table(self, *args: Any, **kwargs: Any) -> str:
        return await asyncio.to_thread(self._sync.watch_table, *args, **kwargs)

    def _start_event_worker(self) -> None:
        with self._event_thread_lock:
            if self._event_thread is not None and self._event_thread.is_alive():
                return
            self._event_thread = threading.Thread(
                target=self._sync.run,
                daemon=True,
                name="sbc-async-events",
            )
            self._event_thread.start()
