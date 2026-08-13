# SBC Session Extractor

This intentionally small Chrome extension is **not** an automation client. It
passively observes the active BigBlueButton page's real GraphQL WebSocket
connection, captures the authenticated connection payload and browser cookies,
and exports a portable `.sbc` file for the Python library.

## Version 1.0.9

The extractor now blocks exports until the active BBB tab has completed a
real listener or microphone connection. Before exporting, select **Listen
only** or **Microphone** in BBB and wait for BBB's headphone/microphone icon.
The extractor then requires all of the following browser-observed state:

- the BBB SFU WebSocket is open;
- BBB accepted the audio `start` request and sent `webRTCAudioSuccess`;
- the browser WebRTC audio peer is connected; and
- BBB has returned fresh TURN/ICE credentials.

This prevents exporting GraphQL-only sessions that cannot later establish the
Python media connection.

## Version 1.0.7

Exports now include the BBB screenshare bridge plus the source-defined SFU
screenshare media-server and bitrate settings. This allows the Python visual
screenshare framework to select the same BBB backend as the HTML5 client.

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
4. Join a BBB meeting and select **Listen only** or **Microphone**.
5. Wait until BBB displays the headphone or microphone icon.
6. Open the extractor popup and export the media-ready session.

The extension does not inject actions, modify media, start a bridge, or replay
GraphQL requests. Treat every exported `.sbc` file as a browser credential.
