Media troubleshooting
=====================

BBB WebRTC SFU microphone mode
------------------------------

For a prepared custom-audio source, use full-audio mode rather than a listener
session:

.. code-block:: python

   client = sbc.client("teacher.sbc", listen_only=False)
   client.media.audio.prepare("warning.mp3")
   client.media.audio.play("warning.mp3", loop=False)

SBC clears BBB's persisted listener-input setting before warming the sender and
unmutes only after the file track is attached. SBC does **not** create an idle
microphone automatically: this avoids SFU deployments briefly showing and then
tearing down a silent microphone before there is audio to send. A fresh session exported by the
current extractor also records the deployment's ``fullAudioOffering`` and
``transparentListenOnly`` settings so the Python WebRTC peer uses BBB's exact
SDP offer/answer role. Older sessions use BBB 3.0's stock source defaults
(``transparentListenOnly: false`` and ``fullAudioOffering: true``), with SBC
offering the full-audio SDP. On an ICE/DTLS failure SBC retries through BBB's
TURN relay and advertises relay candidates only, matching the browser client's
retry-through-relay path. Re-export a session after updating the extension to
preserve a deployment override.

Input mode recovery
~~~~~~~~~~~~~~~~~~~

BBB treats the listener/microphone indicator as participant state separate from
the WebRTC peer connection. Whenever SBC creates a replacement SFU connection,
it restores the last selected input mode automatically. A listener reconnect
reapplies the listener state; an active custom-audio reconnect reapplies
microphone state and unmutes the sender. A silent, explicit warm-up remains
muted until audio is actually played.

Legacy SFU fingerprint compatibility
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Some BBB SFUs advertise a ``sha-1`` DTLS fingerprint despite successfully
serving modern browser clients. SBC detects that source-compatible SDP form
and enables aiortc's SHA-1 *fingerprint validation* for the connection. The
peer certificate must still exactly match the fingerprint advertised by the
SFU; SBC does not disable DTLS identity checks. This fixes the otherwise
misleading combination ``ice=completed`` plus ``DTLS handshake failed
(fingerprint mismatch)``.

``client.media.audio.warmup()`` remains available as an explicit low-latency
option when a bot must play a clip immediately after an event. It is optional;
ordinary ``play()`` reliably opens the sender directly.

SBC publishes custom media directly from Python. The extension is used only to
export the session.

Dynamic visual screenshare
--------------------------

SBC can publish a mutable Python visual as a real BBB screen share.  A board
is a general rendering surface, not a special bot type: update it from chat
commands, timers, files, a local API, or any application logic.  Updates are
sent in subsequent video frames without restarting the screenshare.

.. code-block:: python

   board = client.screenshare.textboard(
       "Waiting for the next item…",
       title="Live status",
       width=1280,
       height=720,
   )
   client.screenshare.start(board)

   # Later, from any application handler:
   board.set_text("Round 2 starts now")
   board.append("Please answer the poll.")

   client.screenshare.stop()

Local video files can be published as screenshare media too:

.. code-block:: python

   client.screenshare.play("status-loop.mp4", loop=True, frame_rate=15)
   client.screenshare.stop()

Static images are ordinary surfaces, so they can later be painted or replaced:

.. code-block:: python

   visual = client.screenshare.image("slide.png")
   visual.paint(lambda image, draw: draw.rectangle((20, 20, 400, 120), fill="#0f172a"), clear=False)

For arbitrary visual content, create a surface and paint it with Pillow.  The
same surface stays connected while its pixels change:

.. code-block:: python

   surface = client.screenshare.surface(1280, 720, background="#111827")

   def paint(image, draw):
       draw.rectangle((80, 80, 1200, 640), outline="#38bdf8", width=8)
       draw.text((120, 120), "Custom visual", fill="white")

   surface.paint(paint)
   client.screenshare.start(surface)

Persian, Arabic, and RTL boards
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``TextBoard`` detects Arabic-family and Hebrew scripts automatically, selects a
local script-capable font when available, right-aligns RTL text, and uses
Arabic shaping plus bidirectional ordering on Pillow builds without libraqm.
Set ``direction="rtl"`` to force RTL or provide a known font file with
``font="path/to/font.ttf"``.

.. code-block:: python

   board = client.screenshare.textboard(
       "سلام دنیا",
       title="وضعیت جلسه",
       language="fa",
       direction="auto",
   )
   client.screenshare.start(board)

Long-running bots
-----------------

SBC mirrors the BBB HTML5 client's RTT/liveness workflow while a client is
connected: it calls ``/bigbluebutton/rtt-check`` and submits the returned
request id through ``userSetConnectionAlive`` every ten seconds. This keeps the
BBB participant lease current even when a bot has no chat, event, or UI
traffic. Normal foreground logs include local timestamps and report both media
connection and ICE state when the SFU reconnects.

.. code-block:: python

   sbc.enable_logging("DEBUG", structured=True)
   client.media.audio.prepare("warning.mp3")
   client.media.audio.play("warning.mp3", loop=False)

If publishing fails, first inspect ``client.media.credentials()``. It reports
the active backend. LiveKit requires a token exposed by BBB's current-user data;
BBB WebRTC SFU requires reachable TURN servers. DEBUG JSON logs include backend
selection, TURN server counts, ICE state, and connection state without logging
session tokens. Re-export the session if ``client.session.validate()`` reports
``requires_reexport``.

Confirming file frames are sent
-------------------------------

``connected`` confirms the WebRTC connection. For a direct sender-level check,
inspect the live RTP counters while a looping file is playing:

.. code-block:: python

   client.media.audio.play("warning.mp3", loop=True)
   time.sleep(2)
   print(client.media.status()["audio_stats"])
   # {"packets_sent": 100, "bytes_sent": 12345}

Non-zero ``packets_sent`` and ``bytes_sent`` confirm Python is reading the
audio file and sending encoded audio to BBB. SBC also verifies after ``play``
that BBB reports the saved identity as unmuted and not listener-only.
