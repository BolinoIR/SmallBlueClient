"""Modern controller syntax: enums, typed inputs, and no raw GraphQL."""
from __future__ import annotations

import argparse
from pathlib import Path

import sbc


SESSION = Path(__file__).with_name("test.sbc")


def configure(client: sbc.SBCClient) -> None:
    """Apply a representative set of high-level BBB controls."""
    client.guests.policy(sbc.GuestPolicy.ASK_MODERATOR)
    client.locks.set(
        sbc.LockSettings(disable_microphone=True, lock_on_join=True),
    )

    poll_id = client.polls.create(
        "Is the lesson clear?",
        ["Yes", "No"],
        poll_type=sbc.PollType.YES_NO,
    )
    client.polls.publish(poll_id, show_answers=True)

    room = sbc.BreakoutRoom(
        name="Discussion group",
        sequence=1,
        users=("student-user-id",),
        free_join=True,
    )
    client.breakout_rooms.create([room], duration_minutes=10)

    client.timer.activate(5 * 60, track="noSound")
    client.external_videos.start("https://www.youtube.com/watch?v=dQw4w9WgXcQ")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="run the mutations")
    args = parser.parse_args()

    if not args.apply:
        print("Dry run. Re-run with --apply to execute the controller calls.")
        return

    client = sbc.client(SESSION)
    try:
        configure(client)
        print("Typed BBB controls applied.")
    finally:
        client.close()


if __name__ == "__main__":
    main()
