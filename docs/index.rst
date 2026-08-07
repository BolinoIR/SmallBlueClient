SmallBlueClient
===============

.. image:: ../icon700.png
   :alt: SmallBlueClient icon
   :width: 128px
   :align: center

SmallBlueClient (SBC) is a community Python automation toolkit for authenticated
BigBlueButton sessions exported by the SBC Chrome extension.

**Python-first release:** this repository currently publishes the Python
library, examples, tests, schema tools, and documentation. The browser extension
is being revised separately and is intentionally not included in this release.

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
