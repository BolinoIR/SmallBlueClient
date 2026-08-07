"""Resilient event registration and dispatch for SBC bots."""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from itertools import count
from typing import Any

from ..schema import TABLE_EVENTS

EventHandler = Callable[..., Any]

EVENTS = (
    "user_joined", "user_left", "user_updated", "user_changed", "hand_raised", "hand_lowered",
    "voice_joined", "voice_left", "user_talking", "user_stopped_talking", "user_muted", "user_unmuted",
    "user_became_presenter", "user_stopped_presenting", "user_became_moderator", "user_stopped_moderating",
    "user_away", "user_back", "user_disconnected", "user_reconnected", "camera_started", "camera_stopped",
    "chat_message", "chat_updated", "public_chat_updated", "private_chat_updated", "presentation_changed",
    "meeting_updated", "meeting_ended", "screenshare_started", "screenshare_stopped", "external_video_started",
    "external_video_stopped", "poll_updated", "poll_published", "poll_ended", "poll_results_changed",
    "timer_updated", "timer_started", "timer_stopped", "timer_elapsed", "current_user_updated",
    "current_user_joined", "current_user_left", "current_user_ejected", "action_started", "action_completed",
    "action_failed", "breakout_created", "breakout_started", "breakout_ended", "breakout_updated", "plugin_data", "error",
) + TABLE_EVENTS


@dataclass(slots=True)
class _Handler:
    callback: EventHandler
    priority: int
    sequence: int
    when: EventHandler | None = None
    once: bool = False


class EventEmitter:
    """Event API with removal, one-shot handlers, filters, priorities and async callbacks."""
    def __init__(self) -> None:
        self._handlers: dict[str, list[_Handler]] = defaultdict(list)
        self._handler_sequence = count()
        self._event_log = logging.getLogger("sbc.events")

    def on(self, event: str, handler: EventHandler | None = None, *, priority: int = 0, when: EventHandler | None = None):
        def register(callback: EventHandler) -> EventHandler:
            if not callable(callback): raise TypeError("event handler must be callable")
            self._handlers[event].append(_Handler(callback, priority, next(self._handler_sequence), when))
            return callback
        return register if handler is None else register(handler)

    def once(self, event: str, handler: EventHandler | None = None, *, priority: int = 0, when: EventHandler | None = None):
        def register(callback: EventHandler) -> EventHandler:
            if not callable(callback): raise TypeError("event handler must be callable")
            self._handlers[event].append(_Handler(callback, priority, next(self._handler_sequence), when, once=True))
            return callback
        return register if handler is None else register(handler)

    def off(self, event: str, handler: EventHandler | None = None) -> int:
        """Remove one handler, or all handlers for an event. Return count removed."""
        records = self._handlers.get(event, [])
        if handler is None:
            removed = len(records); self._handlers.pop(event, None); return removed
        kept = [record for record in records if record.callback is not handler]
        removed = len(records) - len(kept)
        if kept: self._handlers[event] = kept
        else: self._handlers.pop(event, None)
        return removed

    def emit(self, event: str, *args: Any) -> None:
        records = sorted(tuple(self._handlers.get(event, ())), key=lambda item: (-item.priority, item.sequence))
        for record in records:
            try:
                if record.when is not None and not record.when(*args):
                    continue
                result = record.callback(*args)
                if inspect.isawaitable(result):
                    self._run_async(result, event)
                if record.once:
                    self.off(event, record.callback)
            except Exception as exc:
                self._report_handler_error(event, record.callback, exc)

    def _run_async(self, coroutine: Any, event: str) -> None:
        def run() -> None:
            try: asyncio.run(coroutine)
            except Exception as exc: self._report_handler_error(event, None, exc)
        import threading
        threading.Thread(target=run, daemon=True, name="sbc-event-handler").start()

    def _report_handler_error(self, event: str, callback: EventHandler | None, error: Exception) -> None:
        self._event_log.error(
            "SBC handler failed for %s%s",
            event,
            f" ({getattr(callback, '__name__', 'handler')})" if callback else "",
            exc_info=(type(error), error, error.__traceback__),
        )
        if event != "error":
            # An error handler is isolated too; it cannot kill an event stream.
            for record in tuple(self._handlers.get("error", ())):
                try: record.callback(error)
                except Exception: self._event_log.exception("SBC error handler failed")

    @property
    def event_names(self) -> tuple[str, ...]:
        return EVENTS
