# Python quick reference

SmallBlueClient keeps normal automation readable: use controllers, enums, and
typed models rather than embedding GraphQL in every script.

```python
import sbc

with sbc.client("teacher.sbc") as client:
    client.chat.send("The lesson is starting.")
    client.reactions.set(sbc.Reaction.THUMBS_UP)

    for user in client.users.list():
        print(user.name, user.role)
```

## Controller map

| Need | Controller | Typical calls |
| --- | --- | --- |
| Meeting members and moderation | `client.users` | `list()`, `mute()`, `mute_all()`, `remove()` |
| Public/private chat | `client.chat` | `send()`, `public_history()`, `private_history()`, `delete()` |
| Slides and files | `client.presentations` | `list()`, `upload()`, `export()`, `download()` |
| Polls | `client.polls` | `list()`, `create()`, `publish()`, `vote()` |
| Breakouts | `client.breakout_rooms` | `list()`, `create()`, `move()`, `end_all()` |
| Captions | `client.captions` | `transcript()`, `submit()`, `submit_transcript()` |
| Cameras and screen share | `client.cameras`, `client.screenshare` | `list()`, `start()`, `stop()`, `current()` |
| Reactions and status | `client.reactions` | `set()`, `raise_hand()`, `set_away()` |
| Plugin channels | `client.plugins` | `listen()` |

## Event-driven bot

```python
@client.on(sbc.Event.BREAKOUT_STARTED)
def announce_breakout(room: dict) -> None:
    client.chat.send(f"Breakout room started: {room['name']}")

@client.on(sbc.Event.USER_JOINED, when=lambda user: user.id != client.session.user_id)
def welcome(user: sbc.User) -> None:
    client.chat.send(f"Welcome, {user.name}!")

client.run()
```

Use `client.once(...)`, `client.off(...)`, priorities, and filters to keep bots
small and deterministic. For every generated event and method, see the
{doc}`../reference/api` and {doc}`../reference/actions` pages.

## Async

```python
async with sbc.async_client("teacher.sbc") as bot:
    await bot.chat.send("Connected")
    async for user in bot.events.user_joined():
        print(user.name)
```
