# SBC API Reference

The release build generates a complete action reference from SBC's embedded BBB
action schema. Build artifacts contain `API.md`; type checkers receive generated
signatures for all embedded action methods through `sbc/operations/__init__.pyi`.

## Main controllers

| Controller | Read API | Mutation API |
| --- | --- | --- |
| `client.polls` | `list()` | `create`, `publish`, `vote`, `answer`, `cancel` |
| `client.breakout_rooms` | `list()` | `create`, `move`, `end_all`, `set_time` |
| `client.cameras` | `list()` | `start`, `stop`, `pin`, `eject` |
| `client.recordings` | `status()` | `start`, `stop` |
| `client.guests` | `list()` | `policy`, `approve`, `deny`, `lobby_message` |
| `client.captions` | `transcript()` | `submit`, `submit_transcript`, `speech_locale` |
| `client.whiteboards` | `current()` | `submit`, `delete`, `clear`, `cursor` |

## Events

```python
@client.on(sbc.Event.USER_JOINED, priority=10, when=lambda user: not user.bot)
def welcome(user: sbc.User) -> None:
    client.chat.send(f"Welcome {user.name}")

@client.once(sbc.Event.USER_TALKING)
def first_speaker(user: sbc.User) -> None:
    print(user.name)
```

Use `client.watch_table(sbc.BBBTable.NOTIFICATION, "messageId notificationType")`
for every source-schema table and handle its `table_notification_changed` event.
