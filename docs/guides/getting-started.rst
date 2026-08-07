Getting started
===============

Install SBC and export a ``.sbc`` file from a BBB tab with the extension.

.. code-block:: console

   pip install SmallBlueClient

.. code-block:: python

   import sbc

   client = sbc.client("teacher.sbc")
   print(client.meeting.name)
   client.close()

For asyncio, use :func:`sbc.async_client` and await controller calls.
