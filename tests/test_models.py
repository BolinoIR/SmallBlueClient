"""Round-trip coverage for all public typed BBB models."""
from __future__ import annotations

import unittest

from sbc.models import (
    BreakoutRoom, Camera, Caption, Chat, ChatMessage, ExternalVideo, Guest,
    LayoutState, LockSettings, MediaGroupParticipant, MediaGroupState,
    Notification, Poll, Presentation, PresentationDocument, Recording,
    Screenshare, SharedNotesSession, Timer, User,
)


class ModelTests(unittest.TestCase):
    def test_primary_models_parse_bbb_graphql_rows(self) -> None:
        user = User.from_graphql({
            "userId": "u1", "name": "Ada", "presenter": True, "isModerator": True,
            "voice": {"joined": True, "muted": False, "talking": True, "listenOnly": True},
            "cameras": [{"streamId": "cam-1"}],
        })
        self.assertEqual((user.id, user.name, user.camera_stream_ids), ("u1", "Ada", ("cam-1",)))
        self.assertTrue(user.is_presenter and user.voice_joined and user.talking and user.listen_only)
        self.assertEqual(ChatMessage.from_graphql({"messageId": "m", "message": "hi", "senderName": "Ada", "chatId": "c"}).chat_id, "c")
        self.assertEqual(Chat.from_graphql({"chatId": "c", "public": True, "users": [{"userId": "u1"}]}).participant_ids, ("u1",))
        self.assertEqual(Presentation.from_graphql({"presentationId": "p", "pageId": "p/1", "num": 1}).page_number, 1)
        document = PresentationDocument.from_graphql({"presentationId": "p", "name": "slides", "downloadable": True, "totalPages": 3})
        self.assertEqual((document.id, document.total_pages), ("p", 3))

    def test_feature_models_parse_all_controller_domains(self) -> None:
        poll = Poll.from_graphql({"pollId": "poll", "questionText": "Ready?", "published": True, "options": [{"optionId": "a", "optionDesc": "Yes", "optionResponsesCount": 2}]})
        self.assertEqual((poll.id, poll.options[0].responses), ("poll", 2))
        self.assertEqual(Timer.from_graphql({"active": True, "running": True, "time": 9, "songTrack": "x"}).seconds, 9)
        self.assertTrue(Camera.from_graphql({"streamId": "cam", "showAsContent": True}).content)
        self.assertTrue(Caption.from_graphql({"captionId": "cap", "captionText": "text", "locale": "en"}).text == "text")
        room = BreakoutRoom.from_graphql({"name": "Room", "sequence": 1, "shortName": "R", "freeJoin": True})
        self.assertEqual(room.input()["users"], [])
        locks = LockSettings.from_graphql({"disableCam": True, "disablePrivChat": True})
        self.assertTrue(locks.input()["disableCam"] and locks.input()["disablePrivChat"])
        self.assertTrue(Screenshare.from_graphql({"stream": "screen", "hasAudio": True}).has_audio)
        self.assertEqual(ExternalVideo.from_graphql({"externalVideoId": "v", "playerCurrentTime": 2.5}).current_time, 2.5)
        self.assertEqual(Notification.from_graphql({"messageId": "n", "notificationType": "info"}).type, "info")
        self.assertTrue(SharedNotesSession.from_graphql({"sharedNotesExtId": "notes", "pinned": True}).pinned)
        self.assertTrue(Recording.from_graphql({"isRecording": True, "recordFullDurationMedia": True}).recording)
        self.assertEqual(Guest.from_graphql({"userId": "g", "guestStatus": "WAITING", "user": {"name": "Guest"}}).name, "Guest")
        self.assertEqual(MediaGroupParticipant("u", sender=True).input()["sender"], True)
        self.assertEqual(MediaGroupState("group", "audio").input()["groupId"], "group")
        self.assertEqual(LayoutState.from_graphql({"layout": "UNIFIED_LAYOUT", "presentationIsOpen": False}).presentation_open, False)
