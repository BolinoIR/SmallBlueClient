"""SmallBlueClient — community automation for BigBlueButton sessions."""
from pathlib import Path

from .core.client import SBCClient
from .asyncio import AsyncSBCClient
from .core.session import SBCSession, SessionHealth
from .bridge import SessionBridge
from .media import MediaConnectionError
from .core.logging import enable_logging
from .core.events import EVENTS
from .types import (BreakoutLifecycle, CaptionProvider, Event, GuestApproval, GuestPolicy,
                    Layout, MediaScope, MediaType, PollType, PresentationFileState, Reaction, Role)
from .models import (BreakoutRoom, Camera, Caption, Chat, ChatMessage, ExternalVideo,
                     Guest, LayoutState, LockSettings, MediaGroupParticipant,
                     MediaGroupState, Meeting, Notification, PluginDataEntry, Poll,
                     PollOption, Presentation, Recording, Screenshare, SharedNotesSession,
                     Timer, User, WhiteboardAnnotation, WhiteboardCursor,
                     PresentationDocument)
from .schema import BBBTable, SchemaCatalog, catalogs, schema
from .core.exceptions import SBCError, SessionError, ConnectionError, GraphQLError, PermissionDenied, MutationNotFound, MutationValidationError

__version__ = "0.1.1"

def client(session_file: str | Path, *, connect: bool = True, auto_join: bool = True, listen_only: bool = True) -> SBCClient:
    """Load an exported ``.sbc`` session and return an independent client."""
    return SBCClient.from_file(session_file, connect=connect, auto_join=auto_join, listen_only=listen_only)


def async_client(session_file: str | Path, *, auto_join: bool = True,
                 listen_only: bool = True) -> AsyncSBCClient:
    """Load a session for use with ``async with`` and awaitable controllers."""
    return AsyncSBCClient(session_file, auto_join=auto_join, listen_only=listen_only)

__all__ = [
    "client", "async_client", "SBCClient", "AsyncSBCClient", "SBCSession", "SessionHealth", "SessionBridge", "MediaConnectionError",
    "enable_logging", "EVENTS", "schema", "catalogs", "SchemaCatalog", "BBBTable",
    "Role", "GuestPolicy", "GuestApproval", "PollType", "MediaType", "MediaScope", "Event",
    "Layout", "CaptionProvider", "Reaction", "PresentationFileState", "BreakoutLifecycle", "User", "Meeting", "Chat", "ChatMessage",
    "Presentation", "PresentationDocument", "Poll", "PollOption", "Timer", "Camera", "Caption",
    "BreakoutRoom", "LockSettings", "Screenshare", "ExternalVideo", "Guest",
    "SharedNotesSession", "Recording", "WhiteboardAnnotation", "WhiteboardCursor",
    "Notification", "MediaGroupParticipant", "MediaGroupState", "PluginDataEntry",
    "LayoutState", "SBCError", "SessionError", "ConnectionError", "GraphQLError",
    "PermissionDenied", "MutationNotFound", "MutationValidationError",
]
