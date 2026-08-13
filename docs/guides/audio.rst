Incoming audio and transcription
================================

SBC separates local media publishing from incoming BBB audio.  Use
``client.media.audio`` to play a file through the current user's microphone;
use ``client.audio`` to receive decoded PCM and ``client.transcription`` to
run local speech-to-text.

.. warning::

   Incoming audio capture and local transcription are **experimental**.
   BigBlueButton deployments differ in their SFU, media permissions, codecs,
   and network configuration. A connected listener does not guarantee that a
   recording or transcription will be available.

Capture and record audio
------------------------

``client.audio.record`` writes lossless WAV by default and creates a readable
``manifest.json`` beside the files. Separate tracks are the default API shape:

.. code-block:: python

   with sbc.client("teacher.sbc", listen_only=True) as client:
       recording = client.audio.record("recordings", format="wav")
       # BBB audio is captured while the client remains open.
       input("Press Enter to stop: ")
       paths = recording.stop()

       for track, path in paths.items():
           print(track, path)

``format`` accepts ``wav``, ``mp3``, ``flac``, ``ogg``, and ``opus``. SBC
captures into a safe WAV staging file and encodes non-WAV output with PyAV when
the local FFmpeg build provides the requested codec.

Live PCM frames
---------------

Use a listener for immediate processing, visualization, a custom recognizer,
or a websocket relay. Frames are signed 16-bit interleaved PCM. SBC converts
normalized floating-point frames produced by decoded Opus media into this PCM
representation before forwarding them to recorders and speech engines.

.. code-block:: python

   def receive(frame: sbc.AudioFrame) -> None:
       print(frame.user_name, frame.sample_rate, frame.duration)

   client.audio.add_listener(receive)

The asynchronous form is also available:

.. code-block:: python

   async for frame in bot.audio.frames():
       await send_to_your_service(frame.pcm)

With :func:`sbc.async_client`, ``bot.audio.frames()`` is a native asynchronous
iterator. ``await bot.audio.record(...)`` and ``await bot.transcription.start(...)``
provide awaitable lifecycle operations.

BBB track topology
------------------

BBB's regular ``bbb-webrtc-sfu`` listener is a conference mix supplied by the
AudioBroker. SBC records it as a single ``mixed=True`` track labelled
``Conference mix``. It is not technically possible to reconstruct independent
speaker tracks from that mixed signal without applying a separate source-
separation system.

SBC also captures the inbound conference mix on a full-audio peer when the
deployment negotiates it as a receive-capable track, so a bot can record and
transcribe while publishing custom audio as well.

When a backend provides actual participant-labelled receive tracks, SBC keeps
the source user id and name on every :class:`sbc.AudioFrame` and creates one
recording/transcript track per participant automatically.

Local faster-whisper transcription
----------------------------------

Install the optional transcription runtime:

.. code-block:: bash

   pip install "SmallBlueClient[transcription]"

Then start a bounded-memory local worker. Models are downloaded by
``faster-whisper`` on first use and remain local afterward.

.. code-block:: python

   transcript = client.transcription.start(
       model="base",
       language="en",
       chunk_seconds=5,
   )

   @client.on(sbc.Event.TRANSCRIPT_SEGMENT)
   def on_text(segment: sbc.TranscriptSegment) -> None:
       print(segment.user_name, segment.text)

   # Later:
   transcript.stop()
   transcript.export("recordings/meeting.srt", format="srt")
   transcript.export("recordings/meeting.vtt", format="vtt")
   transcript.export("recordings/meeting.txt", format="txt")
   transcript.export("recordings/meeting.json", format="json")

``TranscriptSegment`` contains the text, absolute start/end times, detected
language, optional model confidence, source user metadata, and whether the
source was a conference mix.

See :file:`examples/live_transcription.py` for a complete recording bot.

Command line capture
--------------------

The same workflow is available without writing a bot:

.. code-block:: bash

   sbc transcribe teacher.sbc --minutes 30 --model base --language fa --output recordings

Use ``--mix`` to force one conference file, ``--format flac`` for lossless
compressed tracks, and ``--transcript-format vtt`` or ``json`` for another
subtitle/export form.
