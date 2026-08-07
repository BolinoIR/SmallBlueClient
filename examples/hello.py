"""The smallest complete SBC script."""
from pathlib import Path

import sbc


SESSION = Path(__file__).with_name("test.sbc")


def main() -> None:
    client = sbc.client(SESSION)
    try:
        meeting = client.meeting()
        print(f"Connected to: {meeting.name}")
        print("Participants:", ", ".join(user.name for user in meeting.users()))
        client.chat.send("Hello from SBC")
    finally:
        client.close()


if __name__ == "__main__":
    main()
