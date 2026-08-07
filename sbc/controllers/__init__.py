"""High-level, typed controllers over SBC's complete BBB action registry."""
from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any, Iterable

from ..types import CaptionProvider, GuestApproval, GuestPolicy, Layout, MediaScope, MediaType, PollType, Role, enum_value
from ..core.graphql import (BREAKOUT_ROOMS, CAMERAS, CAPTIONS, GUESTS, PLUGIN_DATA, POLLS,
                      RECORDING, SCREENSHARE, WHITEBOARD)
from ..models import (
    BreakoutRoom,
    Camera,
    Caption,
    Guest,
    LockSettings,
    MediaGroupParticipant,
    MediaGroupState,
    PluginDataEntry,
    Poll,
    Recording,
    Screenshare,
    WhiteboardAnnotation,
    WhiteboardCursor,
)

if TYPE_CHECKING:
    from ..core.client import SBCClient


class _Controller:
    def __init__(self, client: "SBCClient"): self._client = client
    def _call(self, action: str, **variables: Any) -> dict[str, Any]: return self._client.actions.call(action, **variables)
    def _read(self, query: str, table: str) -> list[dict[str, Any]]:
        return list(self._client.graphql.execute(query).get(table) or [])


class PollsController(_Controller):
    def list(self) -> list[Poll]:
        """Return all polls visible to the current BBB identity."""
        return [Poll.from_graphql(row) for row in self._read(POLLS, "poll")]

    def create(self, question: str, options: Iterable[str] = (), *, poll_type: PollType | str = PollType.MULTIPLE_CHOICE, multiple_responses: bool = False, quiz: bool = False, secret: bool = False, correct_answer: str | None = None, poll_id: str | None = None) -> str:
        poll_id = poll_id or str(uuid.uuid4())
        self._call("pollCreate", pollId=poll_id, pollType=enum_value(poll_type), secretPoll=secret, question=question, multipleResponse=multiple_responses, quiz=quiz, answers=list(options) or None, correctAnswer=correct_answer)
        return poll_id
    def publish(self, poll_id: str, *, show_answers: bool = False) -> dict[str, Any]: return self._call("pollPublishResult", pollId=poll_id, showAnswer=show_answers)
    def vote(self, poll_id: str, option_ids: Iterable[int]) -> dict[str, Any]: return self._call("pollSubmitUserVote", pollId=poll_id, answerIds=list(option_ids))
    def answer(self, poll_id: str, text: str) -> dict[str, Any]: return self._call("pollSubmitUserTypedVote", pollId=poll_id, answer=text)
    def cancel(self) -> dict[str, Any]: return self._call("pollCancel")


class BreakoutsController(_Controller):
    def list(self) -> list[BreakoutRoom]:
        return [BreakoutRoom.from_graphql(row) for row in self._read(BREAKOUT_ROOMS, "breakoutRoom")]

    def create(self, rooms: Iterable[BreakoutRoom], *, duration_minutes: int, record: bool = False, capture_notes: bool = False, capture_slides: bool = False, invite_moderators: bool = False) -> dict[str, Any]:
        return self._call("breakoutRoomCreate", record=record, captureNotes=capture_notes, captureSlides=capture_slides, durationInMinutes=duration_minutes, sendInviteToModerators=invite_moderators, rooms=[room.input() for room in rooms])
    def end_all(self) -> dict[str, Any]: return self._call("breakoutRoomEndAll")
    def move(self, user_id: str, *, from_room: str, to_room: str) -> dict[str, Any]: return self._call("breakoutRoomMoveUser", userId=user_id, fromBreakoutRoomMeetingId=from_room, toBreakoutRoomMeetingId=to_room)
    def set_time(self, minutes: int) -> dict[str, Any]: return self._call("breakoutRoomSetTime", timeInMinutes=minutes)
    def message_all(self, text: str) -> dict[str, Any]: return self._call("breakoutRoomSendMessageToAll", message=text)
    def request_join_url(self, room_id: str) -> dict[str, Any]: return self._call("breakoutRoomRequestJoinUrl", breakoutRoomMeetingId=room_id)
    def dismiss_invite(self) -> dict[str, Any]: return self._call("breakoutRoomSetInviteDismissed")


