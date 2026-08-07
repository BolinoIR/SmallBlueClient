Bot framework
=============

The optional bot helpers keep command parsing, cooldowns, permissions,
periodic tasks, and small persistent state out of application code.

.. code-block:: python

   import sbc
   client = sbc.client("teacher.sbc")
   bot = sbc.Bot(client, prefix="!")

   @bot.command(cooldown=3, permission=sbc.Bot.moderator)
   def status(ctx: sbc.CommandContext):
       ctx.reply("SBC is online.")

   @bot.task(interval=60)
   def save_counter():
       bot.state.set("heartbeats", bot.state.get("heartbeats", 0) + 1)

   bot.start()
   client.run()

The command layer filters the saved SBC identity so bots do not reply to
their own messages. ``BotState`` is a human-readable JSON file saved after
each change and when the bot closes.

Native async GraphQL
--------------------

``AsyncSBCClient.graphql`` and ``AsyncSBCClient.subscribe`` use an asyncio
WebSocket transport directly. This is useful for deployment-specific schema
queries while the typed controller API remains identical to synchronous SBC.

.. code-block:: python

   async with sbc.async_client("teacher.sbc") as client:
       data = await client.query("query { meeting { name } }")
       print(data)

       async for event in client.subscribe("subscription { meeting { ended } }"):
           if event["meeting"][0]["ended"]:
               break
