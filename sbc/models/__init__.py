from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True, slots=True)
class User:
    id: str
    name: str
    is_moderator: bool = False
    is_presenter: bool = False
    in_meeting: bool = True
    hand_raised: bool = False
    voice_joined: bool = False
    muted: bool = False
    talking: bool = False
    meeting_id: str | None = None
    external_id: str | None = None
    role: str | None = None
    color: str | None = None
    avatar: str | None = None
    away: bool = False
    reaction_emoji: str | None = None
    pinned: bool = False
    locked: bool = False
    authed: bool = False
    mobile: bool = False
    bot: bool = False
    guest: bool = False
    client_type: str | None = None
    disconnected: bool = False
    logged_out: bool = False
    whiteboard_write_access: bool = False
    is_dial_in: bool = False
    deafened: bool = False
    listen_only: bool = False
    voice_user_id: str | None = None
    listen_only_input_device: bool = False
    camera_stream_ids: tuple[str, ...] = ()

    @classmethod
    def from_graphql(cls, value: dict[str, Any]) -> "User":
        voice = value.get("voice") or {}
        cameras = value.get("cameras") or []
        return cls(
            id=value.get("userId", value.get("id", "")), name=value.get("name", ""),
            is_moderator=bool(value.get("isModerator", False)), is_presenter=bool(value.get("presenter", value.get("isPresenter", False))),
            in_meeting=bool(value.get("currentlyInMeeting", True)), hand_raised=bool(value.get("raiseHand", False)),
            voice_joined=bool(voice.get("joined", False)), muted=bool(voice.get("muted", False)), talking=bool(voice.get("talking", False)),
            meeting_id=value.get("meetingId"), external_id=value.get("extId"), role=value.get("role"), color=value.get("color"), avatar=value.get("avatar"),
            away=bool(value.get("away", False)), reaction_emoji=value.get("reactionEmoji"), pinned=bool(value.get("pinned", False)), locked=bool(value.get("locked", False)),
            authed=bool(value.get("authed", False)), mobile=bool(value.get("mobile", False)), bot=bool(value.get("bot", False)), guest=bool(value.get("guest", False)),
            client_type=value.get("clientType"), disconnected=bool(value.get("disconnected", False)), logged_out=bool(value.get("loggedOut", False)),
            whiteboard_write_access=bool(value.get("whiteboardWriteAccess", False)), is_dial_in=bool(value.get("isDialIn", False)),
            deafened=bool(voice.get("deafened", False)), listen_only=bool(voice.get("listenOnly", False)), voice_user_id=voice.get("voiceUserId"),
            listen_only_input_device=bool(voice.get("listenOnlyInputDevice", False)), camera_stream_ids=tuple(camera.get("streamId") for camera in cameras if camera.get("streamId")),
        )

    @classmethod
    def from_voice_activity(cls, value: dict[str, Any]) -> "User":
        """Create a user from BBB's ``user_voice_activity_stream`` event."""
        user = value.get("user") or {}
        return cls(
            id=value.get("userId", ""),
            name=user.get("name", ""),
            voice_joined=not bool(value.get("leftVoiceConf", False)),
            muted=bool(value.get("muted", False)),
            talking=bool(value.get("talking", False)),
        )

@dataclass(frozen=True, slots=True)
class ChatMessage:
    id: str
    text: str
    sender_name: str
    sender_id: str | None = None
    chat_id: str | None = None
    created_at: str | None = None

    @classmethod
    def from_graphql(cls, value: dict[str, Any]) -> "ChatMessage":
        user = value.get("user") or {}
        return cls(value.get("messageId", ""), value.get("message", ""), value.get("senderName", user.get("name", "")), user.get("userId"), value.get("chatId"), value.get("createdAt"))


@dataclass(frozen=True, slots=True)
class Chat:
    """A BBB public or private chat channel."""

    id: str
    public: bool = False
    participant_ids: tuple[str, ...] = ()

    @classmethod
    def from_graphql(cls, value: dict[str, Any]) -> "Chat":
        participants = value.get("users") or value.get("userIds") or []
        return cls(
            id=value.get("chatId", ""),
            public=bool(value.get("public", False)),
            participant_ids=tuple(
                item.get("userId", item) if isinstance(item, dict) else item
                for item in participants
            ),
        )

@dataclass(frozen=True, slots=True)
class Presentation:
    presentation_id: str
    page_id: str
    page_number: int | None = None

    @classmethod
    def from_graphql(cls, value: dict[str, Any]) -> "Presentation":
        return cls(value.get("presentationId", ""), value.get("pageId", ""), value.get("num"))


@dataclass(frozen=True, slots=True)
class PresentationDocument:
    """A BBB uploaded presentation and its source-backed download metadata."""

    id: str
    name: str
    downloadable: bool = False
    download_url: str | None = None
    current: bool = False
    upload_completed: bool = False
    total_pages: int = 0

    @classmethod
    def from_graphql(cls, value: dict[str, Any]) -> "PresentationDocument":
        return cls(
            id=value.get("presentationId", ""),
            name=value.get("name", ""),
            downloadable=bool(value.get("downloadable", False)),
            download_url=value.get("downloadFileUri"),
            current=bool(value.get("current", False)),
            upload_completed=bool(value.get("uploadCompleted", False)),
            total_pages=int(value.get("totalPages", 0) or 0),
        )

