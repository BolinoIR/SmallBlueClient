"""A clean event-driven moderator bot."""
from pathlib import Path

import sbc


SESSION = Path(__file__).with_name("test.sbc")


def main() -> None:
    bot = sbc.client(SESSION)

    # Enums make event names discoverable and prevent spelling mistakes.
    @bot.on(sbc.Event.USER_JOINED, priority=10, when=lambda user: not user.bot)
    def welcome(user: sbc.User) -> None:
        bot.chat.send(f"Welcome {user.name}!")

    @bot.on(sbc.Event.HAND_RAISED)
    def raised_hand(user: sbc.User) -> None:
        print(f"{user.name} raised their hand")

    @bot.on("user_became_presenter")
    def presenter_changed(user: sbc.User) -> None:
        bot.chat.send(f"{user.name} is now presenting.")

    print("Moderation bot is running. Press Ctrl+C to stop.")
    try:
        bot.run()
    finally:
        bot.close()


if __name__ == "__main__":
    main()