class CaptionsController(_Controller):
    def transcript(self) -> list[Caption]:
        """Return the captions/transcription rows visible to this user."""
        return [Caption.from_graphql(row) for row in self._read(CAPTIONS, "caption")]

    def add_locale(self, locale: str) -> dict[str, Any]: return self._call("captionAddLocale", locale=locale)
    def set_locale(self, locale: str, provider: CaptionProvider | str = CaptionProvider.DEFAULT) -> dict[str, Any]: return self._call("userSetCaptionLocale", locale=locale, provider=enum_value(provider))
    def submit(self, caption: Caption) -> dict[str, Any]: return self._call("captionSubmitText", transcriptId=caption.transcript_id, start=caption.start, end=caption.end, text=caption.text, transcript=caption.text, locale=caption.locale, isFinal=caption.final)
    def submit_transcript(self, transcript_id: str, text: str, locale: str) -> dict[str, Any]:
        return self._call("captionSubmitTranscript", transcriptId=transcript_id, transcript=text, locale=locale)
    def speech_locale(self, locale: str, provider: CaptionProvider | str = CaptionProvider.DEFAULT) -> dict[str, Any]:
        return self._call("userSetSpeechLocale", locale=locale, provider=enum_value(provider))
    def speech_options(self, *, partial_utterances: bool, min_utterance_length: float | None = None) -> dict[str, Any]:
        return self._call("userSetSpeechOptions", partialUtterances=partial_utterances, minUtteranceLength=min_utterance_length)


class SharedNotesController(_Controller):
    def create(self, external_id: str) -> dict[str, Any]: return self._call("sharedNotesCreateSession", sharedNotesExtId=external_id)
    def pin(self, external_id: str, pinned: bool = True) -> dict[str, Any]: return self._call("sharedNotesSetPinned", sharedNotesExtId=external_id, pinned=pinned)


class RecordingController(_Controller):
    def status(self) -> Recording:
        rows = self._read(RECORDING, "meeting_recording")
        return Recording.from_graphql(rows[0]) if rows else Recording()

    def start(self) -> dict[str, Any]: return self._call("meetingRecordingSetStatus", recording=True)
    def stop(self) -> dict[str, Any]: return self._call("meetingRecordingSetStatus", recording=False)


class CamerasController(_Controller):
    def list(self) -> list[Camera]:
        return [Camera.from_graphql(row) for row in self._read(CAMERAS, "user_camera")]

    def start(self, stream: str) -> dict[str, Any]: return self._call("cameraBroadcastStart", stream=stream)
    def stop(self, stream: str) -> dict[str, Any]: return self._call("cameraBroadcastStop", stream=stream)
    def eject(self, user_id: str) -> dict[str, Any]: return self._call("userEjectCameras", userId=user_id)
    def pin(self, user_id: str, pinned: bool = True) -> dict[str, Any]: return self._call("userSetCameraPinned", userId=user_id, pinned=pinned)
    def show_as_content(self, stream_id: str, enabled: bool = True) -> dict[str, Any]: return self._call("cameraSetShowAsContent", streamId=stream_id, showAsContent=enabled)


class WhiteboardController(_Controller):
    def current(self, *, page_id: str | None = None) -> list[WhiteboardAnnotation]:
        """Return current annotations, optionally limited to one page."""
        rows = self._read(WHITEBOARD, "pres_annotation_curr")
        if page_id is not None:
            rows = [row for row in rows if row.get("pageId") == page_id]
        return [
            WhiteboardAnnotation(page_id=row.get("pageId", ""), payload=row)
            for row in rows
        ]

    def submit(self, annotation: WhiteboardAnnotation) -> dict[str, Any]: return self._call("presAnnotationSubmit", pageId=annotation.page_id, annotations=annotation.payload)
    def delete(self, page_id: str, annotation_ids: Iterable[str]) -> dict[str, Any]: return self._call("presAnnotationDelete", pageId=page_id, annotationsIds=list(annotation_ids))
    def clear(self, page_id: str) -> dict[str, Any]: return self._call("presAnnotationDeleteAll", pageId=page_id)
    def cursor(self, cursor: WhiteboardCursor) -> dict[str, Any]: return self._call("presentationPublishCursor", whiteboardId=cursor.whiteboard_id, xPercent=cursor.x_percent, yPercent=cursor.y_percent)
    def set_access(self, user_ids: Iterable[str] = (), *, enabled: bool = True, all_users: bool = False) -> dict[str, Any]: return self._call("userSetWhiteboardWriteAccess", userIds=list(user_ids) or None, allUsers=all_users, whiteboardWriteAccess=enabled)