@dataclass(frozen=True, slots=True)
class Meeting:
    id: str | None
    name: str | None
    ended: bool = False


@dataclass(frozen=True, slots=True)
class PollOption:
    id: str
    text: str
    responses: int = 0

    @classmethod
    def from_graphql(cls, value: dict[str, Any]) -> "PollOption":
        return cls(value.get("optionId", ""), value.get("optionDesc", ""), int(value.get("optionResponsesCount", 0)))


@dataclass(frozen=True, slots=True)
class Poll:
    id: str
    question: str
    type: str | None = None
    published: bool = False
    ended: bool = False
    options: tuple[PollOption, ...] = ()

    @classmethod
    def from_graphql(cls, value: dict[str, Any]) -> "Poll":
        rows = value.get("responses") or value.get("options") or []
        return cls(value.get("pollId", ""), value.get("questionText", ""), value.get("type"), bool(value.get("published")), bool(value.get("ended")), tuple(PollOption.from_graphql(row) for row in rows))


@dataclass(frozen=True, slots=True)
class Timer:
    active: bool = False
    running: bool = False
    seconds: int = 0
    elapsed: bool = False
    stopwatch: bool = False
    track: str | None = None

    @classmethod
    def from_graphql(cls, value: dict[str, Any]) -> "Timer":
        return cls(bool(value.get("active")), bool(value.get("running")), int(value.get("time", 0)), bool(value.get("elapsed")), bool(value.get("stopwatch")), value.get("songTrack"))


@dataclass(frozen=True, slots=True)
class Camera:
    stream_id: str
    user_id: str | None = None
    content: bool = False

    @classmethod
    def from_graphql(cls, value: dict[str, Any]) -> "Camera":
        return cls(value.get("streamId", ""), value.get("userId"), bool(value.get("showAsContent", False)))


@dataclass(frozen=True, slots=True)
class Caption:
    transcript_id: str
    text: str
    locale: str
    start: int = 0
    end: int = 0
    final: bool = False

    @classmethod
    def from_graphql(cls, value: dict[str, Any]) -> "Caption":
        return cls(
            transcript_id=value.get("transcriptId", value.get("captionId", "")),
            text=value.get("text", value.get("transcript", value.get("captionText", ""))),
            locale=value.get("locale", ""),
            start=int(value.get("start", 0)),
            end=int(value.get("end", 0)),
            final=bool(value.get("isFinal", False)),
        )


@dataclass(frozen=True, slots=True)
class BreakoutRoom:
    name: str
    sequence: int
    users: tuple[str, ...] = ()
    short_name: str | None = None
    free_join: bool = False

    @classmethod
    def from_graphql(cls, value: dict[str, Any]) -> "BreakoutRoom":
        return cls(
            name=value.get("name", ""),
            sequence=int(value.get("sequence", 0)),
            short_name=value.get("shortName"),
            free_join=bool(value.get("freeJoin", False)),
        )

    def input(self) -> dict[str, Any]:
        """Return BBB's real ``BreakoutRoom`` GraphQL input object."""
        return {
            "captureNotesFilename": "", "captureSlidesFilename": "", "freeJoin": self.free_join,
            "isDefaultName": False, "name": self.name, "sequence": self.sequence,
            "shortName": self.short_name, "users": list(self.users),
        }


@dataclass(frozen=True, slots=True)
class LockSettings:
    disable_camera: bool = False
    disable_microphone: bool = False
    disable_notes: bool = False
    disable_private_chat: bool = False
    disable_public_chat: bool = False
    hide_user_list: bool = False
    lock_on_join: bool = False
    lock_on_join_configurable: bool = False
    hide_viewers_cursor: bool = False
    hide_viewers_annotation: bool = False

    @classmethod
    def from_graphql(cls, value: dict[str, Any]) -> "LockSettings":
        return cls(
            disable_camera=bool(value.get("disableCam", False)),
            disable_microphone=bool(value.get("disableMic", False)),
            disable_notes=bool(value.get("disableNotes", False)),
            disable_private_chat=bool(value.get("disablePrivChat", value.get("disablePrivateChat", False))),
            disable_public_chat=bool(value.get("disablePubChat", value.get("disablePublicChat", False))),
            hide_user_list=bool(value.get("hideUserList", False)),
            lock_on_join=bool(value.get("lockOnJoin", False)),
            lock_on_join_configurable=bool(value.get("lockOnJoinConfigurable", False)),
            hide_viewers_cursor=bool(value.get("hideViewersCursor", False)),
            hide_viewers_annotation=bool(value.get("hideViewersAnnotation", False)),
        )

    def input(self) -> dict[str, bool]:
        """Return the complete BBB ``meetingLockSettingsSetProps`` input."""
        return {
            "disableCam": self.disable_camera,
            "disableMic": self.disable_microphone,
            "disablePrivChat": self.disable_private_chat,
            "disablePubChat": self.disable_public_chat,
            "disableNotes": self.disable_notes,
            "hideUserList": self.hide_user_list,
            "lockOnJoin": self.lock_on_join,
            "lockOnJoinConfigurable": self.lock_on_join_configurable,
            "hideViewersCursor": self.hide_viewers_cursor,
            "hideViewersAnnotation": self.hide_viewers_annotation,
        }


