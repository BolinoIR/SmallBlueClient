from __future__ import annotations
from collections.abc import Callable
from typing import Any

class Rule:
    """A tiny reusable event rule for SBC bots."""
    def __init__(self, event: str, action: Callable[..., Any], *, when: Callable[..., bool] | None = None):
        self.event, self.action, self.when = event, action, when
    def install(self, client: Any) -> "Rule":
        @client.on(self.event)
        def run(*args: Any) -> None:
            if self.when is None or self.when(*args): self.action(*args)
        return self
