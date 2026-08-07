# SBC Session Extractor

This intentionally small Chrome extension is **not** an automation client. It
passively observes the active BigBlueButton page's real GraphQL WebSocket
connection, captures the authenticated connection payload and browser cookies,
and exports a portable `.sbc` file for the Python library.

## Load locally

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Select **Load unpacked** and choose this `extension/` directory.
4. Join a BBB meeting, open the extractor popup, then export the session.

The extension does not inject actions, modify media, start a bridge, or replay
GraphQL requests. Treat every exported `.sbc` file as a browser credential.