class GuestsController(_Controller):
    def list(self) -> list[Guest]:
        return [Guest.from_graphql(row) for row in self._read(GUESTS, "user_guest")]

    def policy(self, value: GuestPolicy | str) -> dict[str, Any]: return self._call("guestUsersSetPolicy", guestPolicy=enum_value(value))
    def lobby_message(self, text: str, *, guest_id: str | None = None) -> dict[str, Any]:
        return self._call("guestUsersSetLobbyMessagePrivate" if guest_id else "guestUsersSetLobbyMessage", **({"guestId": guest_id, "message": text} if guest_id else {"message": text}))
    def approve(self, guest_ids: Iterable[str]) -> dict[str, Any]: return self._approval(guest_ids, GuestApproval.APPROVE)
    def deny(self, guest_ids: Iterable[str]) -> dict[str, Any]: return self._approval(guest_ids, GuestApproval.DENY)
    def _approval(self, guest_ids: Iterable[str], status: GuestApproval) -> dict[str, Any]: return self._call("guestUsersSubmitApprovalStatus", guests=[{"guest": guest_id, "status": status.value} for guest_id in guest_ids])


class TimerController(_Controller):
    def activate(self, seconds: int, *, running: bool = True, stopwatch: bool = False, track: str | None = None) -> dict[str, Any]: return self._call("timerActivate", stopwatch=stopwatch, running=running, time=seconds, track=track)
    def deactivate(self) -> dict[str, Any]: return self._call("timerDeactivate")
    def start(self) -> dict[str, Any]: return self._call("timerStart")
    def stop(self) -> dict[str, Any]: return self._call("timerStop")
    def reset(self) -> dict[str, Any]: return self._call("timerReset")
    def set_time(self, seconds: int) -> dict[str, Any]: return self._call("timerSetTime", time=seconds)
    def set_track(self, track: str) -> dict[str, Any]: return self._call("timerSetSongTrack", track=track)
    def stopwatch(self, enabled: bool = True) -> dict[str, Any]: return self._call("timerSwitchMode", stopwatch=enabled)


class ExternalVideoController(_Controller):
    def start(self, url: str) -> dict[str, Any]: return self._call("externalVideoStart", externalVideoUrl=url)
    def stop(self) -> dict[str, Any]: return self._call("externalVideoStop")
    def update(self, *, status: str, rate: float, time: float, state: float) -> dict[str, Any]: return self._call("externalVideoUpdate", status=status, rate=rate, time=time, state=state)


