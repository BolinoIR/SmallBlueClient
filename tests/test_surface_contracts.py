"""Contract tests for the entire public controller/action surface.

These tests deliberately use a local recording transport. They verify the
Python API constructs the source-derived BBB mutation and variable names for
every embedded action without changing a real meeting.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

import sbc
from sbc.core.client import SBCClient
from sbc.core.session import SBCSession
from sbc.models import BreakoutRoom, Caption, ChatMessage, LockSettings, MediaGroupParticipant, MediaGroupState, WhiteboardAnnotation, WhiteboardCursor


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, query: str, variables: dict[str, object]) -> dict[str, object]:
        self.calls.append((query, variables))
        if "SBCPresentation" in query:
            return {"data": {"pres_page_curr": [{"presentationId": "pres", "pageId": "page-1", "num": 1}]}}
        return {"data": {}}


class ActionRecorder:
    """Action-compatible recorder for testing high-level method mappings."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call(self, name: str, **variables: object) -> dict[str, object]:
        self.calls.append((name, variables))
        return {name: True}

    def __getattr__(self, name: str):
        return lambda **variables: self.call(name, **variables)


class ActionContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = SBCClient(
            SBCSession(
                server="https://bbb.example",
                websocket_url="wss://bbb.example/graphql",
                meeting_id="meeting-1",
                snapshot={"presentation_pages": [
                    {"presentationId": "pres", "pageId": "page-1"},
                    {"presentationId": "pres", "pageId": "page-2"},
                ]},
            ),
            connect=False,
        )
        self.transport = RecordingTransport()
        self.client.graphql.transport = self.transport
        self.actions = ActionRecorder()
        self.client.actions = self.actions  # type: ignore[assignment]

    def test_every_embedded_action_invokes_a_complete_local_graphql_operation(self) -> None:
        """All 109 direct actions work through the Actions facade and transport."""
        client = SBCClient(SBCSession(server="https://bbb.example", websocket_url="wss://bbb.example/graphql"), connect=False)
        transport = RecordingTransport()
        client.graphql.transport = transport

        for name in client.actions.names:
            mutation = client.actions.schema(name)
            variables: dict[str, object] = {}
            for argument in mutation.arguments:
                if not argument.required:
                    continue
                if argument.is_list:
                    variables[argument.name] = []
                elif argument.type == "Boolean":
                    variables[argument.name] = False
                elif argument.type == "Int":
                    variables[argument.name] = 0
                elif argument.type == "Float":
                    variables[argument.name] = 0.0
                elif argument.type == "json":
                    variables[argument.name] = {}
                elif argument.type in {"BreakoutRoom", "GuestUserApprovalStatus", "MediaGroupParticipant", "MediaGroupStateEntry"}:
                    variables[argument.name] = {}
                else:
                    variables[argument.name] = "test"
            result = client.actions.call(name, **variables)
            self.assertEqual(result, {})
            document, sent = transport.calls[-1]
            self.assertIn(name, document)
            self.assertEqual(sent, variables)

        self.assertEqual(len(transport.calls), 109)

    def test_core_controller_methods_use_the_expected_bbb_actions(self) -> None:
        chat = self.client.chat
        chat.send("hello")
        chat.reply(ChatMessage("message-1", "question", "Ada", chat_id="chat-1"), "answer")
        chat.mark_read(chat_id="chat-1", at="2026-08-07T00:00:00Z")
        chat.create_private("user-1")
        chat.edit("chat-1", "message-1", "edited")
        chat.delete("chat-1", "message-1")
        chat.react("chat-1", "message-1", "👍")
        chat.remove_reaction("chat-1", "message-1", "👍")
        chat.clear_public_history()
        chat.set_typing("chat-1")
        self.client.users.mute_all(except_presenter=True)
        self.client.users.mute("user-1")
        self.client.users.unmute("user-1")
        self.client.users.remove("user-1", ban=True)
        self.client.presentation.set_page("pres", "page-2")
        self.client.presentation.next_page()
        self.client.presentation.set_current("pres")
        self.client.presentation.remove("pres")
        self.client.presentation.export("pres")
        self.client.presentation.set_downloadable("pres")

        names = [name for name, _ in self.actions.calls]
        self.assertEqual(names, [
            "chatSendMessage", "chatSendMessage", "chatSetLastSeen", "chatCreateWithUser",
            "chatEditMessage", "chatDeleteMessage", "chatSendMessageReaction",
            "chatDeleteMessageReaction", "chatPublicClearHistory", "chatSetTyping",
            "meetingSetMuted", "userSetMuted", "userSetMuted", "userEjectFromMeeting",
            "presentationSetPage", "presentationSetPage", "presentationSetCurrent",
            "presentationRemove", "presentationExport", "presentationSetDownloadable",
        ])
        self.assertEqual(self.actions.calls[2][1], {"chatId": "chat-1", "lastSeenAt": "2026-08-07T00:00:00Z"})
        self.assertEqual(self.actions.calls[15][1], {"presentationId": "pres", "pageId": "page-2"})

    def test_every_advanced_controller_method_maps_to_a_source_action(self) -> None:
        self.client.polls.create("Ready?", ["Yes", "No"], poll_type=sbc.PollType.YES_NO, poll_id="poll")
        self.client.polls.publish("poll", show_answers=True)
        self.client.polls.vote("poll", [1])
        self.client.polls.answer("poll", "Yes")
        self.client.polls.cancel()
        room = BreakoutRoom("Room 1", 1, users=("user-1",))
        self.client.breakouts.create([room], duration_minutes=5, record=True, capture_notes=True, capture_slides=True, invite_moderators=True)
        self.client.breakouts.end_all(); self.client.breakouts.move("user-1", from_room="room-a", to_room="room-b")
        self.client.breakouts.set_time(10); self.client.breakouts.message_all("hello"); self.client.breakouts.request_join_url("room-a"); self.client.breakouts.dismiss_invite()
        caption = Caption("caption-1", "text", "en", start=1, end=2, final=True)
        self.client.captions.add_locale("en"); self.client.captions.set_locale("en"); self.client.captions.submit(caption); self.client.captions.submit_transcript("caption-1", "text", "en")
        self.client.captions.speech_locale("en"); self.client.captions.speech_options(partial_utterances=True, min_utterance_length=0.3)
        self.client.notes.create("notes-1"); self.client.notes.pin("notes-1")
        self.client.recording.start(); self.client.recording.stop()
        self.client.cameras.start("camera-1"); self.client.cameras.stop("camera-1"); self.client.cameras.eject("user-1"); self.client.cameras.pin("user-1"); self.client.cameras.show_as_content("camera-1")
        self.client.whiteboard.submit(WhiteboardAnnotation("page-1", {"id": "a1"})); self.client.whiteboard.delete("page-1", ["a1"]); self.client.whiteboard.clear("page-1")
        self.client.whiteboard.cursor(WhiteboardCursor("page-1", 0.1, 0.2)); self.client.whiteboard.set_access(["user-1"])
        self.client.guests.policy(sbc.GuestPolicy.ASK_MODERATOR); self.client.guests.lobby_message("wait"); self.client.guests.lobby_message("wait", guest_id="guest-1")
        self.client.guests.approve(["guest-1"]); self.client.guests.deny(["guest-2"])
        self.client.timer.activate(10); self.client.timer.deactivate(); self.client.timer.start(); self.client.timer.stop(); self.client.timer.reset(); self.client.timer.set_time(5); self.client.timer.set_track("track"); self.client.timer.stopwatch()
        self.client.external_video.start("https://video.example"); self.client.external_video.stop(); self.client.external_video.update(status="playing", rate=1.0, time=2.0, state=3.0)
        self.client.plugins.push("plugin", "channel", "sub", {"a": 1}, roles=["VIEWER"], users=["user-1"]); self.client.plugins.replace("plugin", "channel", "sub", "entry", {"a": 2}); self.client.plugins.delete("plugin", "channel", "sub", "entry"); self.client.plugins.reset("plugin", "channel", "sub"); self.client.plugins.persist("plugin", "event", {"a": 1})
        self.client.plugins.upsert_learning_data("plugin", "user-1", {"score": 1}); self.client.plugins.delete_learning_data("plugin", "user-1", {"score": 1}); self.client.plugins.clear_learning_data("plugin", "card")
        participant = MediaGroupParticipant("user-1", sender=True)
        self.client.media_groups.create("group-1", sbc.MediaType.AUDIO, senders=[participant]); self.client.media_groups.destroy("group-1", sbc.MediaType.AUDIO)
        self.client.media_groups.set_user_state("user-1", [MediaGroupState("group-1", "audio", sender=True)])
        self.client.settings.role("user-1", sbc.Role.MODERATOR); self.client.settings.layout(sbc.Layout.UNIFIED)
        self.client.locks.set(LockSettings(disable_camera=True)); self.client.locks.user_public_chat("user-1", disabled=True)
        self.client.screenshare.set_as_content(); self.client.reactions.set("thumbsUp"); self.client.reactions.set_status("happy"); self.client.reactions.clear_all(); self.client.reactions.clear_all_statuses(); self.client.reactions.set_away(); self.client.reactions.raise_hand(); self.client.reactions.lower_hand()

        names = {name for name, _ in self.actions.calls}
        expected = {
            "pollCreate", "pollPublishResult", "pollSubmitUserVote", "pollSubmitUserTypedVote", "pollCancel",
            "breakoutRoomCreate", "breakoutRoomEndAll", "breakoutRoomMoveUser", "breakoutRoomSetTime", "breakoutRoomSendMessageToAll", "breakoutRoomRequestJoinUrl", "breakoutRoomSetInviteDismissed",
            "captionAddLocale", "userSetCaptionLocale", "captionSubmitText", "captionSubmitTranscript", "userSetSpeechLocale", "userSetSpeechOptions",
            "sharedNotesCreateSession", "sharedNotesSetPinned", "meetingRecordingSetStatus", "cameraBroadcastStart", "cameraBroadcastStop", "userEjectCameras", "userSetCameraPinned", "cameraSetShowAsContent",
            "presAnnotationSubmit", "presAnnotationDelete", "presAnnotationDeleteAll", "presentationPublishCursor", "userSetWhiteboardWriteAccess",
            "guestUsersSetPolicy", "guestUsersSetLobbyMessage", "guestUsersSetLobbyMessagePrivate", "guestUsersSubmitApprovalStatus",
            "timerActivate", "timerDeactivate", "timerStart", "timerStop", "timerReset", "timerSetTime", "timerSetSongTrack", "timerSwitchMode",
            "externalVideoStart", "externalVideoStop", "externalVideoUpdate", "pluginDataChannelPushEntry", "pluginDataChannelReplaceEntry", "pluginDataChannelDeleteEntry", "pluginDataChannelReset", "pluginPersistEvent",
            "pluginLearningAnalyticsDashboardUpsertUserData", "pluginLearningAnalyticsDashboardDeleteUserData", "pluginLearningAnalyticsDashboardClearAllUsersData",
            "mediaGroupCreate", "mediaGroupDestroy", "mediaGroupSetUserState", "userSetRole", "meetingLayoutSetProps", "meetingLockSettingsSetProps", "userSetUserLockSettings", "meetingLayoutSetScreenshareAsContent",
            "userSetReactionEmoji", "userSetEmojiStatus", "allUsersClearReaction", "allUsersClearEmoji", "userSetAway", "userSetRaiseHand",
        }
        self.assertEqual(names, expected)
        self.assertEqual(self.actions.calls[-1][0], "userSetRaiseHand")
        self.assertEqual(self.actions.calls[-1][1], {"raiseHand": False})

    def test_mark_read_default_timestamp_is_utc_and_uses_public_group(self) -> None:
        self.client.chat.mark_read()
        name, variables = self.actions.calls[-1]
        self.assertEqual(name, "chatSetLastSeen")
        self.assertEqual(variables["chatId"], "MAIN-PUBLIC-GROUP-CHAT")
        self.assertGreater(datetime.fromisoformat(str(variables["lastSeenAt"]).replace("Z", "+00:00")), datetime(2020, 1, 1, tzinfo=timezone.utc))
