# Changelog — Legacy Extension → SmallBlueClient 0.1.0

## 0.2.3 — Long-running meeting liveness

- Added HTML5-source-compatible ``/bigbluebutton/rtt-check`` and
  ``userSetConnectionAlive`` heartbeats so long-running SBC clients retain a
  live BBB participant lease while publishing media.
- Added timestamps to normal and structured SBC logs.
- Added media connection-state, ICE-state, and SFU signalling-close details
  to make a later disconnect immediately diagnosable.

## 0.2.2 — BBB SFU fingerprint compatibility

- Fixed ``bbb-webrtc-sfu`` full-audio connections on BBB deployments that
  advertise only a legacy ``sha-1`` DTLS fingerprint. SBC now validates that
  fingerprint against the peer certificate instead of rejecting an otherwise
  completed ICE connection.
- Fixed full-audio answerer negotiation to attach the source track after the
  SFU offer, matching BBB's ``AudioBroker`` ordering.
- Added source-compatible TURN relay retry support and relay-only SDP
  candidates after a normal ICE attempt fails.
- Corrected legacy BBB 3.0 SDP-role defaults: full audio offers by default
  unless an exported deployment setting enables transparent listen-only.

## 0.2.1 — Native async transport and community tooling

- Added ``AsyncGraphQLTransport`` and ``AsyncGraphQLClient`` for raw native
  asyncio GraphQL queries, mutations, and reconnecting subscriptions.
- Added async client ``query()`` and ``subscribe()`` convenience APIs while
  preserving the established high-level async controller surface.
- Added Ruff, mypy, pre-commit, CI linting, contributor instructions, issue
  forms, credential-safe security policy, and community conduct rules.
- Added community compatibility-report guidance and async transport docs.

## 0.2.0 — Reliability, CLI, media controls, and bot framework

- Added the ``sbc validate``, ``sbc inspect``, ``sbc diagnose``,
  ``sbc endurance``, and ``sbc run`` command-line tools.
- Added credential-safe long-running ``EnduranceMonitor`` reports with session,
  media, outbound-RTP, and recovery observations.
- Added audio RTP health checks, stale looping-source recovery, gain in dB,
  fade-in, serial queued clips, and delayed scheduled playback.
- Added structured exceptions with stable error codes/context and
  ``MediaStalledError`` for actionable recovery handling.
- Added the optional ``sbc.Bot`` framework with commands, cooldowns,
  moderator permission helper, periodic tasks, self-message filtering, and
  JSON-backed state.
- Added reliability, CLI, and bot framework documentation plus GitHub release,
  dependency-audit, static-scan, and Dependabot configuration.

## 0.1.5 — Reliable BBB microphone publishing and full contract tests

- Fixed full-audio joins explicitly clearing BBB's retained listener-input
  preference before opening or warming the microphone. A bot can no longer
  have a connected SFU sender that BBB still treats as listen-only.
- Added BBB source-backed full-audio SDP mode handling. Fresh extractor
  sessions capture ``fullAudioOffering`` and ``transparentListenOnly`` so the
  Python publisher follows the deployment's offer/answer role.
- Added a paced source swap before unmuting prepared audio, preventing BBB
  from dropping the first non-silent custom-media frames.
- Fixed the warm-up source and decoded file source using incompatible audio
  layouts. Both now use BBB/aiortc's 48 kHz ``s16`` stereo format, preventing
  aiortc's RTP task from terminating with ``Frame does not match
  AudioResampler setup`` after an MP3 is attached.
- Added sender-level ``client.media.status()["audio_stats"]`` RTP counters so
  a script can verify that packets and bytes are actually leaving Python.
- Made idle microphone warm-up opt-in. ``listen_only=False`` now selects BBB
  full-audio input state without opening a temporary silent sender; explicit
  ``client.media.audio.warmup()`` remains available for latency-sensitive bots.
- Added the BBB source-defined JSON audio heartbeat (``{"id": "ping"}``) to
  keep long-running SFU publisher sessions alive instead of relying only on
  WebSocket control pings.
- Added a GitHub Actions matrix for Python 3.10–3.12, unit tests, wheel/sdist
  validation, clean wheel installation, and warning-free Sphinx builds.
