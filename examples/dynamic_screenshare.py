"""Publish a mutable text board as a BBB screen share.

This is deliberately just an application of SBC's visual framework.  It has
no bot/command policy: replace ``input()`` with chat commands, a web API,
timers, or any other source of updates.
"""
from pathlib import Path

import sbc


SESSION = Path(__file__).with_name("5.sbc")


def main() -> None:
    with sbc.client(SESSION) as client:
        board = client.screenshare.textboard(
            "منتظر به‌روزرسانی…",
            title="تابلوی زنده",
            width=1280,
            height=720,
            language="fa",  # Persian/Arabic/Hebrew text selects RTL automatically.
        )
        client.screenshare.start(board)
        print("Visual screenshare started. Type text to update it; press Enter on an empty line to stop.")
        try:
            while text := input("> "):
                board.set_text(text)
        finally:
            client.screenshare.stop()


if __name__ == "__main__":
    main()
