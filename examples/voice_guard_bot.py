"""Play a warning and remove anyone else that BBB detects talking."""
from pathlib import Path
from threading import Lock
from time import sleep

import sbc


SESSION = Path(__file__).with_name("test.sbc")
WARNING_CLIP = Path(__file__).with_name("test.mp3")


def main() -> None:
    # Full audio mode warms the Python publisher while remaining muted. It makes
    # the warning faster than negotiating a fresh audio session after speech.
    bot = sbc.client(SESSION, listen_only=False)
    warning_lock = Lock()
    bot.media.audio.prepare(WARNING_CLIP)

    @bot.on(sbc.Event.USER_TALKING, priority=100)
    def guard(user: sbc.User) -> None:
        # Do not react to the bot itself, including its prerecorded warning.
        if user.id == bot.session.user_id or user.bot:
            return
        if not warning_lock.acquire(blocking=False):
            return

        try:
            print(f"{user.name} spoke; playing warning.")
            bot.media.audio.play(WARNING_CLIP, loop=False)
            sleep(8)
            bot.users.remove(user.id)
            print(f"Removed {user.name}.")
        finally:
            warning_lock.release()

    print("Voice guard is running. Press Ctrl+C to stop.")
    try:
        bot.run()
    finally:
        bot.close()


if __name__ == "__main__":
    main()
