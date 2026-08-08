"""Control BBB external video with public-chat commands.

Commands:
    !startvid https://www.youtube.com/watch?v=...
    !endvid
"""
from pathlib import Path
from urllib.parse import urlparse

import sbc


SESSION = Path(__file__).with_name("test.sbc")
SUCCESS_REACTION = "✅"


def is_http_url(value: str) -> bool:
    """Accept only complete HTTP(S) external-video URLs."""
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def main() -> None:
    bot = sbc.client(SESSION)

    @bot.on("chat_message")
    def external_video_commands(message: sbc.ChatMessage) -> None:
        # Never handle the confirmation message sent by this bot.
        if message.sender_id == bot.session.user_id:
            return

        command, _, argument = message.text.strip().partition(" ")
        command = command.lower()

        if command == "!startvid":
            url = argument.strip()
            if not is_http_url(url):
                bot.chat.reply(message, "Usage: `!startvid https://video-url`")
                return
            bot.external_video.start(url)

        elif command == "!endvid":
            bot.external_video.stop()

        else:
            return

        bot.chat.reply(message, "Done!")
        if message.chat_id:
            bot.chat.react(message.chat_id, message.id, SUCCESS_REACTION)

    print("Video command bot is running. Press Ctrl+C to stop.")
    try:
        bot.run()
    finally:
        bot.close()


if __name__ == "__main__":
    main()
