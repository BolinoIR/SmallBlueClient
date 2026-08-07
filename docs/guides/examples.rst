Examples
========

SmallBlueClient has a compact API for simple scripts and a complete event system
for long-running bots. Every example starts from a session exported by the SBC
Session Extractor.

Basic chat
----------

.. code-block:: python

   import sbc

   with sbc.client("teacher.sbc") as client:
       client.chat.send("Hello from SBC")

Welcome bot
-----------

.. code-block:: python

   import sbc

   bot = sbc.client("classroom.sbc")

   @bot.on(sbc.Event.USER_JOINED)
   def welcome(user):
       bot.chat.send(f"Welcome {user.name}")

   bot.run()

Moderation handler
------------------

.. code-block:: python

   import sbc

   bot = sbc.client("moderator.sbc", auto_join=True, listen_only=True)

   @bot.on(sbc.Event.HAND_RAISED, when=lambda user: not user.is_moderator)
   def respond_to_hand(user):
       bot.chat.send(f"{user.name}, a moderator will be with you shortly.")

   bot.run()

Async bot
---------

.. code-block:: python

   import sbc

   async def main():
       async with sbc.async_client("teacher.sbc") as bot:
           await bot.chat.send("SBC is online")
           async for user in bot.events.user_joined():
               await bot.chat.send(f"Welcome {user.name}")

Use the repository's ``examples/`` directory for ready-to-run files covering
media, typed controllers, schema events, moderation, and event patterns.

Capability diagnostic
---------------------

``library_diagnostic.py`` checks every high-level read controller, opens the
built-in event streams, and validates all embedded BBB action definitions. It
writes a detailed JSON report containing safe controller payloads, stream
observations, controller signatures, every action's required variables, local
GraphQL document, and a clear pass/fail/skip classification. It never ends the
meeting.

.. code-block:: bash

   python examples/library_diagnostic.py examples/teacher.sbc
   python examples/library_diagnostic.py examples/teacher.sbc --writes --send-chat
   python examples/library_diagnostic.py examples/teacher.sbc --event-seconds 20 --full-details

The optional write probe only performs reversible saved-user actions: mark the
chat read, typing, away on/off, and an activity sign. It sends no chat message
unless ``--send-chat`` is included.

Full server-side action plan
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

BBB actions such as breakout creation, moderation, polls, notes, recordings,
or presentation changes require real IDs and meeting-specific payloads. SBC
therefore does not invent values or fire them automatically. Generate a plan
containing all 109 mutations, replace the placeholders, and set only the cases
you want to test to ``true``:

.. code-block:: bash

   python examples/library_diagnostic.py --list-actions
   python examples/library_diagnostic.py --generate-action-plan sbc-action-plan.json
   python examples/library_diagnostic.py examples/teacher.sbc --action-plan sbc-action-plan.json --execute-plan --report full-report.json

Every plan entry is disabled by default. The diagnostic refuses to execute a
plan without ``--execute-plan`` and permanently excludes ``meetingEnd``. This
makes the generated report useful for deployment compatibility testing without
silently altering a live meeting.
