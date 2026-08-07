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

Examples
--------

Install the stable release:

.. code-block:: bash

   pip install SmallBlueClient

Build a small bot with the synchronous API:

.. code-block:: python

   import sbc

   with sbc.client("teacher.sbc") as client:
       client.chat.send("Hello from SBC")

.. toctree::
   :maxdepth: 2
   :caption: API Documents

   reference/api
   reference/actions
   reference/schema
   API

.. toctree::
   :maxdepth: 2
   :caption: Guides

   guides/getting-started
   guides/quick-reference
   guides/sessions
   guides/controllers
   guides/events
   guides/media
   guides/reliability
   guides/cli
   guides/bots
   guides/compatibility
   guides/python-only-release

.. toctree::
   :maxdepth: 2
   :caption: Examples

   guides/examples

.. toctree::
   :maxdepth: 1
   :caption: Project Links

   GitHub repository <https://github.com/BolinoIR/SmallBlueClient>
   PyPI package <https://pypi.org/project/smallblueclient/>
   Documentation website <https://sbc.protobuf.lol/>
   Full changelog <https://github.com/BolinoIR/SmallBlueClient/blob/main/CHANGELOG.md>
