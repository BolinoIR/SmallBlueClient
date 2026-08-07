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
