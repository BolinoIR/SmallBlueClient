"""Stable, readable values used by BigBlueButton controller methods."""
from __future__ import annotations

from enum import Enum


class StringEnum(str, Enum):
    """An enum that serializes directly into BBB GraphQL variables."""
    def __str__(self) -> str:
        return self.value


class Role(StringEnum):
    VIEWER = "VIEWER"
    MODERATOR = "MODERATOR"


class GuestPolicy(StringEnum):
    ALWAYS_ACCEPT = "ALWAYS_ACCEPT"
    ALWAYS_DENY = "ALWAYS_DENY"
    ASK_MODERATOR = "ASK_MODERATOR"
    ALWAYS_ACCEPT_AUTH = "ALWAYS_ACCEPT_AUTH"


class GuestApproval(StringEnum):
    APPROVE = "APPROVE"
    DENY = "DENY"


class PollType(StringEnum):
    YES_NO = "YN"
    YES_NO_ABSTENTION = "YNA"
    TRUE_FALSE = "TF"
    LETTER_CHOICES = "A-"
    MULTIPLE_CHOICE = "A-2"
    MULTIPLE_RESPONSE = "A-2"
    NUMBER_CHOICES = "1-"
    OPEN_RESPONSE = "R-"
    TYPED_ANSWER = "CUSTOM"

    @staticmethod
    def letters(count: int) -> str:
        """Return BBB's source-defined letter poll type (for example ``A-4``)."""
        if count < 2:
            raise ValueError("a BBB letter poll needs at least two choices")
        return f"A-{count}"

    @staticmethod
    def numbers(count: int) -> str:
        """Return BBB's source-defined numeric poll type (for example ``1-4``)."""
        if count < 2:
            raise ValueError("a BBB numeric poll needs at least two choices")
        return f"1-{count}"


class MediaType(StringEnum):
    AUDIO = "audio"
    VIDEO = "video"


class MediaScope(StringEnum):
    ROOM = "room"
    USER = "user"


class Layout(StringEnum):
    UNIFIED = "UNIFIED_LAYOUT"
    SMART = "SMART_LAYOUT"
    PRESENTATION_FOCUS = "PRESENTATION_FOCUS"
    VIDEO_FOCUS = "VIDEO_FOCUS"
    CUSTOM = "CUSTOM_LAYOUT"


class RecordingStatus(StringEnum):
    STARTED = "started"
    STOPPED = "stopped"


class CaptionProvider(StringEnum):
    DEFAULT = "default"
    GLADIA = "gladia"


class Reaction(StringEnum):
    """Source-defined user activity signs accepted by BBB."""

    RAISE_HAND = "raiseHand"
    APPLAUSE = "applause"
    THUMBS_UP = "thumbsUp"
    THUMBS_DOWN = "thumbsDown"
    HAPPY = "happy"
    NEUTRAL = "neutral"
    SAD = "sad"
    CONFUSED = "confused"
    AWAY = "away"


class PresentationFileState(StringEnum):
    """BBB presentation conversion states used by export/download controls."""

    UPLOADED = "UPLOADED"
    CONVERTED = "CONVERTED"
    PUBLISHED = "PUBLISHED"


class BreakoutLifecycle(StringEnum):
    """Stable lifecycle event names emitted for breakout rooms."""

    CREATED = "breakout_created"
    STARTED = "breakout_started"
    UPDATED = "breakout_updated"
    ENDED = "breakout_ended"


class Event(StringEnum):
    """Common high-level SBC events for clean, typo-resistant handlers."""

    USER_JOINED = "user_joined"
    USER_LEFT = "user_left"
    USER_TALKING = "user_talking"
    USER_STOPPED_TALKING = "user_stopped_talking"
    CHAT_MESSAGE = "chat_message"
    HAND_RAISED = "hand_raised"
    VOICE_JOINED = "voice_joined"
    PRESENTATION_CHANGED = "presentation_changed"
    MEETING_ENDED = "meeting_ended"
    POLL_UPDATED = "poll_updated"
    TIMER_UPDATED = "timer_updated"
    BREAKOUT_CREATED = BreakoutLifecycle.CREATED.value
    BREAKOUT_STARTED = BreakoutLifecycle.STARTED.value
    BREAKOUT_UPDATED = BreakoutLifecycle.UPDATED.value
    BREAKOUT_ENDED = BreakoutLifecycle.ENDED.value
    PLUGIN_DATA = "plugin_data"
    ERROR = "error"


def enum_value(value: StringEnum | str) -> str:
    """Return a GraphQL-safe string from an SBC enum or raw BBB value."""
    return value.value if isinstance(value, StringEnum) else value
