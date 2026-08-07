Command-line tools
==================

Installing SmallBlueClient also installs the ``sbc`` command.

.. code-block:: bash

   sbc validate teacher.sbc
   sbc inspect teacher.sbc --json
   sbc diagnose teacher.sbc
   sbc endurance teacher.sbc --minutes 10 --audio warning.mp3 --output report.json
   sbc run bots/welcome.py teacher.sbc

``inspect`` never prints session tokens, cookies, or connection headers.
``run`` passes the selected session as the script's first argument and places
its resolved path in ``SBC_SESSION``.
