# SBC Session Extractor

This intentionally small Chrome extension is **not** an automation client. It
passively observes the active BigBlueButton page's real GraphQL WebSocket
connection, captures the authenticated connection payload and browser cookies,
and exports a portable `.sbc` file for the Python library.

## Version 1.0.6

Exports now include BBB's configured SFU audio negotiation settings
(``fullAudioOffering`` and ``transparentListenOnly``) so the Python media
publisher can use the same source-defined SDP role as the HTML5 client.

## Version 1.0.5

Exports now always download as ``.sbc`` credential packages rather than JSON
documents. Each session includes the BBB HTML5 client's public
``bbb_webrtc_sfu`` settings snapshot, including the audio/listen-only/camera
media-server fields and the ICE/STUN configuration used by the Python media
client.

## Load locally

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Select **Load unpacked** and choose this `extension/` directory.
4. Join a BBB meeting, open the extractor popup, then export the session.

The extension does not inject actions, modify media, start a bridge, or replay
GraphQL requests. Treat every exported `.sbc` file as a browser credential.
