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
