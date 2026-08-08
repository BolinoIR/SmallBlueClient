"""Foreground status logging for SmallBlueClient."""
from __future__ import annotations

import logging
import json
from typing import Any, Union

LOGGER = logging.getLogger("sbc")


class StructuredFormatter(logging.Formatter):
    """One JSON record per line for bot runners and log collectors."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "logger": record.name,
            "level": record.levelname,
            "message": record.getMessage(),
        }
        details = getattr(record, "sbc_details", None)
        if details:
            payload["details"] = details
        return json.dumps(payload, default=str, separators=(",", ":"))


def enable_logging(level: Union[int, str] = logging.INFO, *, structured: bool = False) -> logging.Logger:
    """Configure foreground logs; ``structured=True`` emits JSON lines."""
    handlers = [handler for handler in LOGGER.handlers if getattr(handler, "_sbc_handler", False)]
    if not handlers:
        handler = logging.StreamHandler()
        handler._sbc_handler = True  # type: ignore[attr-defined]
        handler.setFormatter(
            StructuredFormatter()
            if structured
            else logging.Formatter("[%(asctime)s] [SBC %(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
        )
        LOGGER.addHandler(handler)
    elif structured:
        for handler in handlers:
            handler.setFormatter(StructuredFormatter())
    LOGGER.setLevel(level)
    LOGGER.propagate = False
    return LOGGER


def get_logger() -> logging.Logger:
    return enable_logging()


def debug_trace(event: str, /, **details: Any) -> None:
    """Emit non-secret structured DEBUG diagnostics for transport/media work."""
    LOGGER.debug(event, extra={"sbc_details": details})
