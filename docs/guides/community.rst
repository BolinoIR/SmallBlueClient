Community and compatibility
===========================

SmallBlueClient is community software. BBB installations differ by release,
role policy, GraphQL schema extensions, proxies, and media backend. A feature
is most useful when its compatibility evidence is recorded.

Share a compatibility result
----------------------------

Run the built-in checks against a meeting you are authorized to test:

.. code-block:: bash

   sbc validate meeting.sbc --json
   sbc diagnose meeting.sbc --json
   sbc endurance meeting.sbc --minutes 5 --output report.json

Before sharing output, remove browser cookies, tokens, user names, meeting IDs,
recording URLs, and chat contents. Include SBC version, BBB version, role,
media backend, and which controller/event was tested.

Community bot recipes
---------------------

The ``examples/`` directory includes a command bot and an endurance checker.
Good reusable bot submissions are small, use typed controllers/enums, filter
the bot's own identity, and have an explicit configuration section.

Contributing
------------

See `CONTRIBUTING.md <https://github.com/BolinoIR/SmallBlueClient/blob/main/CONTRIBUTING.md>`_
for local validation, release requirements, and credential-safe reports.
