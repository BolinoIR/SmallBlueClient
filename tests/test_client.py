import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile

from sbc.core.client import SBCClient
from sbc.core.graphql import USERS, VOICE_ACTIVITY
from sbc.core.session import SBCSession

class Transport:
    def __init__(self): self.last_query = None; self.last_variables = None
    def execute(self, query, variables):
        self.last_query, self.last_variables = query, variables
        if "SBCLiveKitCredentials" in query:
            return {"data": {"user_current": [{"livekit": {"livekitToken": "livekit-test-token"}}]}}
        if "SBCUsers" in query:
            return {"data": {"user": [{"userId": "u1", "name": "Ada", "voice": {"joined": True, "muted": False}}]}}
        return {"data": {"meeting": [{"meetingId": "m1", "name": "Demo", "ended": False}]}}
    def subscribe(self, query, variables): return iter(())

class ClientTests(unittest.TestCase):
    def test_high_level_meeting_and_users_without_network(self):
        client = SBCClient(SBCSession(server="https://bbb.example", websocket_url="wss://bbb.example/graphql"), connect=False)
        client.graphql.transport = Transport()
        self.assertEqual(client.meeting().name, "Demo")
        self.assertEqual(client.meeting.users()[0].name, "Ada")

    def test_all_actions_are_loaded_and_snake_case_arguments_work(self):
        client = SBCClient(SBCSession(server="https://bbb.example", websocket_url="wss://bbb.example/graphql"), connect=False)
        transport = Transport(); client.graphql.transport = transport
        completed = []
        client.on("action_user_set_muted_completed", lambda variables, result: completed.append((variables, result)))
        self.assertEqual(len(client.actions.names), 109)
        client.actions.user_set_muted(user_id="u1", muted=True)
        self.assertIn("userSetMuted", transport.last_query)
        self.assertEqual(transport.last_variables, {"userId": "u1", "muted": True})
        self.assertEqual(completed[0][0], {"userId": "u1", "muted": True})

    def test_chat_uses_bbb_public_chat_group_not_meeting_id(self):
        client = SBCClient(SBCSession(
            server="https://bbb.example",
            websocket_url="wss://bbb.example/graphql",
            meeting_id="a-real-bbb-meeting-id",
        ), connect=False)
        transport = Transport()
        client.graphql.transport = transport

        client.chat.send("Hello from SBC")

        self.assertIn("chatSendMessage", transport.last_query)
        self.assertEqual(transport.last_variables, {
            "chatId": "MAIN-PUBLIC-GROUP-CHAT",
            "chatMessageInMarkdownFormat": "Hello from SBC",
            "replyToMessageId": None,
        })

    def test_chat_reply_uses_the_original_message_and_chat(self):
        client = SBCClient(SBCSession(server="https://bbb.example", websocket_url="wss://bbb.example/graphql"), connect=False)
        transport = Transport()
        client.graphql.transport = transport
        message = __import__("sbc").ChatMessage(
            id="message-1", text="Question", sender_name="Ada", chat_id="private-chat-1",
        )

        client.chat.reply(message, "Answer")

        self.assertEqual(transport.last_variables, {
            "chatId": "private-chat-1",
            "chatMessageInMarkdownFormat": "Answer",
            "replyToMessageId": "message-1",
        })

    def test_user_subscription_uses_real_bbb_presenter_field(self):
        self.assertIn("presenter", USERS)
        self.assertNotIn("isPresenter", USERS)

    def test_voice_activity_uses_bbb_talking_stream(self):
        client = SBCClient(SBCSession(server="https://bbb.example", websocket_url="wss://bbb.example/graphql"), connect=False)
        heard = []
        client.on("user_talking", heard.append)
        self.assertEqual(client._enabled_event_streams, {"voice_activity"})
        client._watch_voice_activity({"user_voice_activity_stream": [{
            "userId": "u2", "talking": True, "muted": False, "leftVoiceConf": False,
            "user": {"name": "Grace"},
        }]})
        self.assertEqual([(user.id, user.name, user.talking) for user in heard], [("u2", "Grace", True)])
        self.assertIn("voice{joined muted talking}", VOICE_ACTIVITY)

    def test_user_field_changes_and_custom_subscriptions_are_exposed(self):
        client = SBCClient(SBCSession(server="https://bbb.example", websocket_url="wss://bbb.example/graphql"), connect=False)
        roles = []
        client.on("user_role_changed", lambda user, old, new: roles.append((user.id, old, new)))
        client._watch_users({"user": [{"userId": "u1", "name": "Ada", "role": "VIEWER"}]})
        client._watch_users({"user": [{"userId": "u1", "name": "Ada", "role": "MODERATOR"}]})
        self.assertEqual(roles, [("u1", "VIEWER", "MODERATOR")])
        client.watch("subscription SBCNotifications { notification { messageId } }", lambda _: None)
        self.assertEqual(len(client._custom_streams), 1)
        with self.assertRaises(ValueError):
            client.watch("query { meeting { name } }", lambda _: None)

    def test_user_joined_ignores_initial_subscription_snapshot(self):
        client = SBCClient(SBCSession(server="https://bbb.example", websocket_url="wss://bbb.example/graphql"), connect=False)
        joined = []
        client.on("user_joined", joined.append)

        # The first subscription result contains everybody who was already in
        # the meeting, including the SBC identity. It is a baseline, not a
        # sequence of joins.
        client._watch_users({"user": [
            {"userId": "bot", "name": "SBC bot"},
            {"userId": "already-here", "name": "Ada"},
        ]})
        self.assertEqual(joined, [])

        client._watch_users({"user": [
            {"userId": "bot", "name": "SBC bot"},
            {"userId": "already-here", "name": "Ada"},
            {"userId": "new-user", "name": "Grace"},
        ]})
        self.assertEqual([(user.id, user.name) for user in joined], [("new-user", "Grace")])

    def test_chat_message_ignores_initial_history(self):
        client = SBCClient(SBCSession(server="https://bbb.example", websocket_url="wss://bbb.example/graphql"), connect=False)
        received = []
        client.on("chat_message", received.append)
        historical = {"messageId": "old", "chatId": "MAIN-PUBLIC-GROUP-CHAT", "message": "Before the bot", "senderName": "Ada"}
        fresh = {"messageId": "new", "chatId": "MAIN-PUBLIC-GROUP-CHAT", "message": "After the bot", "senderName": "Grace"}

        client._watch_chat({"chat_message_public": [historical]})
        self.assertEqual(received, [])
        client._watch_chat({"chat_message_public": [historical, fresh]})
        self.assertEqual([(message.id, message.text) for message in received], [("new", "After the bot")])

    def test_event_streams_are_opt_in_and_grouped_by_handler(self):
        client = SBCClient(SBCSession(server="https://bbb.example", websocket_url="wss://bbb.example/graphql"), connect=False)
        client.on("user_talking", lambda _: None)
        client.on("poll_ended", lambda _: None)
        client.on("user_role_changed", lambda *_: None)
        self.assertEqual(client._enabled_event_streams, {"voice_activity", "polls", "users"})

    def test_off_releases_an_unused_event_stream(self):
        client = SBCClient(SBCSession(server="https://bbb.example", websocket_url="wss://bbb.example/graphql"), connect=False)
        def handler(_): pass
        client.on("user_talking", handler)
        self.assertEqual(client._enabled_event_streams, {"voice_activity"})
        self.assertEqual(client.off("user_talking", handler), 1)
        self.assertEqual(client._enabled_event_streams, set())

    def test_media_credentials_are_fetched_from_real_current_user_field(self):
        client = SBCClient(SBCSession(server="https://bbb.example", websocket_url="wss://bbb.example/graphql"), connect=False)
        client.graphql.transport = Transport()
        credentials = client.media.credentials()
        self.assertEqual(credentials, {"url": "wss://bbb.example/livekit", "token": "livekit-test-token"})
        self.assertEqual(client.session.snapshot["livekit"]["token"], "livekit-test-token")

    def test_ensure_joined_uses_the_real_bbb_user_join_action(self):
        session = SBCSession(
            server="https://bbb.example", websocket_url="wss://bbb.example/graphql",
            snapshot={"current_user": {"auth_token": "bbb-auth", "joined": False, "currently_in_meeting": False}},
        )
        client = SBCClient(session, connect=False)
        transport = Transport(); client.graphql.transport = transport
        states = iter([
            {"auth_token": "bbb-auth", "joined": False, "currently_in_meeting": False},
            {"auth_token": "bbb-auth", "joined": True, "currently_in_meeting": True},
        ])
        client._fetch_current_user = lambda **_: next(states)  # type: ignore[method-assign]
        with patch("sbc.core.client.GraphQLClient") as join_client:
            self.assertTrue(client.ensure_joined(timeout=1))
        query, variables = join_client.return_value.mutation.call_args.args
        self.assertIn("userJoinMeeting", query)
        self.assertEqual(variables["authToken"], "bbb-auth")
        self.assertFalse(variables["clientIsMobile"])

    def test_new_join_sets_listener_mode_by_default(self):
        session = SBCSession(
            server="https://bbb.example", websocket_url="wss://bbb.example/graphql",
            snapshot={"current_user": {"auth_token": "bbb-auth", "joined": False, "currently_in_meeting": False}},
        )
        client = SBCClient(session, connect=False)
        client.graphql.transport = Transport()
        states = iter([
            {"auth_token": "bbb-auth", "joined": False, "currently_in_meeting": False},
            {"auth_token": "bbb-auth", "joined": True, "currently_in_meeting": True},
        ])
        client._fetch_current_user = lambda **_: next(states)  # type: ignore[method-assign]
        with patch("sbc.core.client.GraphQLClient"), patch.object(client.media.listener, "join") as join_listener:
            self.assertTrue(client.ensure_joined(timeout=1))
        self.assertIn("userSetListenOnlyInput", client.graphql.transport.last_query)
        self.assertEqual(client.graphql.transport.last_variables, {"listenOnlyInputDevice": True})
        join_listener.assert_called_once()

    def test_new_join_can_start_muted_microphone_mode(self):
        session = SBCSession(
            server="https://bbb.example", websocket_url="wss://bbb.example/graphql",
            snapshot={"current_user": {"auth_token": "bbb-auth", "joined": False, "currently_in_meeting": False}},
        )
        client = SBCClient(session, connect=False, listen_only=False)
        states = iter([
            {"auth_token": "bbb-auth", "joined": False, "currently_in_meeting": False},
            {"auth_token": "bbb-auth", "joined": True, "currently_in_meeting": True},
        ])
        client._fetch_current_user = lambda **_: next(states)  # type: ignore[method-assign]
        with patch("sbc.core.client.GraphQLClient"), patch.object(client.media.microphone, "join") as join_microphone:
            self.assertTrue(client.ensure_joined(timeout=1))
        join_microphone.assert_called_once()

    def test_runtime_join_state_never_rewrites_the_loaded_session_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "teacher.sbc"
            client = SBCClient(SBCSession(server="https://bbb.example", websocket_url="wss://bbb.example/graphql"), connect=False, session_path=path)
            client._update_current_user({"userId": "u1", "authToken": "secret", "joined": True, "currentlyInMeeting": True})
            self.assertFalse(path.exists())

    def test_client_is_a_context_manager(self):
        client = SBCClient(SBCSession(server="https://bbb.example", websocket_url="wss://bbb.example/graphql"), connect=False)
        with client as current:
            self.assertIs(current, client)
        self.assertTrue(client._stop.is_set())
