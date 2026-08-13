Events and lifecycle streams
============================

.. code-block:: python

   @client.on("breakout_started")
   def started(room):
       print(room["name"])

   @client.on(sbc.Event.USER_JOINED, priority=10)
   def joined(user):
       print(user.name)

Built-in events reconnect automatically and use one multiplexed GraphQL socket.
The initial BBB user-table result is treated as a baseline; ``user_joined`` is
emitted only for participants who appear after the bot begins watching.
Breakout lifecycle events are ``breakout_created``, ``breakout_started``,
``breakout_updated``, and ``breakout_ended``.

Incoming audio and local transcripts add ``audio_frame``,
``transcript_segment``, and ``transcript_final``. They are also available as
the typo-safe :class:`sbc.Event` values ``AUDIO_FRAME``,
``TRANSCRIPT_SEGMENT``, and ``TRANSCRIPT_FINAL``.

.. code-block:: python

   @client.on(sbc.Event.TRANSCRIPT_SEGMENT)
   def transcript(segment):
       print(segment.user_name, segment.text)

Plugin data-channel listeners are registered before ``client.run()``:

.. code-block:: python

   client.plugins.listen(lambda entry: print(entry.payload), plugin="sample")
