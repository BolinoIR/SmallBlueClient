Session health and re-exporting
===============================

An ``.sbc`` package contains the authenticated browser session. Inspect it before
running a bot:

.. code-block:: python

   health = client.session.validate()
   print(health.to_dict())
   print(client.session.expires_at)

``SessionHealth`` reports local format problems, known expiry timestamps, and
whether BBB rejected the captured credential. When SBC receives an expired or
unauthorized GraphQL response it sets ``client.session.requires_reexport`` and
records the reason. Export a fresh session from the extension, then replace the
old file. SBC never overwrites a loaded session automatically.
