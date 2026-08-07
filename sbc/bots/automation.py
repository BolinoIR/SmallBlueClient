"""Small, opt-in building blocks for command-driven SBC bots."""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.logging import get_logger
from ..models import ChatMessage


class Rule:
    """A reusable filtered event rule."""
    def __init__(self, event: str, action: Callable[..., Any], *, when: Callable[..., bool] | None = None):
        self.event, self.action, self.when = event, action, when
    def install(self, client: Any) -> "Rule":
        @client.on(self.event)
        def run(*args: Any) -> None:
            if self.when is None or self.when(*args): self.action(*args)
        return self


class BotState:
    """Tiny JSON-backed state store for cooldowns, counters, and bot settings."""
    def __init__(self, path: str | Path = ".sbc-bot-state.json") -> None:
        self.path = Path(path); self._data: dict[str, Any] = {}
        if self.path.is_file():
            try: self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError): self._data = {}
    def get(self, key: str, default: Any = None) -> Any: return self._data.get(key, default)
    def set(self, key: str, value: Any) -> None: self._data[key] = value; self.save()
    def delete(self, key: str) -> bool:
        if key not in self._data: return False
        del self._data[key]; self.save(); return True
    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


@dataclass(frozen=True, slots=True)
class CommandContext:
    """Parsed command invocation supplied to a :class:`Bot` command."""
    bot: "Bot"
    message: ChatMessage
    name: str
    arguments: tuple[str, ...]
    def reply(self, text: str) -> dict[str, Any]: return self.bot.client.chat.reply(self.message, text)


@dataclass(slots=True)
class _Command:
    callback: Callable[[CommandContext], Any]
    cooldown: float
    permission: Callable[[CommandContext], bool] | None
    last_call: dict[str, float]


class Bot:
    """Command/task layer over an existing :class:`~sbc.SBCClient`."""
    def __init__(self, client: Any, *, prefix: str = "!", state_path: str | Path = ".sbc-bot-state.json") -> None:
        self.client = client; self.prefix = prefix; self.state = BotState(state_path)
        self._commands: dict[str, _Command] = {}; self._tasks: list[threading.Event] = []; self._started = False
    def command(self, name: str | None = None, *, cooldown: float = 0,
                permission: Callable[[CommandContext], bool] | None = None):
        """Register a chat command. The handler receives a ``CommandContext``."""
        if cooldown < 0: raise ValueError("cooldown must be non-negative")
        def register(callback: Callable[[CommandContext], Any]):
            self._commands[(name or callback.__name__).lower()] = _Command(callback, cooldown, permission, {})
            return callback
        return register
    def task(self, *, interval: float, run_immediately: bool = False):
        """Register a daemon periodic task, stopped by :meth:`close`."""
        if interval <= 0: raise ValueError("interval must be positive")
        def register(callback: Callable[[], Any]):
            stop = threading.Event(); self._tasks.append(stop)
            def worker() -> None:
                if not run_immediately: stop.wait(interval)
                while not stop.is_set():
                    try: callback()
                    except Exception: get_logger().exception("SBC bot periodic task failed")
                    stop.wait(interval)
            threading.Thread(target=worker, daemon=True, name=f"sbc-bot-task-{callback.__name__}").start()
            return callback
        return register
    @staticmethod
    def moderator(context: CommandContext) -> bool:
        """Permission predicate allowing only a current BBB moderator."""
        return any(user.id == context.message.sender_id and user.is_moderator for user in context.bot.client.users.list())
    def start(self) -> "Bot":
        if self._started: return self
        self._started = True
        @self.client.on("chat_message")
        def receive(message: ChatMessage) -> None: self._dispatch(message)
        self._receive = receive
        return self
    def _dispatch(self, message: ChatMessage) -> None:
        if message.sender_id == self.client.session.user_id or not message.text.startswith(self.prefix): return
        words = message.text[len(self.prefix):].strip().split()
        if not words: return
        name, arguments = words[0].lower(), tuple(words[1:]); command = self._commands.get(name)
        if command is None: return
        context = CommandContext(self, message, name, arguments)
        if command.permission is not None and not command.permission(context): return
        now = time.monotonic(); key = message.sender_id or message.sender_name
        if now - command.last_call.get(key, float("-inf")) < command.cooldown: return
        command.last_call[key] = now
        try:
            result = command.callback(context)
            if hasattr(result, "__await__"):
                import asyncio; threading.Thread(target=lambda: asyncio.run(result), daemon=True).start()
        except Exception: get_logger().exception("SBC bot command %s failed", name)
    def close(self) -> None:
        for stop in self._tasks: stop.set()
        self._tasks.clear()
        if self._started: self.client.off("chat_message", self._receive); self._started = False
        self.state.save()
