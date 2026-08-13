# SmallBlueClient

<p align="center">
  <img src="icon700.png" width="144" alt="SmallBlueClient icon">
</p>

<p align="center">
  <strong>Community automation for authenticated BigBlueButton sessions.</strong><br>
  Export a session once. Build powerful bots in Python.
</p>

<p align="center">
  <a href="https://pypi.org/project/smallblueclient/"><img src="https://img.shields.io/pypi/v/smallblueclient.svg?logo=pypi&logoColor=white&label=PyPI&cacheSeconds=300" alt="PyPI version 0.1.0"></a>
  <a href="https://sbc.protobuf.lol"><img src="https://img.shields.io/badge/docs-sbc.protobuf.lol-2563eb?logo=readthedocs&logoColor=white" alt="Documentation"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-16a34a" alt="MIT License"></a>
  <a href="https://github.com/BolinoIR/SmallBlueClient"><img src="https://img.shields.io/github/stars/BolinoIR/SmallBlueClient?style=flat&logo=github" alt="GitHub stars"></a>
</p>

---

## Why SBC?

SmallBlueClient (SBC) is a Python-first toolkit for BigBlueButton. The included
Chrome extension is deliberately tiny: it **only** passively captures the real,
authenticated BBB GraphQL session and exports a portable `.sbc` credential. All
automation, media, events, models, reconnects, and controllers live in Python.

| Build | With SBC |
| --- | --- |
| **Bots** | welcome users, moderate rooms, react to events, manage breakouts |
| **Automation** | chat, polls, captions, presentations, cameras, timers, recordings |
| **Custom media** | publish Python-controlled audio, video, and mutable visual screenshares where supported by BBB |
| **Capture + transcripts** | record incoming conference/per-user tracks and generate local SRT/VTT/TXT/JSON transcripts |
| **Typed code** | controllers, enums, models, generated schema catalog, async API |
| **Multiple meetings** | one independent `sbc.client("meeting.sbc")` per session |

## Install

```bash
pip install SmallBlueClient
```

For local live transcription:

```bash
pip install "SmallBlueClient[transcription]"
```

```python
import sbc

with sbc.client("teacher.sbc") as client:
    print(client.meeting.name)
    client.chat.send("Hello from SBC")
```