- Expanded local coverage to include all 109 raw actions, all high-level
  controller write mappings, diagnostics/action-plan commands, media
  negotiation modes, and every typed BBB model.

## 0.1.4 — Full capability diagnostics and BBB chat read state

- Corrected the embedded ``chatSetLastSeen`` definition to the BBB HTML5 and
  GraphQL Actions runtime contract: ``chatId`` and ``lastSeenAt`` are both
  required.
- Added ``client.chat.mark_read()`` for public and private chat read cursors.
- Rebuilt ``examples/library_diagnostic.py`` into a detailed, safe capability
  test suite: all read controllers, event streams, mutation compilation,
  reversible self-write probes, JSON-safe full reports, and action inventory.
- Added a review-first all-action test-plan workflow. It creates disabled
  entries for all 109 mutations and executes only explicitly enabled actions;
  ``meetingEnd`` is permanently excluded.
- Added ``--list-actions``, ``--generate-action-plan``, ``--action-plan``,
  ``--execute-plan``, ``--full-details``, and ``--no-auto-join`` diagnostic
  commands.
- Directly running the diagnostic from ``examples/`` now always tests the
  checkout rather than accidentally importing an older globally-installed SBC.

## 0.1.3 — Live chat events and threaded replies

- The initial public-chat history result is now a baseline, so
  ``chat_message`` is emitted only for messages received after watching starts.
- Added ``client.chat.reply(message, text)`` and async equivalent for BBB
  threaded replies.
- Added ``bot.session`` to the async client for session metadata and clean
  self-filtering in async bots.

## 0.1.2 — Correct live user-join events

- The first BBB ``user`` subscription result is now stored as the existing
  participant baseline rather than being emitted as a sequence of joins.
- ``user_joined`` and ``bot.events.user_joined()`` now fire only when a new
  user appears after SBC has started watching the meeting.

## 0.1.1 — Public-chat delivery fix

- Fixed ``client.chat.send()`` and ``await client.chat.send()`` using a BBB
  meeting ID as the public ``chatId``.
- SBC now uses BBB HTML5's source-defined public chat group:
  ``MAIN-PUBLIC-GROUP-CHAT``.
- Added a regression test for synchronous and asynchronous public-chat
  delivery.

## Breaking change: Python-first SBC

SBC changed from a Chrome-extension automation UI into a Python automation
library.

- **Before:** the extension intercepted BBB traffic and exposed action UI,
  scripts, spoofing, and browser-side automation controls.
- **Now:** `import sbc` is the product.
- Chrome is now only a **passive `.sbc` session extractor**.
- Legacy v5 automation is archived locally as `chrome-extension-v5.0.1`.
- The active extension has no action runner, GraphQL mutation sender, spoofing,
  bridge, media hook, script runner, or BBB automation UI.

---

## Added — Python package and distribution

```bash
pip install SmallBlueClient
```

```python
import sbc

client = sbc.client("teacher.sbc")
```

Added:

- `pyproject.toml`, `MANIFEST.in`, `py.typed`, MIT licensing, wheels, and sdists.
- PyPI publishing: <https://pypi.org/project/SmallBlueClient/0.1.0/>.
- Embedded action registry, generated action stubs, and package data.
- A maintainable package structure:

```text
sbc/
├── asyncio/      ├── bots/         ├── bridge/
├── controllers/  ├── core/         ├── media/
├── models/       ├── operations/   ├── schema/
└── types/
```

---

## Added — Portable `.sbc` sessions

`.sbc` files now represent portable authenticated BBB connections. They contain
the BBB server, GraphQL WebSocket URL, observed connection payload, cookies,
meeting/user/role metadata, current-user state, optional LiveKit data, capture
metadata, and a SHA-256 integrity checksum.

Supported formats:

- JSON `.sbc` envelopes exported by Chrome.
- ZIP `.sbc` packages written by Python.

```python
client.session.validate()
client.session.expires_at
client.session.requires_reexport

health = client.session.validate()
print(health.to_dict())
```

Added corruption detection, integrity validation, Unicode-safe browser export
checksums, legacy camelCase upgrades, session-path discovery, expiration checks,
GraphQL authorization failure detection, automatic re-export marking, and no
silent rewrites of loaded session files.