@dataclass(frozen=True, slots=True)
class Screenshare:
    stream: str | None = None
    started_at: str | None = None
    has_audio: bool = False

    @classmethod
    def from_graphql(cls, value: dict[str, Any]) -> "Screenshare":
        return cls(value.get("stream"), value.get("startedAt"), bool(value.get("hasAudio")))


@dataclass(frozen=True, slots=True)
class ExternalVideo:
    id: str | None = None
    url: str | None = None
    playing: bool = False
    current_time: float = 0.0

    @classmethod
    def from_graphql(cls, value: dict[str, Any]) -> "ExternalVideo":
        return cls(value.get("externalVideoId"), value.get("externalVideoUrl"), bool(value.get("playerPlaying")), float(value.get("playerCurrentTime", 0.0)))


@dataclass(frozen=True, slots=True)
class WhiteboardCursor:
    whiteboard_id: str
    x_percent: float
    y_percent: float


@dataclass(frozen=True, slots=True)
class WhiteboardAnnotation:
    page_id: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Notification:
    id: str
    type: str
    description: str | None = None
    role: str | None = None

    @classmethod
    def from_graphql(cls, value: dict[str, Any]) -> "Notification":
        return cls(value.get("messageId", ""), value.get("notificationType", ""), value.get("messageDescription"), value.get("role"))


@dataclass(frozen=True, slots=True)
class SharedNotesSession:
    """Shared-notes session state exposed by BBB's ``sharedNotes_session`` table."""

    external_id: str
    pinned: bool = False
    url: str | None = None

    @classmethod
    def from_graphql(cls, value: dict[str, Any]) -> "SharedNotesSession":
        return cls(
            external_id=value.get("sharedNotesExtId", value.get("externalId", "")),
            pinned=bool(value.get("pinned", False)),
            url=value.get("url"),
        )


@dataclass(frozen=True, slots=True)
class Recording:
    """Meeting recording state from BBB's ``meeting_recording`` table."""

    recording: bool = False
    record_full_duration_media: bool = False
    started_at: str | None = None
    stopped_at: str | None = None

    @classmethod
    def from_graphql(cls, value: dict[str, Any]) -> "Recording":
        return cls(
            recording=bool(value.get("isRecording", value.get("recording", False))),
            record_full_duration_media=bool(value.get("recordFullDurationMedia", False)),
            started_at=value.get("startedAt"),
            stopped_at=value.get("stoppedAt"),
        )


@dataclass(frozen=True, slots=True)
class Guest:
    """A user waiting in BBB's guest lobby."""

    id: str
    name: str = ""
    status: str | None = None

    @classmethod
    def from_graphql(cls, value: dict[str, Any]) -> "Guest":
        user = value.get("user") or {}
        return cls(
            value.get("guestId", value.get("userId", "")),
            value.get("name", user.get("name", "")),
            value.get("status", value.get("guestStatus")),
        )


@dataclass(frozen=True, slots=True)
class MediaGroupParticipant:
    user_id: str
    sender: bool = False
    receiver: bool = True
    active: bool = True

    def input(self) -> dict[str, Any]:
        return {"userId": self.user_id, "sender": self.sender, "receiver": self.receiver, "active": self.active}


@dataclass(frozen=True, slots=True)
class MediaGroupState:
    group_id: str
    media_type: str
    sender: bool = False
    receiver: bool = True
    active: bool = True

    def input(self) -> dict[str, Any]:
        return {
            "groupId": self.group_id,
            "mediaType": self.media_type,
            "sender": self.sender,
            "receiver": self.receiver,
            "active": self.active,
        }


@dataclass(frozen=True, slots=True)
class PluginDataEntry:
    id: str
    plugin_name: str
    channel_name: str
    subchannel_name: str
    payload: Any = None

    @classmethod
    def from_graphql(cls, value: dict[str, Any]) -> "PluginDataEntry":
        return cls(
            id=value.get("entryId", ""),
            plugin_name=value.get("pluginName", ""),
            channel_name=value.get("channelName", ""),
            subchannel_name=value.get("subChannelName", ""),
            payload=value.get("payloadJson"),
        )


@dataclass(frozen=True, slots=True)
class LayoutState:
    layout: str
    sync_with_presenter: bool = False
    presentation_open: bool = True
    focused_camera: str | None = None

    @classmethod
    def from_graphql(cls, value: dict[str, Any]) -> "LayoutState":
        return cls(
            layout=value.get("layout", ""),
            sync_with_presenter=bool(value.get("syncWithPresenterLayout", False)),
            presentation_open=bool(value.get("presentationIsOpen", True)),
            focused_camera=value.get("focusedCamera"),
        )
