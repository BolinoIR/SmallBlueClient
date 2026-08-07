"""Priorities, filters, one-shot callbacks, async handlers, and ``off``."""
from pathlib import Path

import sbc


SESSION = Path(__file__).with_name("test.sbc")


def main() -> None:
    bot = sbc.client(SESSION)

    @bot.once(sbc.Event.USER_JOINED, when=lambda user: user.guest)
    async def greet_first_guest(user: sbc.User) -> None:
        bot.chat.send(f"Welcome, {user.name}.")

    @bot.on(sbc.Event.USER_JOINED, priority=20)
    def audit_join(user: sbc.User) -> None:
        print(f"joined: {user.name} ({user.id})")

    def activity_error(error: Exception) -> None:
        print(f"event stream issue: {error}")

    bot.on(sbc.Event.ERROR, activity_error)

    print("Event-pattern bot is running. Press Ctrl+C to stop.")
    try:
        bot.run()
    finally:
        bot.off(sbc.Event.ERROR, activity_error)
        bot.close()


if __name__ == "__main__":
    main()