Read the complete guides and generated API reference at
**[sbc.protobuf.lol](https://sbc.protobuf.lol)**.

### Incoming audio and transcripts

```python
with sbc.client("teacher.sbc", listen_only=True) as client:
    recording = client.audio.record("recordings")
    transcript = client.transcription.start(model=sbc.TranscriptionModel.BASE)

    @client.on(sbc.Event.TRANSCRIPT_SEGMENT)
    def on_text(segment: sbc.TranscriptSegment) -> None:
        print(segment.user_name, segment.text)

    input("Press Enter to finish: ")
    transcript.stop()
    transcript.export("recordings/meeting.srt")
    recording.stop()
```

The BBB WebRTC SFU listener provides a conference mix. LiveKit participant
tracks are recorded separately when the BBB deployment exposes them.

See the full migration history in [CHANGELOG.md](CHANGELOG.md).

### Development and community

```bash
python -m pip install -e ".[docs,dev]"
python -m unittest discover -s tests -q
ruff check sbc tests
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contributor workflow,
[SECURITY.md](SECURITY.md) for credential-safe reporting, and the hosted
[community guide](https://sbc.protobuf.lol/guides/community.html) for sharing
BBB compatibility results.

## Quick start

1. Load the [`extension/`](extension/README.md) directory through
   `chrome://extensions` → **Load unpacked**.
2. Join your BBB meeting normally and open **SBC Session Extractor**.
3. Export the detected `.sbc` file and keep it private.
4. Start writing Python:

```python
import sbc

bot = sbc.client("classroom.sbc")

@bot.on(sbc.Event.USER_JOINED)
def welcome(user: sbc.User) -> None:
    bot.chat.send(f"Welcome, {user.name}!")

bot.run()
```

> [!IMPORTANT]
> An `.sbc` export is an authenticated browser credential. Do not commit it,
> send it to someone else, or publish it in bug reports.

## Session extractor

The included extension is a **passive session extractor, not the SBC program**.
It observes the BBB page's actual GraphQL WebSocket, captures the observed
connection payload, adds browser cookies through Chrome's extension API, and
downloads an integrity-checked `.sbc` file. It does **not** send GraphQL
mutations, automate BBB, alter media, spoof devices, or run a localhost bridge.

The retired v5 automation extension is stored locally in
`archive/chrome-extension-v5.0.1/` and is not part of this repository.

## Escape hatch: source-derived actions

High-level controllers cover normal tasks. For experiments, all 109 BBB
mutations are embedded in the installed Python package and are validated before
they are sent. Both BBB camelCase and clean Python snake_case are supported:

```python
client.actions.userSetMuted(userId="user-id", muted=True)
client.actions.user_set_muted(userId="user-id", muted=True)
client.mutation("meetingEnd")
```

## Bots and events

```python
bot = sbc.client("classroom.sbc")

@bot.on("user_joined")
def welcome(user):
    bot.chat.send(f"Welcome {user.name}")

bot.run()
```

Events: `user_joined`, `user_left`, `chat_message`, `hand_raised`,
`voice_joined`, `user_talking`, `presentation_changed`, and `meeting_ended`.

### Complete event surface

SBC also exposes source-backed user transitions: `hand_lowered`, `voice_left`,
`user_stopped_talking`, `user_muted`, `user_unmuted`,
`user_became_presenter`, `user_stopped_presenting`,
`user_became_moderator`, `user_stopped_moderating`, `user_away`, `user_back`,
`user_disconnected`, `user_reconnected`, `camera_started`, and `camera_stopped`.

Meeting streams provide `meeting_updated`, `screenshare_started`,
`screenshare_stopped`, `external_video_started`, `external_video_stopped`,
`poll_updated`, `poll_published`, `poll_ended`, `poll_results_changed`,
`timer_updated`, `timer_started`, `timer_stopped`, `timer_elapsed`, and
current-user events including `current_user_joined` and `current_user_ejected`.

Every field in the BBB 3.0.32 user, current-user, and meeting subscriptions also
gets a generated change handler. For example:

```python
@bot.on("user_role_changed")
def role_changed(user, old_role, new_role):
    print(user.name, old_role, "->", new_role)

@bot.on("meeting_lock_settings_changed")
def locks_changed(meeting, old, new):
    print(new)
```

Handlers can be removed, filtered, prioritized, run once, or be `async`:

```python
@bot.once("user_joined", priority=10, when=lambda user: user.guest)
async def greet_guest(user):
    bot.chat.send(f"Welcome {user.name}")

bot.off("user_joined", greet_guest)
```

For any BBB 3.0.32 schema table, use the embedded source-backed catalog before
`run`:

```python
bot.watch_table(
    sbc.BBBTable.NOTIFICATION,
    "messageId notificationType messageDescription",
)

@bot.on("table_notification_changed")
def notification(rows):
    print(rows)
```

`sbc.BBBTable` contains every table listed in the bundled BBB 3.0.32 GraphQL
schema. `sbc.schema.subscription(...)` is also available when a raw operation
string is useful.

All 109 built-in BBB action methods also have lifecycle handlers. For example,
`userSetMuted` emits `action_started`, `action_completed`, and `action_failed`,
plus `action_user_set_muted_started`, `action_user_set_muted_completed`, and
`action_user_set_muted_failed`. The same pattern is generated for every action
in `client.actions.names`.

Live event subscriptions reconnect automatically with exponential backoff. SBC
multiplexes all selected built-in and table subscriptions through one event
socket, so a bot no longer creates one authenticated BBB WebSocket per handler.
A new `X-ClientSessionUUID` is generated for each SBC socket so reconnects do
not replace the browser's active BBB GraphQL connection.

Only streams required by registered handlers are opened. A bot using only
`user_talking` opens one BBB GraphQL subscription instead of every optional
meeting stream. Streams may also be selected explicitly:

```python
bot.enable_events("user_talking", "chat_message")
```

Pass `listen_only=False` to join a muted full-audio microphone session instead
of a listener session. It keeps the SFU connection ready so a later
`client.media.audio.play(...)` begins without a new WebRTC negotiation:

```python
bot = sbc.client("classroom.sbc", listen_only=False)
```

## Typed controllers and enums

High-level controllers cover polls, breakouts, captions, shared notes,
recording, cameras, whiteboards, guests, timers, external video, plugins, media
groups, meeting settings, and locks. BBB values are named enums rather than
unexplained strings:

```python
import sbc

bot = sbc.client("teacher.sbc")

poll_id = bot.polls.create(
    "Ready?", ["Yes", "No"], poll_type=sbc.PollType.YES_NO,
)
bot.polls.publish(poll_id)

bot.guests.policy(sbc.GuestPolicy.ASK_MODERATOR)
bot.settings.role("student-id", sbc.Role.VIEWER)
bot.locks.set(sbc.LockSettings(disable_microphone=True, lock_on_join=True))

room = sbc.BreakoutRoom("Group A", sequence=1, users=("student-id",))
bot.breakouts.create([room], duration_minutes=15)
```

Typed data objects are available directly from `sbc`: `User`, `Meeting`,
`Chat`, `ChatMessage`, `Presentation`, `Poll`, `Timer`, `Caption`,
`BreakoutRoom`, `Camera`, `LockSettings`, `Screenshare`, `ExternalVideo`,
`SharedNotesSession`, `Recording`, `WhiteboardAnnotation`, `Notification`, and
plugin/media-group models.

## Read controllers

Controllers provide typed reads as well as actions:

```python
with sbc.client("teacher.sbc") as client:
    polls = client.polls.list()
    rooms = client.breakout_rooms.list()
    cameras = client.cameras.list()
    recording = client.recordings.status()
    guests = client.guests.list()
    annotations = client.whiteboards.current()
    transcript = client.captions.transcript()
```

## Native asyncio

The async facade uses the same session format, models, enums, controllers, and
automatic reconnect behavior:

```python
import sbc

async with sbc.async_client("test.sbc") as bot:
    await bot.chat.send("Hello")

    async for user in bot.events.user_joined():
        await bot.chat.send(f"Welcome {user.name}")
```

Use `python examples/async_bot.py` for a complete runnable version.

## Generated source catalog and API

Release builds read BBB's `bbb-graphql-schema.md` directly and package a frozen
catalog containing table enums, scalar fields, generated table events, and
`TypedDict` row models. Public Python-only source checkouts intentionally omit
the large BBB source tree and use SBC's compact compatibility catalog until a
schema is supplied for a release build.

To release for BBB 2.7 or a newer checkout, provide its source explicitly:

```powershell
$env:SBC_BBB_SCHEMA = "C:\bbb-2.7\bbb-graphql-server\bbb-graphql-schema.md"
$env:SBC_BBB_VERSION = "2.7"
python -m build --wheel
```

At runtime, load a second source version without replacing the default catalog:

```python
bbb27 = sbc.catalogs.load("C:/bbb-2.7/bbb-graphql-schema.md", version="2.7")
print(bbb27.fields("notification"))
```

Every wheel includes `py.typed`, generated action signatures, and an `API.md`
reference generated from SBC's embedded action definitions. See `docs/API.md`
for the controller/event cheat sheet. Enable JSON logs and non-secret media
diagnostics with:

```python
sbc.enable_logging("DEBUG", structured=True)
```

## Examples

All examples use the local `examples/test.sbc` session and can be launched from
the repository root:

- `python examples/hello.py` — minimal meeting, user, and chat workflow.
- `python examples/automation.py` — one-shot moderation controllers.
- `python examples/moderation_bot.py` — enum-backed event bot.
- `python examples/voice_guard_bot.py` — warmed custom-audio voice guard.
- `python examples/custom_media.py` — loop `examples/test.mp3` as the Python microphone.
- `python examples/event_patterns.py` — async, priority, filters, once, and off.
- `python examples/schema_events.py` — source-schema table events.
- `python examples/typed_controls.py --apply` — typed controllers and enums.
- `python examples/async_bot.py` — async context manager and event iterator.

## Custom microphone and camera

The extension only exports the `.sbc` session. Python connects directly to BBB's
LiveKit room and publishes the selected media:

```python
client.media.audio.play("music.mp3", loop=True)
client.media.audio.mute()
client.media.audio.unmute()

client.media.camera.play("loop.mp4", loop=True)
client.media.camera.mute()
```

Runnable audio example: `python examples/custom_media.py`; it uses the bundled
`examples/test.mp3`.

SBC follows BBB 3.0.32's LiveKit implementation: it reads the authenticated
`user_current.livekit.livekitToken`, connects to `wss://<bbb-host>/livekit`, and
publishes Python-decoded audio/video frames as microphone/camera tracks. No
extension media hook or local bridge is used. Run `python examples/custom_media.py`
to publish `examples/test.mp3`.

The extension also observes BBB's `user_current` GraphQL response and writes the
LiveKit `token` and `url` into `snapshot.livekit` in exported `.sbc` files. For an
older session such as `examples/test.sbc`, `client.media.credentials()` fetches
that same source-backed field once and keeps it in the running client. SBC never
rewrites the loaded `.sbc` file automatically; call `client.save_session()` only
when you explicitly want to persist updated session data.

## Current BBB mapping

The operation mapping was taken from the bundled BBB 3.0.32 source:
`bbb-graphql-actions` and `bigbluebutton-html5`. It uses `chatSendMessage`,
`userSetMuted`, `meetingSetMuted`, `userEjectFromMeeting`, `presentationSetPage`,
and `meetingEnd` rather than guessed mutation names.

## Documentation site

The complete documentation is authored as a Sphinx site with the Furo theme in
`docs/`. It includes the generated Python API/action reference, controller and
event guides, the BBB version compatibility table, session-health guidance, and
media troubleshooting. The published documentation is available at
[sbc.protobuf.lol](https://sbc.protobuf.lol).

```powershell
pip install -e ".[docs]"
sphinx-build -W -b html docs docs/_build/html
```

Open `docs/_build/html/index.html` after a successful build.

## Session health

```python
with sbc.client("teacher.sbc") as client:
    health = client.session.validate()
    print(health.to_dict())
    print(client.session.expires_at)

    if health.requires_reexport:
        print("Export a new .sbc session from the extension.")
```

SBC marks a running session for re-export when BBB reports expired or rejected
credentials. It never mutates the original `.sbc` file unless you explicitly
call `client.save_session()`.
