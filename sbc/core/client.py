"""Friendly, high-level automation API for BigBlueButton."""
from __future__ import annotations

import threading
import time
import re
import uuid
import mimetypes
from datetime import datetime, timezone
from dataclasses import fields
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from .events import EventEmitter
from .graphql import (CHAT_MESSAGES, CHATS, CURRENT_USER, CURRENT_USER_STATE, MEETING,
                      PRESENTATION, USERS,
                      USER_JOIN_MEETING, VOICE_ACTIVITY, POLL_RESULTS, TIMER, MEETING_STATE,
                      PRESENTATIONS, PRESENTATION_UPLOAD_TOKEN, BREAKOUT_LIFECYCLE, GraphQLClient)
from .exceptions import ConnectionError, GraphQLError, SessionError
from .logging import get_logger
from ..models import ChatMessage, Meeting, Presentation, PresentationDocument, User
from ..media import MediaController
from ..operations import Actions
from .session import SBCSession
from ..schema import BBBTable, TABLE_EVENTS, schema
from .utils import public_chat_id
from .websocket import GraphQLWebSocket, Subscription, SubscriptionMultiplexer
from ..controllers import (BreakoutsController, CamerasController, CaptionsController, ExternalVideoController,
                          GuestsController, LocksController, MediaGroupsController, MeetingSettingsController, PluginsController,
                          PollsController, RecordingController, SharedNotesController, TimerController,
                          WhiteboardController, ReactionsController, ScreenshareController)


def _event_name(prefix: str, field: str) -> str:
    """Convert BBB GraphQL field names into stable Python event names."""
    return f"{prefix}_{re.sub(r'(?<!^)(?=[A-Z])', '_', field).lower()}_changed"


