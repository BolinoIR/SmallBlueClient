SmallBlueClient
===============

.. image:: ../icon700.png
   :alt: SmallBlueClient icon
   :width: 128px
   :align: center

SmallBlueClient (SBC) is a community Python automation toolkit for authenticated
BigBlueButton sessions exported by the SBC Chrome extension.

**Python-first release:** this repository publishes the Python library, examples,
tests, schema tools, documentation, and a deliberately minimal Chrome session
extractor. The extension only exports authenticated ``.sbc`` sessions; all BBB
automation is implemented by the Python package.

`View SmallBlueClient on GitHub <https://github.com/BolinoIR/SmallBlueClient>`_.
Documentation is published at `sbc.protobuf.lol <https://sbc.protobuf.lol>`_.

.. code-block:: python

   import sbc

   with sbc.client("teacher.sbc") as client:
       client.chat.send("Hello from SBC")

.. toctree::
   :maxdepth: 2
   :caption: Guides

   guides/getting-started
   guides/quick-reference
   guides/sessions
   guides/controllers
   guides/events
   guides/media
   guides/compatibility
   guides/python-only-release

.. toctree::
   :maxdepth: 2
   :caption: Reference

   reference/api
   reference/actions
   reference/schema
   API
