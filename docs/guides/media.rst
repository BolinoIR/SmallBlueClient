Media troubleshooting
=====================

SBC publishes custom media directly from Python. The extension is used only to
export the session.

.. code-block:: python

   sbc.enable_logging("DEBUG", structured=True)
   client.media.audio.prepare("warning.mp3")
   client.media.audio.play("warning.mp3", loop=False)

If publishing fails, first inspect ``client.media.credentials()``. It reports
the active backend. LiveKit requires a token exposed by BBB's current-user data;
BBB WebRTC SFU requires reachable TURN servers. DEBUG JSON logs include backend
selection, TURN server counts, ICE state, and connection state without logging
session tokens. Re-export the session if ``client.session.validate()`` reports
``requires_reexport``.