---

## Added — Passive Chrome session extractor

```text
extension/
├── manifest.json
├── background.js
├── content.js
├── page-capture.js
├── popup.html
├── popup.js
└── README.md
```

The extractor:

- observes BBB's real GraphQL WebSocket;
- detects BBB from observed GraphQL operations;
- captures real `connection_init` payloads, user/meeting data, and exposed
  LiveKit credentials;
- attaches browser cookies through Chrome APIs;
- exports integrity-checked `.sbc` files;
- stays idle on non-BBB pages;
- provides a focused one-button export popup.

Removed from the active extension: action execution, automation scripts,
`actions.json` dependency, request interception/replay, custom mutation UI,
spoofing, media injection, and localhost bridge controls.

---

## Added — GraphQL, WebSocket, joining, and reliability

Added an internal authenticated GraphQL client for queries, mutations, and
subscriptions. Normal users use controllers instead of GraphQL strings:

```python
client.chat.send("Hello")
client.users.mute_all()
client.meeting.end()
```

Added:

- GraphQL transport handling and error parsing.
- Heartbeats, operation retries, subscription recovery, and exponential reconnects.
- Subscription multiplexing and selective event-stream startup.
- Independent clients for multiple BBB meetings.
- Current-user meeting-state checks and automatic `userJoinMeeting` recovery.
- Listener-first joining and optional muted full-audio joining:

```python
sbc.client("teacher.sbc", auto_join=True, listen_only=True)
sbc.client("teacher.sbc", listen_only=False)
```

---

## Added — High-level controllers

Core controllers:

```python
client.chat
client.users
client.meeting
client.presentation
client.presentations
client.media
```

Advanced controllers:

```python
client.polls              # polls and answers
client.breakout_rooms     # breakout lifecycle and moderation
client.captions           # locales and transcripts
client.shared_notes       # shared-note sessions
client.recordings         # recording state
client.cameras            # camera state/content controls
client.whiteboards        # annotations and cursors
client.guests             # lobby and approval
client.timers             # timers and modes
client.external_videos    # external media
client.plugins            # plugin data channels
client.media_groups       # media groups
client.settings           # meeting/user settings
client.locks              # meeting locks
client.screenshare        # screenshare content state
client.reactions          # reactions, hands, and status
```

### Chat and moderation

```python
client.chat.send("Hello")
client.chat.public_history()
client.chat.private_history()
client.chat.create_private(user_id)
client.chat.edit(chat_id, message_id, "Edited")
client.chat.delete(chat_id, message_id)
client.chat.react(chat_id, message_id, "👍")
client.chat.remove_reaction(chat_id, message_id, "👍")
client.chat.clear_public_history()
client.chat.set_typing(chat_id, True)

client.users.list()
client.users.mute(user_id)
client.users.unmute(user_id)
client.users.mute_all()
client.users.remove(user_id)
```

### Presentations, screenshare, and reactions

```python
client.presentations.list()
client.presentation.next_page()
client.presentation.set_page(presentation_id, page_id)
client.presentations.set_current(presentation_id)
client.presentations.remove(presentation_id)
client.presentations.export(presentation_id)
client.presentations.set_downloadable(presentation_id)
client.presentations.request_upload_token("slides.pdf")
client.presentations.upload("slides.pdf", endpoint="...")
client.presentations.download(presentation, "downloads/")

client.screenshare.current()
client.screenshare.set_as_content(True)
client.reactions.set(sbc.Reaction.THUMBS_UP)
client.reactions.raise_hand()
client.reactions.lower_hand()
client.reactions.set_away()
client.reactions.clear_all()
```

Added typed `PresentationDocument` support plus typed reads for polls,
breakouts, captions, guests, cameras, recordings, whiteboards, and timers.

---

## Added — Enums, models, and action registry

Added clean enums:

```python
sbc.Reaction
sbc.PresentationFileState
sbc.BreakoutLifecycle
sbc.Event
sbc.Role
sbc.PollType
sbc.GuestPolicy
sbc.GuestApproval
sbc.Layout
sbc.CaptionProvider
```

