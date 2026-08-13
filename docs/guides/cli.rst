Command-line tools
==================

Installing SmallBlueClient also installs the ``sbc`` command.

.. code-block:: bash

   sbc validate teacher.sbc
   sbc inspect teacher.sbc --json
   sbc diagnose teacher.sbc
   sbc endurance teacher.sbc --minutes 10 --audio warning.mp3 --output report.json
   sbc transcribe teacher.sbc --minutes 30 --model base --language fa --output recordings
   sbc run bots/welcome.py teacher.sbc

``inspect`` never prints session tokens, cookies, or connection headers.
``run`` passes the selected session as the script's first argument and places
its resolved path in ``SBC_SESSION``.

``transcribe`` records incoming BBB audio and produces a local subtitle file.
Install ``SmallBlueClient[transcription]`` first; use ``--mix`` for a single
conference file, and choose ``--transcript-format`` from ``srt``, ``vtt``,
``txt``, or ``json``.
