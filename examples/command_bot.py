"""A modern command bot with cooldowns, moderator permissions, and state."""
from pathlib import Path
import sys

import sbc


SESSION = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("test.sbc")

client = sbc.client(SESSION)
bot = sbc.Bot(client, prefix="!", state_path="command-bot-state.json")


@bot.command(cooldown=3)
def ping(ctx: sbc.CommandContext) -> None:
    ctx.reply("pong")


@bot.command(permission=sbc.Bot.moderator)
def attendance(ctx: sbc.CommandContext) -> None:
    ctx.reply(f"{len(client.users.list())} participant(s) are currently in the meeting.")


@bot.task(interval=60)
def persist_uptime() -> None:
    bot.state.set("minutes_alive", bot.state.get("minutes_alive", 0) + 1)


if __name__ == "__main__":
    bot.start()
    print("Command bot online: !ping for everyone; !attendance for moderators.")
    try:
        client.run()
    except KeyboardInterrupt:
        pass
    finally:
        bot.close()
        client.close()