def _mapping_changes(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    return {key: (previous.get(key), current.get(key)) for key in previous.keys() | current.keys() if previous.get(key) != current.get(key)}


def _user_changes(previous: User, current: User) -> dict[str, tuple[Any, Any]]:
    return {
        field.name: (getattr(previous, field.name), getattr(current, field.name))
        for field in fields(User)
        if getattr(previous, field.name) != getattr(current, field.name)
    }


class ChatController:
    def __init__(self, client: "SBCClient"): self._client = client
    def send(self, text: str, *, chat_id: str | None = None, reply_to: str | None = None) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip(): raise ValueError("chat text must not be empty")
        chat_id = chat_id or self._client.session.snapshot.get("public_chat_id") or public_chat_id(self._client.session.meeting_id)
        return self._client.actions.chatSendMessage(chatId=chat_id, chatMessageInMarkdownFormat=text, replyToMessageId=reply_to)

    def reply(self, message: ChatMessage | str, text: str, *, chat_id: str | None = None) -> dict[str, Any]:
        """Send ``text`` as a BBB threaded reply to a chat message.

        Pass a :class:`~sbc.models.ChatMessage` received from the
        ``chat_message`` event to preserve its public/private chat id, or pass
        a message id directly with an optional ``chat_id``.
        """
        if isinstance(message, ChatMessage):
            return self.send(text, chat_id=chat_id or message.chat_id, reply_to=message.id)
        return self.send(text, chat_id=chat_id, reply_to=message)

    def mark_read(self, *, chat_id: str | None = None, at: str | None = None) -> dict[str, Any]:
        """Set the BBB read cursor for a public or private chat.

        The timestamp defaults to the current UTC time and uses BBB HTML5's
        ``chatSetLastSeen(chatId, lastSeenAt)`` runtime contract.
        """
        chat_id = chat_id or self._client.session.snapshot.get("public_chat_id") or public_chat_id()
        at = at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return self._client.actions.chatSetLastSeen(chatId=chat_id, lastSeenAt=at)

    def public_history(self, *, limit: int = 100, offset: int = 0) -> list[ChatMessage]:
        data = self._client.graphql.execute(CHAT_MESSAGES, {"limit": limit, "offset": offset})
        return [ChatMessage.from_graphql(row) for row in data.get("chat_message_public", [])]

    def private_history(self, *, limit: int = 100, offset: int = 0) -> list[ChatMessage]:
        from .graphql import PRIVATE_CHAT_MESSAGES
        data = self._client.graphql.execute(PRIVATE_CHAT_MESSAGES, {"limit": limit, "offset": offset})
        return [ChatMessage.from_graphql(row) for row in data.get("chat_message_private", [])]

    def create_private(self, user_id: str) -> dict[str, Any]:
        return self._client.actions.chatCreateWithUser(userId=user_id)

    def edit(self, chat_id: str, message_id: str, text: str) -> dict[str, Any]:
        return self._client.actions.chatEditMessage(chatId=chat_id, messageId=message_id, chatMessageInMarkdownFormat=text)

    def delete(self, chat_id: str, message_id: str) -> dict[str, Any]:
        return self._client.actions.chatDeleteMessage(chatId=chat_id, messageId=message_id)

    def react(self, chat_id: str, message_id: str, emoji: str) -> dict[str, Any]:
        return self._client.actions.chatSendMessageReaction(chatId=chat_id, messageId=message_id, reactionEmoji=emoji)

    def remove_reaction(self, chat_id: str, message_id: str, emoji: str) -> dict[str, Any]:
        return self._client.actions.chatDeleteMessageReaction(chatId=chat_id, messageId=message_id, reactionEmoji=emoji)

    def clear_public_history(self) -> dict[str, Any]:
        return self._client.actions.chatPublicClearHistory()

    def set_typing(self, chat_id: str | None = None) -> dict[str, Any]:
        return self._client.actions.chatSetTyping(chatId=chat_id)


class UsersController:
    def __init__(self, client: "SBCClient"): self._client = client
    def list(self) -> list[User]: return self._client._users()
    def mute_all(self, *, except_presenter: bool = False) -> dict[str, Any]:
        return self._client.actions.meetingSetMuted(muted=True, exceptPresenter=except_presenter)
    def mute(self, user_id: str) -> dict[str, Any]: return self._client.actions.userSetMuted(userId=user_id, muted=True)
    def unmute(self, user_id: str) -> dict[str, Any]: return self._client.actions.userSetMuted(userId=user_id, muted=False)
    def remove(self, user_id: str, *, ban: bool = False) -> dict[str, Any]:
        return self._client.actions.userEjectFromMeeting(userId=user_id, banUser=ban)


class PresentationController:
    def __init__(self, client: "SBCClient"): self._client = client
    def current(self) -> Presentation | None:
        data = self._client.graphql.execute(PRESENTATION)
        pages = data.get("pres_page_curr") or []
        return Presentation.from_graphql(pages[0]) if pages else None
    def set_page(self, presentation_id: str, page_id: str) -> dict[str, Any]:
        return self._client.actions.presentationSetPage(presentationId=presentation_id, pageId=page_id)
    def next_page(self) -> dict[str, Any]:
        # The extension records page ordering from the authenticated client. It
        # avoids inventing a BBB page id, which varies between deployments.
        pages = self._client.session.snapshot.get("presentation_pages") or []
        current = self.current()
        if not current or not pages: raise RuntimeError("presentation page list is unavailable; refresh and re-export the SBC session")
        ids = [page.get("pageId") for page in pages]
        try: target = pages[ids.index(current.page_id) + 1]
        except (ValueError, IndexError) as exc: raise RuntimeError("there is no next presentation page") from exc
        return self.set_page(target.get("presentationId", current.presentation_id), target["pageId"])

    def list(self) -> list[PresentationDocument]:
        data = self._client.graphql.execute(PRESENTATIONS)
        return [PresentationDocument.from_graphql(row) for row in data.get("pres_presentation", [])]

    def set_current(self, presentation_id: str) -> dict[str, Any]:
        return self._client.actions.presentationSetCurrent(presentationId=presentation_id)

    def remove(self, presentation_id: str) -> dict[str, Any]:
        return self._client.actions.presentationRemove(presentationId=presentation_id)

    def export(self, presentation_id: str, *, file_state_type: str | None = None) -> dict[str, Any]:
        return self._client.actions.presentationExport(presentationId=presentation_id, fileStateType=file_state_type)

    def set_downloadable(self, presentation_id: str, enabled: bool = True, *, file_state_type: str = "CONVERTED") -> dict[str, Any]:
        return self._client.actions.presentationSetDownloadable(presentationId=presentation_id, downloadable=enabled, fileStateType=file_state_type)

    def request_upload_token(self, filename: str, *, pod_id: str = "DEFAULT_PRESENTATION_POD", timeout: float = 5) -> dict[str, str]:
        """Use BBB's HTML5 upload-token flow and return its real upload token."""
        temporary_id = str(uuid.uuid4())
        self._client.actions.presentationRequestUploadToken(
            podId=pod_id,
            filename=Path(filename).name,
            uploadTemporaryId=temporary_id,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self._client.graphql.execute(PRESENTATION_UPLOAD_TOKEN, {"uploadTemporaryId": temporary_id})
            rows = data.get("pres_presentation_uploadToken") or []
            if rows and rows[0].get("uploadToken"):
                return {
                    "presentation_id": rows[0].get("presentationId", ""),
                    "temporary_id": temporary_id,
                    "token": rows[0]["uploadToken"],
                }
            time.sleep(0.25)
        raise RuntimeError("BBB did not return a presentation upload token within the timeout")

    def upload(self, path: str | Path, *, endpoint: str, downloadable: bool = True,
               current: bool = False, pod_id: str = "DEFAULT_PRESENTATION_POD") -> str:
        """Upload a presentation using BBB's source-backed tokenized HTTP flow.

        ``endpoint`` is the authenticated HTML5 uploader endpoint from the BBB
        deployment/client configuration. SBC intentionally does not guess it.
        """
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(path)
        if not self._client.session.meeting_id:
            raise RuntimeError("the session has no meeting_id; re-export it after joining before uploading")
        token = self.request_upload_token(path.name, pod_id=pod_id)
        boundary = f"----SBC{uuid.uuid4().hex}"
        fields = {
            "conference": self._client.session.meeting_id,
            "room": self._client.session.meeting_id,
            "temporaryPresentationId": token["temporary_id"],
            "pod_id": pod_id,
            "is_downloadable": str(downloadable).lower(),
            "current": str(current).lower(),
        }
        chunks: list[bytes] = []
        for key, value in fields.items():
            chunks.extend((f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(), str(value).encode(), b"\r\n"))
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.extend((f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="fileUpload"; filename="{path.name}"\r\n'.encode(), f"Content-Type: {media_type}\r\n\r\n".encode(), path.read_bytes(), b"\r\n", f"--{boundary}--\r\n".encode()))
        target = endpoint.replace("upload", f"{token['token']}/upload")
        request = Request(target, data=b"".join(chunks), method="POST")
        request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        for key, value in self._client.session.headers.items():
            if value:
                request.add_header(key, value)
        with urlopen(request, timeout=30) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"BBB presentation upload failed with HTTP {response.status}")
        return token["presentation_id"] or token["temporary_id"]

    def download(self, presentation: PresentationDocument | str, destination: str | Path) -> Path:
        """Download a BBB presentation using its authenticated source URL."""
        document = presentation if isinstance(presentation, PresentationDocument) else next((item for item in self.list() if item.id == presentation), None)
        if document is None or not document.download_url:
            raise RuntimeError("BBB did not expose a downloadable URL for this presentation")
        target = Path(destination)
        if target.is_dir():
            target /= document.name or f"{document.id}.pdf"
        request = Request(urljoin(self._client.session.server + "/", document.download_url))
        for key, value in self._client.session.headers.items():
            if value:
                request.add_header(key, value)
        with urlopen(request, timeout=30) as response:
            target.write_bytes(response.read())
        return target


class MeetingController:
    def __init__(self, client: "SBCClient"): self._client = client; self._cached: Meeting | None = None
    def __call__(self) -> "MeetingController": return self
    @property
    def name(self) -> str | None: return self.info().name
    @property
    def id(self) -> str | None: return self.info().id
    @property
    def ended(self) -> bool: return self.info().ended
    def info(self, *, refresh: bool = False) -> Meeting:
        if self._cached is None or refresh:
            data = self._client.graphql.execute(MEETING)
            row = (data.get("meeting") or [{}])[0]
            self._cached = Meeting(row.get("meetingId", self._client.session.meeting_id), row.get("name", self._client.session.meeting_name), bool(row.get("ended", False)))
        return self._cached
    def users(self) -> list[User]: return self._client.users.list()
    def end(self) -> dict[str, Any]: return self._client.actions.meetingEnd()


class SBCClient(EventEmitter):
    """An independent authenticated connection to one BBB meeting."""
    def __init__(self, session: SBCSession, *, connect: bool = True, session_path: str | Path | None = None, auto_join: bool = True, listen_only: bool = True):
        super().__init__()
        self.session = session
        self.session_path = Path(session_path) if session_path is not None else None
        self.transport = GraphQLWebSocket(session)
        self.graphql = GraphQLClient(self.transport, on_error=self._handle_graphql_error)
        self.chat = ChatController(self)
        self.actions = Actions(self)
        self.users = UsersController(self)
        self.presentation = PresentationController(self)
        self.media = MediaController(self)
        self.meeting = MeetingController(self)
        self.polls = PollsController(self)
        self.breakouts = BreakoutsController(self)
        self.cameras = CamerasController(self)
        self.captions = CaptionsController(self)
        self.notes = SharedNotesController(self)
        self.recording = RecordingController(self)
        self.whiteboard = WhiteboardController(self)
        self.guests = GuestsController(self)
        self.timer = TimerController(self)
        self.external_video = ExternalVideoController(self)
        self.plugins = PluginsController(self)
        self.media_groups = MediaGroupsController(self)
        self.settings = MeetingSettingsController(self)
        self.locks = LocksController(self)
        self.screenshare = ScreenshareController(self)
        self.reactions = ReactionsController(self)
        # Descriptive plural aliases keep scripts readable without removing the
        # short names used by existing SBC examples.
        self.breakout_rooms = self.breakouts
        self.shared_notes = self.notes
        self.recordings = self.recording
        self.whiteboards = self.whiteboard
        self.presentations = self.presentation
        self.timers = self.timer
        self.guest_lobby = self.guests
        self.external_videos = self.external_video
        self._stop = threading.Event()
        self._event_transports: set[GraphQLWebSocket] = set()
        self._event_transport_lock = threading.Lock()
        self._event_multiplexer: SubscriptionMultiplexer | None = None
        self._custom_streams: list[tuple[str, dict[str, Any], Any]] = []
        self._enabled_event_streams: set[str] = set()
        self._connection_lease_stop = threading.Event()
        self._connection_lease_thread: threading.Thread | None = None
        self.auto_join = auto_join
        self.listen_only = listen_only
        # ``ensure_joined`` can be reached repeatedly (for example after a
        # subscription reconnect).  Apply the requested audio mode once per
        # client connection, even when the saved BBB identity was already
        # present when SBC started.  Without this, ``listen_only=False``
        # silently did nothing for an already-active identity and custom
        # audio had no warmed microphone publisher to use.
        self._initial_media_mode_applied = False
        if connect: self.connect()

    def __enter__(self) -> "SBCClient":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    @classmethod
    def from_file(cls, path: str | Path, *, connect: bool = True, auto_join: bool = True, listen_only: bool = True) -> "SBCClient":
        session = SBCSession.load(path)
        # Store the resolved loaded path so refreshed credentials can be saved
        # directly back into the portable .sbc package.
        requested = Path(path).expanduser()
        if not requested.is_file():
            for root in (Path.cwd(), Path.cwd() / "sessions", Path.cwd() / "examples", Path(__file__).resolve().parent.parent / "sessions", Path(__file__).resolve().parent.parent / "examples"):
                if (root / requested).is_file(): requested = root / requested; break
        return cls(session, connect=connect, session_path=requested, auto_join=auto_join, listen_only=listen_only)
    def save_session(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path is not None else self.session_path
        if target is None: raise ValueError("provide a path for a session that was not loaded from a file")
        saved = self.session.save(target); self.session_path = saved; return saved
    def connect(self) -> "SBCClient":
        get_logger().info("Loading SBC session for %s", self.session.meeting_name or self.session.server)
        self.transport.connect()
        if self.auto_join:
            self.ensure_joined()
        self._start_connection_lease()
        return self

    def _start_connection_lease(self) -> None:
        """Mirror the HTML5 client's RTT/liveness heartbeat in the background.

        BBB treats a WebSocket transport and its media SFU socket as separate
        resources.  Keeping only the SFU WebSocket alive can leave a bot's
        ``connectionAliveAt`` stale, allowing some deployments to remove the
        participant and subsequently terminate its media transport.
        """
        if self._connection_lease_thread and self._connection_lease_thread.is_alive():
            return
        self._connection_lease_stop.clear()
        self._connection_lease_thread = threading.Thread(
            target=self._connection_lease_loop,
            daemon=True,
            name="sbc-connection-lease",
        )
        self._connection_lease_thread.start()
        get_logger().info("Started BBB connection-liveness heartbeat")

    def _connection_lease_loop(self) -> None:
        # BBB HTML5 defaults its RTT worker to ten seconds and starts its
        # first request after half an interval.
        if self._connection_lease_stop.wait(5):
            return
        while not self._connection_lease_stop.is_set():
            try:
                self._report_connection_alive()
            except Exception as exc:
                # A transient RTT endpoint failure must never terminate media
                # or the main automation client. It is retried on the next
                # source-compatible heartbeat interval.
                get_logger().warning("BBB connection-liveness heartbeat failed: %s", exc)
            if self._connection_lease_stop.wait(10):
                return

    def _report_connection_alive(self) -> None:
        """Perform BBB HTML5's ``/rtt-check`` then ``userSetConnectionAlive`` flow."""
        meeting_id = self.session.meeting_id or self.meeting.info().id
        if not meeting_id:
            raise ConnectionError("BBB connection-liveness heartbeat needs a meeting id")
        client_uuid = self.transport.client_session_uuid
        query = urlencode({"session": client_uuid, "user": self.session.user_id or "", "meeting": meeting_id})
        # HTML5 calls ``getBaseUrl()/rtt-check``. BBB 3.0's source default is
        # ``public.app.bbbWebBase = /bigbluebutton``; extractor snapshots can
        # override it for reverse-proxy deployments.
        app_settings = self.session.snapshot.get("meeting_client_settings") or {}
        public_app: dict[str, Any] = {}
        if isinstance(app_settings, dict):
            public = app_settings.get("public") or {}
            if isinstance(public, dict) and isinstance(public.get("app"), dict):
                public_app = public["app"]
        web_base = self.session.snapshot.get("bbb_web_base") or public_app.get("bbbWebBase") or "/bigbluebutton"
        endpoint = urljoin(f"{self.session.server}/", f"{str(web_base).strip('/')}/rtt-check?{query}")
        started = time.perf_counter()
        request = Request(endpoint, headers=self.session.headers)
        with urlopen(request, timeout=10) as response:
            # BBB's worker returns this request id and the GraphQL mutation
            # uses it to associate the browser RTT measurement server-side.
            request_id = response.headers.get("X-Request-Id")
        if not request_id:
            raise ConnectionError("BBB RTT check did not return X-Request-Id")
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        mutation = (
            "mutation SBCConnectionAlive($serverRequestId:String!,$clientSessionUUID:String!,"
            "$networkRttInMs:Float!,$applicationRttInMs:Float){"
            "userSetConnectionAlive(serverRequestId:$serverRequestId,clientSessionUUID:$clientSessionUUID,"
            "networkRttInMs:$networkRttInMs,applicationRttInMs:$applicationRttInMs)}"
        )
        self.graphql.mutation(mutation, {
            "serverRequestId": request_id,
            "clientSessionUUID": client_uuid,
            "networkRttInMs": elapsed_ms,
            "applicationRttInMs": None,
        })
        get_logger().debug("BBB connection-liveness heartbeat acknowledged (RTT %.0f ms)", elapsed_ms)

    def _handle_graphql_error(self, error: GraphQLError) -> None:
        """Flag browser credentials that BBB says are no longer usable."""
        message = str(error).lower()
        markers = ("expired", "unauthenticated", "unauthorized", "invalid session", "session token", "not authorized")
        if any(marker in message for marker in markers):
            self.session.mark_reexport_required(str(error))
            get_logger().error("BBB rejected this captured session; export a fresh .sbc file from the extension")

    def _saved_current_user(self) -> dict[str, Any]:
        value = self.session.snapshot.get("current_user") or {}
        return value if isinstance(value, dict) else {}

    def _update_current_user(self, value: dict[str, Any]) -> dict[str, Any]:
        current = {
            "user_id": value.get("userId"),
            "auth_token": value.get("authToken"),
            "joined": bool(value.get("joined")),
            "currently_in_meeting": bool(value.get("currentlyInMeeting")),
            "logged_out": bool(value.get("loggedOut")),
            "ejected": bool(value.get("ejected")),
            "join_error_code": value.get("joinErrorCode"),
            "join_error_message": value.get("joinErrorMessage"),
        }
        self.session.snapshot["current_user"] = current
        if current["user_id"]:
            self.session.user_id = current["user_id"]
        return current

    def _fetch_current_user(self, *, timeout: float = 3) -> dict[str, Any] | None:
        """Read one current-user update without blocking normal automation."""
        probe = GraphQLWebSocket(self.session, timeout=timeout, reconnects=0, operation_timeout=timeout)
        try:
            data = GraphQLClient(probe).execute(CURRENT_USER)
            rows = data.get("user_current") or []
            if rows:
                get_logger().info("Received BBB user state for %s", rows[0].get("userId", "saved user"))
                return self._update_current_user(rows[0])
        except ConnectionError:
            get_logger().info("BBB did not return a current-user update within %.0fs", timeout)
            return None
        finally:
            probe.close()
        return None

    def ensure_joined(self, *, timeout: float = 20, force: bool = False) -> bool:
        """Join the saved BBB identity before automation runs.

        Returns ``True`` when SBC had to issue ``userJoinMeeting`` and ``False``
        when the saved user was already present or no join state was exported.
        """
        current = self._saved_current_user()
        # Exported state is necessarily stale once a browser tab leaves. BBB's
        # UserJoinMeetingReq handler is safe for a live user (it performs the
        # reconnect path) so never treat a saved ``joined=true`` as proof.
        get_logger().info("Checking whether the saved BBB user is currently in the meeting")
        active = False
        fresh = self._fetch_current_user()
        if fresh:
            current = fresh
            active = current.get("joined") and current.get("currently_in_meeting") and not current.get("logged_out") and not current.get("ejected")
            if active and not force:
                get_logger().info("BBB confirms the saved user is already in the meeting")
                if not self._initial_media_mode_applied:
                    if self.listen_only:
                        self._set_listen_only_default()
                    else:
                        self._set_microphone_default()
                    self._initial_media_mode_applied = True
                return False
        token = current.get("auth_token") or self.session.snapshot.get("auth_token")
        state_known = bool(current)
        if not token:
            if state_known and not active:
                get_logger().error("Cannot join: the saved SBC session has no BBB auth token")
                raise SessionError("saved user is not in the meeting and this .sbc has no BBB auth token; re-export the session from the extension")
            get_logger().info("No join state was exported; continuing with the active BBB session")
            return False
        get_logger().info("Requesting BBB userJoinMeeting%s", " reconnection" if force else "")
        join_transport = GraphQLWebSocket(self.session, timeout=4, reconnects=0, operation_timeout=4)
        try:
            GraphQLClient(join_transport).mutation(USER_JOIN_MEETING, {
                "authToken": token,
                "clientType": "HTML5",
                "clientIsMobile": False,
            })
        except ConnectionError:
            # BBB deliberately closes GraphQL sessions after a successful join
            # so the new identity claims fresh session variables. Continue to
            # the confirmation probe rather than freezing on that close.
            get_logger().info("BBB reset the GraphQL connection while processing the join request")
        finally:
            join_transport.close()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self._fetch_current_user(timeout=min(3, max(1, deadline - time.monotonic())))
            if current and current.get("joined") and current.get("currently_in_meeting"):
                get_logger().info("BBB confirmed the user joined the meeting")
                # BBB's join handler forces a GraphQL reconnection after it has
                # rebuilt the user's media state. Give the SFU route a moment
                # to observe that completed state before creating WebRTC.
                time.sleep(1)
                if self.listen_only:
                    self._set_listen_only_default()
                else:
                    self._set_microphone_default()
                self._initial_media_mode_applied = True
                return True
            if current and (current.get("ejected") or current.get("join_error_code")):
                detail = current.get("join_error_message") or current.get("join_error_code")
                raise SessionError(f"BBB could not join the saved user: {detail}")
            time.sleep(0.25)
        get_logger().error("BBB did not confirm that the saved user joined within %.0fs", timeout)
        raise ConnectionError("BBB did not confirm that the saved user joined before the timeout")

    def _set_listen_only_default(self) -> None:
        """Mark a newly joined SBC identity as a listener, never a microphone.

        This is BBB's real ``userSetListenOnlyInput`` action. It deliberately
        does not open a Python microphone or publish an audio track.
        """
        try:
            get_logger().info("Setting newly joined BBB user to listener mode")
            self.media.set_input_mode("listener")
            self.actions.userSetListenOnlyInput(listenOnlyInputDevice=True)
            self.media.listener.join()
        except Exception as exc:
            # Joining has already succeeded. A server may not expose this
            # optional UI-state action, so preserve the joined connection.
            get_logger().warning("BBB listener session could not be started: %s", exc)

    def _set_microphone_default(self) -> None:
        """Select full-audio mode without opening an idle silent publisher.

        ``media.audio.play()`` starts its own sender when there is an actual
        file to publish.  Automatic warm-up is deliberately opt-in via
        ``media.audio.warmup()`` because some BBB SFU deployments immediately
        tear down an idle silent microphone and it can make the microphone UI
        flash before any user-requested audio exists.
        """
        try:
            get_logger().info("Selecting BBB full-audio mode (idle microphone warm-up is disabled)")
            self.media.set_input_mode("microphone")
            # A session may previously have been listener-only. BBB keeps this
            # client setting across reconnections; clear it before warming the
            # sendrecv SFU stream or the server can accept WebRTC while still
            # suppressing the participant's microphone input.
            self.actions.userSetListenOnlyInput(listenOnlyInputDevice=False)
        except Exception as exc:
            get_logger().warning("BBB microphone mode could not be started: %s", exc)
    def close(self) -> None:
        self._stop.set()
        self._connection_lease_stop.set()
        lease = self._connection_lease_thread
        if lease and lease is not threading.current_thread():
            lease.join(timeout=1)
        self.transport.close()
        self.media.close()
        if self._event_multiplexer is not None:
            self._event_multiplexer.close()
        with self._event_transport_lock:
            transports = tuple(self._event_transports)
        for transport in transports: transport.close()
    def _users(self) -> list[User]:
        data = self.graphql.execute(USERS)
        return [User.from_graphql(row) for row in data.get("user", [])]

    def mutation(self, name: str, /, **variables: Any) -> dict[str, Any]:
        """Run any of the 109 BBB action mutations by its schema name."""
        return self.actions.call(name, **variables)

    def on(self, event: str, handler=None, *, priority: int = 0, when=None):
        """Register an event handler and enable only its required BBB stream."""
        self.enable_events(event)
        return super().on(event, handler, priority=priority, when=when)

    def once(self, event: str, handler=None, *, priority: int = 0, when=None):
        """Register a one-shot BBB handler and enable only its required stream."""
        self.enable_events(event)
        return super().once(event, handler, priority=priority, when=when)

    def off(self, event: str, handler=None) -> int:
        """Remove callbacks and stop selecting an unused built-in stream."""
        removed = super().off(event, handler)
        stream = self._event_stream_for(event)
        if stream and not self._handlers.get(event):
            still_needed = any(
                self._event_stream_for(name) == stream and records
                for name, records in self._handlers.items()
            )
            if not still_needed:
                self._enabled_event_streams.discard(stream)
        return removed

    def enable_events(self, *events: str) -> None:
        """Enable built-in BBB event streams without registering a callback.

        SBC deliberately does not open every possible GraphQL subscription by
        default. BBB installations commonly limit concurrent authenticated
        sockets; a voice bot should therefore open only the voice stream.
        """
        for event in events:
            stream = self._event_stream_for(event)
            if stream:
                self._enabled_event_streams.add(stream)

    @staticmethod
    def _event_stream_for(event: str) -> str | None:
        # Source-schema table events are opt-in through ``watch_table`` because
        # each one needs an explicit field selection.
        if event in TABLE_EVENTS:
            return None
        if event in {"user_talking", "user_stopped_talking"}:
            return "voice_activity"
        if event == "chat_message":
            return "chat_messages"
        if event in {"chat_updated", "public_chat_updated", "private_chat_updated"}:
            return "chats"
        if event == "presentation_changed":
            return "presentation"
        if event == "meeting_ended":
            return "meeting"
        if event.startswith("poll_"):
            return "polls"
        if event.startswith("breakout_"):
            return "breakouts"
        if event.startswith("timer_"):
            return "timer"
        if event.startswith("current_user_"):
            return "current_user"
        if event.startswith("screenshare_") or event.startswith("external_video_") or event == "meeting_updated" or event.startswith("meeting_"):
            return "meeting_state"
        if event.startswith("user_") or event in {"hand_raised", "hand_lowered", "voice_joined", "voice_left", "camera_started", "camera_stopped"}:
            return "users"
        return None

    def watch(self, query: str, handler, *, variables: dict[str, Any] | None = None) -> None:
        """Add any authenticated BBB GraphQL subscription before :meth:`run`.

        ``handler`` receives each decoded GraphQL data dictionary. This covers
        deployment-specific BBB tables beyond SBC's built-in named events.
        """
        if not isinstance(query, str) or not query.lstrip().startswith("subscription"):
            raise ValueError("watch() requires a GraphQL subscription operation")
        if not callable(handler):
            raise TypeError("watch() handler must be callable")
        if getattr(self, "_event_running", False):
            raise RuntimeError("register custom subscriptions before client.run()")
        self._custom_streams.append((query, dict(variables or {}), handler))

    def watch_table(self, table: BBBTable | str, fields: str, *, event: str | None = None,
                    name: str = "SBCStream", arguments: str = "",
                    variables: dict[str, Any] | None = None) -> str:
        """Expose any BBB 3.0.32 schema table as an SBC event.

        Example::

            client.watch_table(sbc.BBBTable.NOTIFICATION,
                               "messageId notificationType messageDescription")
            @client.on("notification_changed")
            def notification(rows): ...

        Register the table subscription before :meth:`run`.
        """
        return schema.watch_event(
            self, table, fields, event=event, name=name,
            arguments=arguments, variables=variables,
        )

    def __getattr__(self, name: str):
        # Community scripts often prefer ``client.user_set_away(...)``. Every
        # action also remains discoverable under ``client.actions``.
        if name.startswith("_"): raise AttributeError(name)
        try:
            return getattr(self.actions, name)
        except Exception as exc:
            raise AttributeError(name) from exc

    def _event_transport(self) -> GraphQLClient:
        # A live subscription consumes incoming frames continuously, so it must
        # have its own connection rather than race normal automation operations.
        transport = GraphQLWebSocket(self.session)
        with self._event_transport_lock: self._event_transports.add(transport)
        return GraphQLClient(transport)

    def run(self) -> None:
        """Run event subscriptions until :meth:`close` is called or Ctrl-C stops it."""
        available_streams = {
            "users": (USERS, {}, self._watch_users),
            "voice_activity": (VOICE_ACTIVITY, {}, self._watch_voice_activity),
            "chat_messages": (CHAT_MESSAGES, {"limit": 100, "offset": 0}, self._watch_chat),
            "chats": (CHATS, {}, self._watch_chats),
            "presentation": (PRESENTATION, {}, self._watch_presentation),
            "meeting": (MEETING, {}, self._watch_meeting),
            "meeting_state": (MEETING_STATE, {}, self._watch_meeting_state),
            "polls": (POLL_RESULTS, {}, self._watch_polls),
            "breakouts": (BREAKOUT_LIFECYCLE, {}, self._watch_breakouts),
            "timer": (TIMER, {}, self._watch_timer),
            "current_user": (CURRENT_USER_STATE, {}, self._watch_current_user),
        }
        streams = [available_streams[name] for name in sorted(self._enabled_event_streams)]
        streams.extend(self._custom_streams)
        if streams:
            get_logger().info("Starting SBC event streams: %s", ", ".join(sorted(self._enabled_event_streams)) or "custom")
        else:
            get_logger().warning("No SBC event handlers are registered; client.run() is waiting without GraphQL subscriptions")
        self._event_running = True
        try:
            if streams:
                transport = GraphQLWebSocket(self.session)
                with self._event_transport_lock:
                    self._event_transports.add(transport)
                self._event_multiplexer = SubscriptionMultiplexer(
                    transport,
                    [
                        Subscription(
                            query,
                            variables,
                            lambda response, callback=callback: self._dispatch_stream(callback, response),
                        )
                        for query, variables, callback in streams
                    ],
                    on_error=self._event_stream_error,
                )
                self._event_multiplexer.run(self._stop)
            else:
                while not self._stop.wait(0.25):
                    pass
        except KeyboardInterrupt:
            self.close()
        finally:
            if self._event_multiplexer is not None:
                transport = self._event_multiplexer.transport
                self._event_multiplexer.close()
                with self._event_transport_lock:
                    self._event_transports.discard(transport)
                self._event_multiplexer = None
            self._event_running = False

    def _dispatch_stream(self, callback, response: dict[str, Any]) -> None:
        """Turn graphql-transport-ws payloads into SBC watcher data."""
        if response.get("errors"):
            raise ConnectionError("; ".join(str(item.get("message", item)) for item in response["errors"]))
        callback(response.get("data", response))

    def _event_stream_error(self, error: Exception) -> None:
        """Report event failures without killing unrelated registered handlers."""
        get_logger().warning("BBB event stream failed: %s", error)
        self.emit("error", error)

    def _consume(self, query: str, variables: dict[str, Any], callback) -> None:
        delay = 1.0
        try:
            while not self._stop.is_set():
                stream = self._event_transport()
                try:
                    for data in stream.subscribe(query, variables):
                        if self._stop.is_set(): break
                        callback(data)
                    delay = 1.0
                except (ConnectionError, OSError):
                    # Transient network/server drops are normal for long-running
                    # BBB bots. Keep each event stream alive until ``close``.
                    if not self._stop.wait(delay): delay = min(delay * 2, 20.0)
                except Exception as exc:
                    # Schema/permission errors should not kill every other event
                    # worker or print an uncontrolled thread traceback.
                    get_logger().warning("BBB event stream failed: %s", exc)
                    self.emit("error", exc)
                    if not self._stop.wait(delay): delay = min(delay * 2, 20.0)
                finally:
                    stream.transport.close()
                    with self._event_transport_lock: self._event_transports.discard(stream.transport)
        finally:
            return

    def _watch_users(self, data: dict[str, Any]) -> None:
        current = {u.id: u for u in (User.from_graphql(row) for row in data.get("user", []))}
        previous = getattr(self, "_event_users", None)

        # A GraphQL subscription's first result is the current table snapshot,
        # not a stream of historical joins. Treat it as the baseline so a bot
        # does not welcome itself or every participant already in the room.
        # Subsequent snapshots are diffed and only genuinely new IDs emit
        # ``user_joined``.
        if previous is None:
            self._event_users = current
            return

        for user_id, user in current.items():
            old = previous.get(user_id)
            if old is None:
                self.emit("user_joined", user)
                continue
            changes = _user_changes(old, user)
            if not changes:
                continue
            # Universal handler plus a handler for every selected BBB user
            # field: ``user_away_changed``, ``user_role_changed``, etc.
            self.emit("user_updated", user, changes)
            self.emit("user_changed", user, changes)
            for field, change in changes.items():
                self.emit(_event_name("user", field), user, *change)
            if not old.hand_raised and user.hand_raised: self.emit("hand_raised", user)
            if old.hand_raised and not user.hand_raised: self.emit("hand_lowered", user)
            if not old.voice_joined and user.voice_joined: self.emit("voice_joined", user)
            if old.voice_joined and not user.voice_joined: self.emit("voice_left", user)
            if not old.muted and user.muted: self.emit("user_muted", user)
            if old.muted and not user.muted: self.emit("user_unmuted", user)
            if not old.is_presenter and user.is_presenter: self.emit("user_became_presenter", user)
            if old.is_presenter and not user.is_presenter: self.emit("user_stopped_presenting", user)
            if not old.is_moderator and user.is_moderator: self.emit("user_became_moderator", user)
            if old.is_moderator and not user.is_moderator: self.emit("user_stopped_moderating", user)
            if not old.away and user.away: self.emit("user_away", user)
            if old.away and not user.away: self.emit("user_back", user)
            if not old.disconnected and user.disconnected: self.emit("user_disconnected", user)
            if old.disconnected and not user.disconnected: self.emit("user_reconnected", user)
            if not old.camera_stream_ids and user.camera_stream_ids: self.emit("camera_started", user)
            if old.camera_stream_ids and not user.camera_stream_ids: self.emit("camera_stopped", user)
        for user_id, user in previous.items():
            if user_id not in current: self.emit("user_left", user)
        self._event_users = current

    def _watch_voice_activity(self, data: dict[str, Any]) -> None:
        """Emit speech transitions from BBB's live ``user.voice.talking`` state."""
        previous = getattr(self, "_voice_activity", {})
        activity_rows = data.get("user_voice_activity_stream")
        if activity_rows is None:
            activity_rows = [
                {
                    "userId": row.get("userId"), "talking": (row.get("voice") or {}).get("talking"),
                    "muted": (row.get("voice") or {}).get("muted"), "user": {"name": row.get("name", "")},
                }
                for row in data.get("user") or []
            ]
        for row in activity_rows:
            user = User.from_voice_activity(row)
            if not user.id:
                continue
            talking = bool(row.get("talking")) and not bool(row.get("leftVoiceConf"))
            old_talking = previous.get(user.id)
            previous[user.id] = talking
            if talking and old_talking is not True:
                get_logger().info("BBB detected speech from %s (%s)", user.name or user.id, user.id)
                self.emit("user_talking", user)
            elif not talking and old_talking is True:
                get_logger().info("BBB detected that %s stopped speaking", user.name or user.id)
                self.emit("user_stopped_talking", user)
        self._voice_activity = previous

    def _watch_chats(self, data: dict[str, Any]) -> None:
        current = {row.get("chatId"): row for row in data.get("chat") or [] if row.get("chatId")}
        previous = getattr(self, "_event_chats", {})
        for chat_id, chat in current.items():
            changes = _mapping_changes(previous.get(chat_id, {}), chat)
            if changes:
                self.emit("chat_updated", chat, changes)
                self.emit("public_chat_updated" if chat.get("public") else "private_chat_updated", chat, changes)
        self._event_chats = current

    def _watch_polls(self, data: dict[str, Any]) -> None:
        rows = data.get("poll") or []
        current = {row.get("pollId"): row for row in rows if row.get("pollId")}
        previous = getattr(self, "_event_polls", {})
        for poll_id, poll in current.items():
            old = previous.get(poll_id, {})
            changes = _mapping_changes(old, poll)
            if not changes:
                continue
            self.emit("poll_updated", poll, changes)
            if poll.get("published") and not old.get("published"):
                self.emit("poll_published", poll)
            if poll.get("ended") and not old.get("ended"):
                self.emit("poll_ended", poll)
            if "responses" in changes:
                self.emit("poll_results_changed", poll)
        self._event_polls = current

    def _watch_timer(self, data: dict[str, Any]) -> None:
        rows = data.get("timer") or []
        current = rows[0] if rows else None
        if current is None:
            return
        previous = getattr(self, "_event_timer", {})
        changes = _mapping_changes(previous, current)
        if changes:
            self.emit("timer_updated", current, changes)
            if current.get("running") and not previous.get("running"):
                self.emit("timer_started", current)
            if not current.get("running") and previous.get("running"):
                self.emit("timer_stopped", current)
            if current.get("elapsed") and not previous.get("elapsed"):
                self.emit("timer_elapsed", current)
        self._event_timer = current

    def _watch_breakouts(self, data: dict[str, Any]) -> None:
        """Emit source-backed breakout lifecycle transitions."""
        rows = data.get("breakoutRoom") or []
        current = {row.get("breakoutRoomMeetingId"): row for row in rows if row.get("breakoutRoomMeetingId")}
        previous = getattr(self, "_event_breakouts", {})
        for room_id, room in current.items():
            old = previous.get(room_id, {})
            changes = _mapping_changes(old, room)
            if old and changes:
                self.emit("breakout_updated", room, changes)
            if not old:
                self.emit("breakout_created", room)
            if room.get("startedAt") and not old.get("startedAt"):
                self.emit("breakout_started", room)
            if room.get("endedAt") and not old.get("endedAt"):
                self.emit("breakout_ended", room)
        self._event_breakouts = current

    def _watch_meeting_state(self, data: dict[str, Any]) -> None:
        rows = data.get("meeting") or []
        current = rows[0] if rows else None
        if current is None:
            return
        previous = getattr(self, "_event_meeting_state", {})
        changes = _mapping_changes(previous, current)
        if changes:
            self.emit("meeting_updated", current, changes)
            for field, change in changes.items():
                self.emit(_event_name("meeting", field), current, *change)
            old_screen, screen = previous.get("screenshare") or {}, current.get("screenshare") or {}
            if screen.get("stream") and not old_screen.get("stream"):
                self.emit("screenshare_started", current)
            if old_screen.get("stream") and not screen.get("stream"):
                self.emit("screenshare_stopped", current)
            old_video, video = previous.get("externalVideo") or {}, current.get("externalVideo") or {}
            if video.get("externalVideoId") and not old_video.get("externalVideoId"):
                self.emit("external_video_started", current)
            if old_video.get("externalVideoId") and not video.get("externalVideoId"):
                self.emit("external_video_stopped", current)
        self._event_meeting_state = current

    def _watch_current_user(self, data: dict[str, Any]) -> None:
        rows = data.get("user_current") or []
        if not rows:
            return
        current = rows[0]
        previous = getattr(self, "_event_current_user", {})
        changes = _mapping_changes(previous, current)
        if changes:
            self.emit("current_user_updated", current, changes)
            for field, change in changes.items():
                self.emit(_event_name("current_user", field), current, *change)
            if current.get("joined") and not previous.get("joined"):
                self.emit("current_user_joined", current)
            if previous.get("joined") and not current.get("joined"):
                self.emit("current_user_left", current)
            if current.get("ejected") and not previous.get("ejected"):
                self.emit("current_user_ejected", current)
        self._event_current_user = current

    def _watch_chat(self, data: dict[str, Any]) -> None:
        messages = [ChatMessage.from_graphql(row) for row in data.get("chat_message_public", [])]
        seen = getattr(self, "_seen_messages", None)
        # The initial public-chat query contains history. Mark it seen instead
        # of emitting it as newly received messages; callers can explicitly
        # use ``chat.public_history()`` when they want the backlog.
        if seen is None:
            self._seen_messages = {message.id for message in messages}
            return
        for message in messages:
            if message.id not in seen: self.emit("chat_message", message)
        self._seen_messages = seen | {message.id for message in messages}

    def _watch_presentation(self, data: dict[str, Any]) -> None:
        pages = data.get("pres_page_curr") or []
        if pages:
            presentation = Presentation.from_graphql(pages[0])
            if getattr(self, "_event_presentation", None) != presentation:
                if hasattr(self, "_event_presentation"): self.emit("presentation_changed", presentation)
                self._event_presentation = presentation

    def _watch_meeting(self, data: dict[str, Any]) -> None:
        rows = data.get("meeting") or []
        if rows and rows[0].get("ended") and not getattr(self, "_event_ended", False):
            self._event_ended = True; self.emit("meeting_ended")
