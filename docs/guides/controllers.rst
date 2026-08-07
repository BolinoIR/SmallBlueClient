Controllers
===========

SBC uses named, typed controllers instead of raw GraphQL strings.

.. code-block:: python

   client.reactions.set(sbc.Reaction.RAISE_HAND)
   client.screenshare.set_as_content(True)
   messages = client.chat.public_history()
   private_messages = client.chat.private_history()
   client.chat.delete(messages[0].chat_id, messages[0].id)

   presentations = client.presentations.list()
   client.presentations.export(
       presentations[0].id,
       file_state_type=sbc.PresentationFileState.CONVERTED,
   )
   client.plugins.listen(print, plugin="my-plugin")

Read APIs include polls, breakouts, cameras, recordings, guests, captions, and
whiteboard annotations. All 109 source-derived mutation methods remain available
through ``client.actions`` and ``client.mutation``.