class PluginsController(_Controller):
    def push(self, plugin: str, channel: str, subchannel: str, payload: dict[str, Any], *, roles: Iterable[str] = (), users: Iterable[str] = ()) -> dict[str, Any]: return self._call("pluginDataChannelPushEntry", pluginName=plugin, channelName=channel, subChannelName=subchannel, payloadJson=json.dumps(payload), toRoles=list(roles), toUserIds=list(users))
    def replace(self, plugin: str, channel: str, subchannel: str, entry_id: str, payload: dict[str, Any]) -> dict[str, Any]: return self._call("pluginDataChannelReplaceEntry", pluginName=plugin, channelName=channel, subChannelName=subchannel, entryId=entry_id, payloadJson=json.dumps(payload))
    def delete(self, plugin: str, channel: str, subchannel: str, entry_id: str) -> dict[str, Any]: return self._call("pluginDataChannelDeleteEntry", pluginName=plugin, channelName=channel, subChannelName=subchannel, entryId=entry_id)
    def reset(self, plugin: str, channel: str, subchannel: str) -> dict[str, Any]: return self._call("pluginDataChannelReset", pluginName=plugin, channelName=channel, subChannelName=subchannel)
    def persist(self, plugin: str, event: str, payload: dict[str, Any]) -> dict[str, Any]: return self._call("pluginPersistEvent", pluginName=plugin, eventName=event, payloadJson=payload)
    def upsert_learning_data(self, plugin: str, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        return self._call("pluginLearningAnalyticsDashboardUpsertUserData", pluginName=plugin, targetUserId=user_id, userDataForLearningAnalyticsDashboard=data)
    def delete_learning_data(self, plugin: str, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        return self._call("pluginLearningAnalyticsDashboardDeleteUserData", pluginName=plugin, targetUserId=user_id, userDataForLearningAnalyticsDashboard=data)
    def clear_learning_data(self, plugin: str, card_title: str) -> dict[str, Any]:
        return self._call("pluginLearningAnalyticsDashboardClearAllUsersData", pluginName=plugin, cardTitle=card_title)

    def listen(self, handler, *, plugin: str | None = None, channel: str | None = None,
               subchannel: str | None = None) -> None:
        """Register a filtered plugin data-channel listener before ``run()``."""
        if not callable(handler):
            raise TypeError("plugin listener must be callable")

        def dispatch(data: dict[str, Any]) -> None:
            for row in data.get("pluginDataChannelEntry") or []:
                entry = PluginDataEntry.from_graphql(row)
                if plugin is not None and entry.plugin_name != plugin:
                    continue
                if channel is not None and entry.channel_name != channel:
                    continue
                if subchannel is not None and entry.subchannel_name != subchannel:
                    continue
                handler(entry)

        self._client.watch(PLUGIN_DATA, dispatch)


class ScreenshareController(_Controller):
    """Read screenshare state and control its BBB layout treatment."""

    def current(self) -> Screenshare | None:
        rows = self._read(SCREENSHARE, "screenshare")
        return Screenshare.from_graphql(rows[0]) if rows else None

    def set_as_content(self, enabled: bool = True) -> dict[str, Any]:
        return self._call("meetingLayoutSetScreenshareAsContent", screenshareAsContent=enabled)


class ReactionsController(_Controller):
    """Current-user reaction, emoji-status, presence, and hand controls."""

    def set(self, emoji: str) -> dict[str, Any]:
        return self._call("userSetReactionEmoji", reactionEmoji=emoji)

    def set_status(self, emoji: str) -> dict[str, Any]:
        return self._call("userSetEmojiStatus", emoji=emoji)

    def clear_all(self) -> dict[str, Any]:
        return self._call("allUsersClearReaction")

    def clear_all_statuses(self) -> dict[str, Any]:
        return self._call("allUsersClearEmoji")

    def set_away(self, away: bool = True) -> dict[str, Any]:
        return self._call("userSetAway", away=away)

    def raise_hand(self) -> dict[str, Any]:
        return self._call("userSetRaiseHand", raiseHand=True)

    def lower_hand(self) -> dict[str, Any]:
        return self._call("userSetRaiseHand", raiseHand=False)


class MediaGroupsController(_Controller):
    @staticmethod
    def _participant_values(participants: Iterable[MediaGroupParticipant | dict[str, Any]]) -> list[dict[str, Any]]:
        return [participant.input() if isinstance(participant, MediaGroupParticipant) else participant for participant in participants]

    @staticmethod
    def _state_values(entries: Iterable[MediaGroupState | dict[str, Any]]) -> list[dict[str, Any]]:
        return [entry.input() if isinstance(entry, MediaGroupState) else entry for entry in entries]

    def create(self, group_id: str, media_type: MediaType | str, *, locked: bool = False, record: bool = False, senders: Iterable[MediaGroupParticipant | dict[str, Any]] = (), receivers: Iterable[MediaGroupParticipant | dict[str, Any]] = ()) -> dict[str, Any]:
        return self._call("mediaGroupCreate", id=group_id, mediaType=enum_value(media_type), locked=locked, record=record, senders=self._participant_values(senders), receivers=self._participant_values(receivers))
    def destroy(self, group_id: str, media_type: MediaType | str) -> dict[str, Any]: return self._call("mediaGroupDestroy", id=group_id, mediaType=enum_value(media_type))
    def set_user_state(self, user_id: str, entries: Iterable[MediaGroupState | dict[str, Any]], *, scope: MediaScope | str = MediaScope.ROOM) -> dict[str, Any]:
        return self._call("mediaGroupSetUserState", userId=user_id, entries=self._state_values(entries), scope=enum_value(scope))


class MeetingSettingsController(_Controller):
    def role(self, user_id: str, value: Role | str) -> dict[str, Any]: return self._call("userSetRole", userId=user_id, role=enum_value(value))
    def layout(self, value: Layout | str, *, sync: bool = False, presentation_open: bool = True, resizing: bool = False, camera_position: str | None = None, focused_camera: str = "", presentation_video_rate: float = 1.0) -> dict[str, Any]: return self._call("meetingLayoutSetProps", layout=enum_value(value), syncWithPresenterLayout=sync, presentationIsOpen=presentation_open, isResizing=resizing, cameraPosition=camera_position, focusedCamera=focused_camera, presentationVideoRate=presentation_video_rate)


class LocksController(_Controller):
    """Meeting lock settings with a complete typed BBB input object."""

    def set(self, settings: LockSettings) -> dict[str, Any]:
        return self._call("meetingLockSettingsSetProps", **settings.input())

    def user_public_chat(self, user_id: str, *, disabled: bool) -> dict[str, Any]:
        return self._call("userSetUserLockSettings", userId=user_id, disablePubChat=disabled)
