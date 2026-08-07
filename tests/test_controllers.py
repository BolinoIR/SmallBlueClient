import unittest

import sbc
from sbc.core.client import SBCClient
from sbc.models import LockSettings, MediaGroupParticipant, MediaGroupState
from sbc.core.session import SBCSession


class Transport:
    def __init__(self):
        self.last_query = ""
        self.last_variables = {}

    def execute(self, query, variables):
        self.last_query = query
        self.last_variables = variables
        return {"data": {}}


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.client = SBCClient(
            SBCSession(server="https://bbb.example", websocket_url="wss://bbb.example/graphql"),
            connect=False,
        )
        self.transport = Transport()
        self.client.graphql.transport = self.transport

    def test_typed_poll_and_lock_controller_values(self):
        poll_id = self.client.polls.create(
            "Ready?", ["Yes", "No"], poll_type=sbc.PollType.YES_NO, poll_id="poll-1"
        )
        self.assertEqual(poll_id, "poll-1")
        self.assertIn("pollCreate", self.transport.last_query)
        self.assertEqual(self.transport.last_variables["pollType"], "YN")

        self.client.locks.set(LockSettings(disable_camera=True, lock_on_join=True))
        self.assertIn("meetingLockSettingsSetProps", self.transport.last_query)
        self.assertTrue(self.transport.last_variables["disableCam"])
        self.assertTrue(self.transport.last_variables["lockOnJoin"])

    def test_media_group_models_and_plugin_dashboard_are_ergonomic(self):
        participant = MediaGroupParticipant("u1", sender=True)
        self.client.media_groups.create("group-1", sbc.MediaType.AUDIO, senders=[participant])
        self.assertIn("mediaGroupCreate", self.transport.last_query)
        self.assertEqual(self.transport.last_variables["mediaType"], "audio")
        self.assertEqual(self.transport.last_variables["senders"][0]["userId"], "u1")

        state = MediaGroupState("group-1", "audio", sender=True)
        self.client.media_groups.set_user_state("u1", [state])
        self.assertIn("mediaGroupSetUserState", self.transport.last_query)
        self.assertEqual(self.transport.last_variables["entries"][0]["groupId"], "group-1")

        self.client.plugins.upsert_learning_data("demo", "u1", {"score": 100})
        self.assertIn("pluginLearningAnalyticsDashboardUpsertUserData", self.transport.last_query)

    def test_high_level_read_controllers_return_typed_models(self):
        class ReadTransport(Transport):
            def execute(self, query, variables):
                self.last_query, self.last_variables = query, variables
                if "SBCPolls" in query:
                    return {"data": {"poll": [{"pollId": "p1", "questionText": "Ready?", "options": []}]}}
                if "SBCBreakoutRooms" in query:
                    return {"data": {"breakoutRoom": [{"name": "Room 1", "sequence": 1}]}}
                if "SBCCameras" in query:
                    return {"data": {"user_camera": [{"streamId": "cam", "userId": "u1"}]}}
                if "SBCRecording" in query:
                    return {"data": {"meeting_recording": [{"isRecording": True}]}}
                if "SBCGuests" in query:
                    return {"data": {"user_guest": [{"userId": "u2", "guestStatus": "WAITING", "user": {"name": "Guest"}}]}}
                if "SBCCaptions" in query:
                    return {"data": {"caption": [{"captionId": "c1", "captionText": "hello", "locale": "en"}]}}
                return {"data": {"pres_annotation_curr": [{"pageId": "page", "annotationId": "a1"}]}}

        self.client.graphql.transport = ReadTransport()
        self.assertEqual(self.client.polls.list()[0].question, "Ready?")
        self.assertEqual(self.client.breakout_rooms.list()[0].name, "Room 1")
        self.assertEqual(self.client.cameras.list()[0].stream_id, "cam")
        self.assertTrue(self.client.recordings.status().recording)
        self.assertEqual(self.client.guests.list()[0].name, "Guest")
        self.assertEqual(self.client.captions.transcript()[0].text, "hello")
        self.assertEqual(self.client.whiteboards.current()[0].page_id, "page")