Added typed models for users, meetings, chats, chat messages, presentations,
polls, timers, captions, breakout rooms, cameras, locks, screenshares, external
video, guests, notes, recordings, whiteboard data, notifications, plugins,
media groups, and layouts.

The external `actions.json` runtime dependency was removed from the Python
library. All 109 BBB mutations are embedded and validated, with both BBB
camelCase and Python snake_case APIs:

```python
client.actions.userSetMuted(userId="user-id", muted=True)
client.actions.user_set_muted(userId="user-id", muted=True)
client.mutation("meetingEnd")
```

Action lifecycle events and generated action type signatures were added.

---

## Added — Event system and schema catalog

Basic usage:

```python
@client.on(sbc.Event.USER_JOINED)
def joined(user):
    print(user.name)
```

Added built-in handling for user joining/leaving/talking/muting, voice state,
hands, chat, presentations, cameras, screenshares, meeting end, polls, timers,
current-user state, breakouts, plugin data, actions, and generated table events.

Event-system improvements:

```python
client.on(...)
client.once(...)
client.off(...)
client.enable_events(...)
client.watch(...)
client.watch_table(...)
```

- priorities and `when=` filters;
- async handlers;
- isolated handler-error reporting;
- dynamic source-schema table subscriptions;
- action-started/action-completed/action-failed events.

Added source-schema catalog tooling:

```python
sbc.BBBTable
sbc.schema
sbc.catalogs
sbc.SchemaCatalog
```

It supports source-derived table catalogs, field discovery, generated table
events/models, versioned BBB catalogs, and `SBC_BBB_SCHEMA` release builds for
BBB 2.7, 3.0, and future versions.

---

## Added — Async API and Python media

```python
async with sbc.async_client("teacher.sbc") as bot:
    await bot.chat.send("Connected")
    async for user in bot.events.user_joined():
        print(user.name)
```

Added async controller proxies, async context-manager support, and async event
iterators.

Added direct Python-side media controls:

```python
client.media.audio.prepare("warning.mp3")
client.media.audio.play("warning.mp3", loop=False)
client.media.audio.mute()
client.media.audio.unmute()

client.media.camera.play("camera.mp4", loop=True)
client.media.camera.mute()
client.media.camera.unmute()
```

Media additions include source playback, loop support, prewarming, credential
inspection, LiveKit and BBB WebRTC SFU support, backend detection, ICE/TURN
logging, retries, and no extension media hook requirement.

---

## Added — Logging, examples, tests, and docs

```python
sbc.enable_logging("DEBUG", structured=True)
```

Logging covers sessions, GraphQL, joining, listener/full-audio state, event
streams, reconnects, media backend selection, WebRTC/ICE state, handler errors,
and re-export requirements.

Added/modernized examples:

```text
examples/
├── hello.py              ├── automation.py
├── moderation_bot.py     ├── voice_guard_bot.py
├── custom_media.py       ├── async_bot.py
├── event_patterns.py     ├── schema_events.py
└── typed_controls.py
```

Added tests for sessions, browser-export integrity, paths, health, clients,
async support, events, controllers, schema handling, WebSockets, extension
format, and bridge compatibility.

Added a complete Sphinx + Furo documentation site with MyST Markdown support,
generated API/action/schema references, compatibility documentation, media
troubleshooting, project branding, and hosted documentation:

<https://sbc.protobuf.lol>

---

## Added — Release infrastructure

- Reinitialized and cleaned the GitHub project.
- Removed legacy GitHub release assets.
- Added project metadata, icon, README redesign, license, and packaging files.
- Added `.gitignore` protection for sessions, builds, archives, and BBB source.
- Published and verified `SmallBlueClient 0.1.0` on PyPI.
- Built and checked wheels and source distributions with `twine check`.
- Verified direct installation from PyPI.

## Current project identity

```text
SmallBlueClient 0.1.0
```

- Python-first BBB automation toolkit.
- Passive Chrome `.sbc` session extractor.
- Source-derived GraphQL/mutation support.
- Synchronous and asynchronous APIs.
- Typed controllers, models, enums, events, media, documentation, tests, PyPI
  distribution, GitHub repository, and hosted docs.
