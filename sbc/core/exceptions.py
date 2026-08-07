"""Structured exceptions raised by SmallBlueClient."""
from __future__ import annotations

from typing import Any


class SBCError(Exception):
    """Base error with machine-readable context for retry/reporting code."""

    default_code = "sbc_error"

    def __init__(self, message: str = "", *, code: str | None = None,
                 operation: str | None = None, recoverable: bool = False,
                 context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.operation = operation
        self.recoverable = recoverable
        self.context = dict(context or {})

    def to_dict(self) -> dict[str, Any]:
        """Return safe structured error data suitable for a diagnostic report."""
        return {"type": type(self).__name__, "message": self.message,
                "code": self.code, "operation": self.operation,
                "recoverable": self.recoverable, "context": self.context}


class SessionError(SBCError):
    """An ``.sbc`` file is malformed, corrupt, expired, or unsupported."""
    default_code = "session_error"


class ConnectionError(SBCError):
    """A BBB GraphQL, WebSocket, or media endpoint could not be reached."""
    default_code = "connection_error"


class GraphQLError(SBCError):
    """BigBlueButton returned GraphQL errors."""
    default_code = "graphql_error"


class PermissionDenied(GraphQLError):
    """The saved BBB role cannot perform this action."""
    default_code = "permission_denied"


class MutationNotFound(SBCError):
    """The requested operation is not in SBC's BBB action registry."""
    default_code = "mutation_not_found"


class MutationValidationError(SBCError):
    """Mutation arguments do not match the BBB GraphQL action schema."""
    default_code = "mutation_validation_error"


class MediaStalledError(ConnectionError):
    """A connected media sender stopped advancing outbound RTP counters."""
    default_code = "media_stalled"
