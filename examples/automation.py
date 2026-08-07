"""Simple one-shot moderation using SBC's high-level controllers."""
from pathlib import Path

import sbc


SESSION = Path(__file__).with_name("test.sbc")


def main() -> None:
    client = sbc.client(SESSION)
    try:
        meeting = client.meeting()
        moderators = [user for user in meeting.users() if user.is_moderator]

        print(f"{meeting.name}: {len(meeting.users())} participants")
        print("Moderators:", ", ".join(user.name for user in moderators) or "none")

        # High-level named parameters; no GraphQL strings or raw action names.
        client.users.mute_all(except_presenter=True)
        client.guests.policy(sbc.GuestPolicy.ASK_MODERATOR)
        client.chat.send("The room is now moderated by SBC.")
    finally:
        client.close()


if __name__ == "__main__":
    main()
