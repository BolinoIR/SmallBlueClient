Reliability and endurance testing
=================================

SBC can produce credential-safe evidence about a long-running BBB automation
session. The monitor samples session health, media state, RTP counters, and
the current user state without writing browser tokens or cookies into reports.

.. code-block:: python

   import sbc
   with sbc.client("teacher.sbc", listen_only=False) as client:
       client.media.audio.play("hold-music.mp3", loop=True, gain_db=-8)
       report = sbc.EnduranceMonitor(client, interval=30).run(duration=600)
       report.save("sbc-endurance-report.json")

``client.media.audio.health()`` compares real outbound RTP counters. If a
looping source stays connected but counters stop advancing, it can create a
fresh BBB SFU sender automatically.

.. code-block:: python

   client.media.audio.enqueue("intro.mp3", gain_db=-3)
   client.media.audio.enqueue("warning.mp3", fade_in=0.25)
   client.media.audio.schedule("bell.mp3", delay=60)

``enqueue`` is serial playback for short clips. Use ``duration=`` when a
media container does not report its duration.
