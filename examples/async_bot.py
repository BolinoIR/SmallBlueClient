"""Native asyncio SBC bot with awaitable controllers and event iterators."""
import asyncio
from pathlib import Path

import sbc


SESSION = Path(__file__).with_name("test.sbc")


async def main() -> None:
    async with sbc.async_client(SESSION) as bot:
        await bot.chat.send("Async SBC is online.")
        print("Waiting for users. Press Ctrl+C to stop.")

        async for user in bot.events.user_joined():
            print(f"{user.name} joined")
            await bot.chat.send(f"Welcome {user.name}!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
