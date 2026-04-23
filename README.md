# SBC – Small Blue Client

**SBC (Small Blue Client)** is a Chrome extension designed to unlock **full automation capabilities** within BigBlueButton (BBB) meetings.  
It intercepts the internal GraphQL WebSocket communication, allowing you to execute any mutation, react to events, automate actions through custom scripts, and even spoof your microphone and camera with custom media files.

![Version](https://img.shields.io/badge/version-5.0.1-blue)
![Manifest](https://img.shields.io/badge/manifest-v3-green)

---

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [User Interface](#user-interface)
  - [Actions Tab](#actions-tab)
  - [Methods Tab](#methods-tab)
  - [Actions File Tab](#actions-file-tab)
  - [Automation Tab](#automation-tab)
  - [Spoof Tab](#spoof-tab)
  - [Executed Tab](#executed-tab)
  - [Responses Tab](#responses-tab)
  - [Info Tab](#info-tab)
  - [Custom Send Tab](#custom-send-tab)
- [Automation Scripts](#automation-scripts)
  - [Script Format](#script-format)
  - [Trigger Types](#trigger-types)
  - [Action Types](#action-types)
  - [Templates &amp; Placeholders](#templates--placeholders)
  - [Examples](#examples)
- [Media Spoofing](#media-spoofing)
- [Technical Architecture](#technical-architecture)
- [File Structure](#file-structure)
- [Permissions](#permissions)
- [Troubleshooting](#troubleshooting)
- [Disclaimer](#disclaimer)

---

## Features

- **One‑Click Actions**: Raise/lower hand, toggle mobile mode, set away status, react to messages.
- **Operation Explorer**: Discover all GraphQL mutations/subscriptions used by BBB in real time.
- **Custom Mutation Sender**: Build and send any GraphQL operation with a variable editor.
- **Automation Scripts**: Create event‑driven or time‑based scripts that react to chat messages, user joins, hand raises, voice events, etc.
- **Message Reaction Spammer**: Send massive random or selected emoji reactions to any message.
- **Live Clock in Messages**: Transform a message into a ticking clock that updates every second.
- **Media Spoofing**: Replace your microphone and/or camera with any audio/image/video file.
- **Debug Overlay &amp; Toasts**: Get visual feedback inside the BBB page.
- **Executed Operation History**: Rerun any previously executed operation with one click.
- **Response Inspector**: View raw responses for every operation you’ve sent.

---

## Installation

1. Download or clone this repository.
2. Open Chrome and navigate to `chrome://extensions`.
3. Enable **Developer mode** (toggle in the top‑right corner).
4. Click **Load unpacked** and select the folder containing `manifest.json`.
5. The SBC icon (🅂) will appear in your toolbar.
6. Open a BigBlueButton meeting in a tab – the extension will automatically activate on pages containing `/html5client`.

---

## Quick Start

1. Join any BBB meeting.
2. Click the SBC extension icon.
3. In the popup, the **Actions** tab lets you instantly toggle hand‑raise, mobile mode, and away status.
4. The **Custom Send** tab lets you send raw GraphQL operations.
5. Switch to the **Automation** tab, load a JSON file with your scripts, and they will run automatically inside the meeting.

---

## User Interface

The popup window (700px wide) is divided into several tabs:

### Actions Tab
Quick controls to:
- Raise / lower your hand.
- Enable / disable mobile mode.
- Set yourself as away / back.
- Send a reaction (👍 ❤️ 😄 🎉) to the most recent public chat message.

### Methods Tab
Displays all GraphQL operations (mutations/subscriptions/queries) that BBB has used since the extension was loaded.  
Each entry shows the operation name, type, and usage count. Click any method to populate the Custom Send tab with that query and its last‑known variables.  
You can **⭐ pin** methods to keep them at the top of the list (pinning persists in storage).

### Actions File Tab
Lists all officially supported BBB mutations parsed from `actions.json`.  
Click any mutation to open a form that asks for the required arguments, then automatically builds the GraphQL operation and loads it into the Custom Send tab.

### Automation Tab
The heart of SBC’s script engine:
- **Load JSON**: Import an automation script file (see [Automation Scripts](#automation-scripts) for format).
- **Clear All**: Remove all loaded scripts.
- Each script is shown with a toggle to enable/disable it without unloading.
- All script execution logs appear in the **Execution Log** box at the bottom.

### Spoof Tab
Allows you to replace your real microphone/camera with custom media:
- **Fake Microphone**: Upload an audio file (MP3, WAV, OGG). Enable looping. Test the stream before applying.
- **Fake Camera**: Upload a video file (MP4, WebM) or an image (JPG, PNG). Enable looping. Test the stream.
- **Apply to Page**: Sends the settings to the BBB tab and activates the spoofed devices.
- **Reset to Real Devices**: Disables all spoofing and restores real hardware.

Once applied, BBB will see two new devices: **SBC Fake Microphone** and **SBC Fake Camera**.  
*You may need to refresh the BBB page or re‑join the meeting for the new devices to appear in BBB’s media settings.*

### Executed Tab
Shows the latest 30 GraphQL operations that were actually sent (including those triggered by scripts).  
Click any row to reload its query and variables into the Custom Send tab.  
Use the **↻** button to re‑send that exact operation.  
Pinning works here as well.

### Responses Tab
Displays the responses received for each operation (by operation ID).  
Click an entry to see the full response JSON in an alert.

### Info Tab
- Displays your captured user info (name, ID, role) and WebSocket state.
- Shows the 5 most recent raw WebSocket messages (for quick debugging).

### Custom Send Tab
- A free‑form JSON editor for any GraphQL operation.
- **Variables Editor** lets you update each variable individually and then apply them to the JSON.
- **Example** loads a sample `userSetRaiseHand` mutation.
- **Format** pretty‑prints the current JSON.
- **Send Operation** delivers it to the BBB WebSocket.

---

## Automation Scripts

Automation scripts are JSON files that define **triggers** and **actions**. They are evaluated continuously inside the BBB page.

### Script Format

```json
{
  "scripts": [
    {
      "name": "My Script",
      "trigger": {
        "type": "onChatMessage",
        "params": { ... }
      },
      "action": {
        "type": "sendReaction",
        "params": { ... }
      },
      "runMode": "once"
    }
  ]
}
```

- `name` – unique (within the file) identifier for the script.
- `trigger` – specifies when the script should fire.
- `action` – what to do when the trigger conditions are met.
- `runMode` – `"once"` (fire only the first time) or `"forever"` (fire on every matching event).  
  Time‑based triggers (`onTime`) always respect this setting.

### Trigger Types

| Trigger type            | Description | Available params |
|-------------------------|-------------|------------------|
| `onChatMessage`         | A public/private chat message appears. | `senderId` (use `"SELF"` for your own messages), `senderName` (partial match), `text` (exact match), `chatId` |
| `onUserJoin`            | A user joins the meeting. | – |
| `onUserLeave`           | A user leaves. | – |
| `onRaiseHand`           | Somebody raises their hand. | – |
| `onUserVoiceConnect`    | A user connects to the voice bridge. | – |
| `onUserVoiceDisconnect` | A user disconnects from voice. | – |
| `onTime`                | A specific time of day (HH:MM). Multiple times allowed, comma‑separated. | `timePattern`: e.g. `"09:00, 12:30, 15:45"` |

### Action Types

| Action type         | Description | Key params |
|---------------------|-------------|------------|
| `sendReaction`      | Send emoji reactions to a message. | `messageId` (or auto‑captured), `mode`: `"random"` (count) or `"select"` (array of emojis) |
| `mutation`          | Execute any known mutation from `actions.json` or discovered operations. | `mutationName`, `variables` (supports placeholders) |
| `sendDirectMessage` | Send a private message to a user. | `userId`, `message` |
| `sendChatMessage`   | Send a message to a specific chat. | `chatId`, `message` |
| `startClock`        | Turn a message into a live clock. | `messageId`, `format` (default `"⏰ {{time}}"`) |
| `stopClock`         | Stop a live clock. | `messageId`, optionally `finalMessage` to replace it |
| `getMessageInfo`    | Log the author and text of a message. | `messageId` |
| `getUserInfo`       | Log a user’s name and role. | `userId` |
| `listUsers`         | Log all users currently in the meeting. | – |
| `getChatHistory`    | Log the most recent chat messages. | `count` (default 10) |

### Templates &amp; Placeholders

In action parameters, you can use dynamic placeholders:
- `{{userId}}`, `{{userName}}`, `{{messageId}}`, `{{messageText}}`, `{{chatId}}`, `{{replyToMessageId}}` – filled from the triggering event’s context.
- `{{time}}` – current time (for clock format).
- `{{date}}` – current date.
- `{{meetingId}}` – the BBB meeting ID.

Special input values:
- `"SELF_ID"`, `"AUTO_CAPTURE_ID"`, `"USER_ID"` → your own user ID.
- `"TARGET_ID"` → the user ID that triggered the event (e.g. the sender of a chat message).
- `"MESSAGE_ID"` → the message ID from the event.

### Examples

**1. Auto‑reply with a reaction and a message when someone says “hello”**

```json
{
  "scripts": [
    {
      "name": "Hello Reply",
      "trigger": {
        "type": "onChatMessage",
        "params": { "text": "hello" }
      },
      "action": {
        "type": "sendReaction",
        "params": {
          "messageId": "{{messageId}}",
          "mode": "select",
          "emojis": ["👋", "❤️"]
        }
      },
      "runMode": "forever"
    },
    {
      "name": "Hello Reply Text",
      "trigger": {
        "type": "onChatMessage",
        "params": { "text": "hello" }
      },
      "action": {
        "type": "sendChatMessage",
        "params": {
          "chatId": "{{chatId}}",
          "message": "Hi {{userName}}!"
        }
      },
      "runMode": "forever"
    }
  ]
}
```

**2. Time‑triggered message every morning**

```json
{
  "scripts": [
    {
      "name": "Good morning",
      "trigger": {
        "type": "onTime",
        "params": { "timePattern": "09:00" }
      },
      "action": {
        "type": "sendChatMessage",
        "params": {
          "chatId": "MAIN-PUBLIC-GROUP-CHAT",
          "message": "☀️ Good morning everyone!"
        }
      },
      "runMode": "forever"
    }
  ]
}
```

**3. Live clock on the last message**

```json
{
  "scripts": [
    {
      "name": "Start Clock",
      "trigger": {
        "type": "onChatMessage",
        "params": { "text": "!clock" }
      },
      "action": {
        "type": "startClock",
        "params": {
          "messageId": "{{messageId}}",
          "format": "🕒 Current time: {{time}}"
        }
      },
      "runMode": "once"
    }
  ]
}
```

*(After sending `!clock`, the message will update every second. Stop it with a script that calls `stopClock` on the same message.)*

For a detailed breakdown of all action types, see the popup’s **Actions File** tab, which visualises every mutation from BBB’s official API.

---

## Media Spoofing

SBC can replace your real media devices with custom audio/video streams:

- **Upload an audio file** (MP3, WAV, OGG) and toggle **Fake Microphone**.
- **Upload a video file or image** (MP4, WebM, JPG, PNG) and toggle **Fake Camera**.
- Use the **Test Stream** buttons to preview them directly on the BBB page before applying.
- Enable **Loop** for continuous playback.

Once applied, SBC overrides:
- `navigator.mediaDevices.getUserMedia` – exactly matches the device ID `sbc-fake-audio-device` or `sbc-fake-video-device`.
- `navigator.mediaDevices.enumerateDevices` – adds the two fake devices to the list.
- `MediaStreamTrack.prototype.getSettings` – ensures the fake devices report correctly.

BBB will see the fake devices and, if you select them in its settings, your “microphone” and “camera” will be replaced by your chosen media.

> **Important:**  
> After applying spoof settings, you may need to **refresh the BBB page** or **re‑join the meeting** for the fake devices to appear in the media settings dropdown. Once selected, they will work immediately.

---

## Technical Architecture

SBC uses a multi‑layer approach:

1. **Content Script (`content.js`)**  
   Loads automatically on any page containing `/html5client`.  
   Injects the main script (`inject.js`) into the page’s JavaScript context, bypassing isolation.  
   Acts as a bridge between the injected script, the popup, and the background.

2. **Injected Script (`inject.js`)**  
   - **WebSocket Interception**: Overrides `window.WebSocket` to intercept the `graphql-transport-ws` connection used by BBB.  
   - **GraphQL Parsing**: Listens to all sent/received messages, extracts operation names, queries, and variables.  
   - **Event Processing**: Analyzes incoming data (user joins, chat messages, voice activity, etc.) and builds a rich event model.  
   - **Caching**: Maintains caches of users and messages for fast lookups.  
   - **Script Engine**: Evaluates user‑defined automation scripts against every processed event.  
   - **Mutation Sender**: Provides functions to send any GraphQL operation (reactions, messages, edits, etc.).  
   - **Media Spoofing**: Overrides `getUserMedia` / `enumerateDevices` to inject fake audio/video streams.  
   - **Compatibility**: Forces `clientIsMobile = true` in the `UserJoin` mutation to improve cross‑platform compatibility.

3. **Popup UI (`popup.html` + `popup.js`)**  
   Reads stored settings, displays real‑time data from the BBB tab, and provides all controls.  
   Communication with the page happens via `chrome.tabs.sendMessage` and custom `postMessage` events.

4. **Actions Definitions (`actions.json`)**  
   A registry of official BBB mutations, parsed at startup to offer forms for building queries with correct argument types.

All inter‑component communication is asynchronous and timeout‑protected to avoid hanging the UI.

---

## File Structure

```
sbc/
├── manifest.json              # Chrome extension manifest (MV3)
├── content.js                 # Content script; injects inject.js, bridges messages
├── inject.js                  # Core injectable script: WebSocket interception, script engine, spoofing
├── popup.html                 # Popup UI structure
├── popup.js                   # Popup logic and event handling
├── actions.json               # Mutation definitions (official BBB API)
├── tw.js                      # (Optional) Tailwind CSS utility (not shown in sources)
└── icon700.png                # Extension icon
```

`tw.js` is a Tailwind‑style utility script (included in the original project) that styles the popup using class‑based CSS – you can replace it with any CSS framework or plain CSS.

---

## Permissions

- `activeTab` – to interact with the currently active BBB tab.
- `scripting` – to execute `content.js` programmatically if needed (though the content script is declared in the manifest).
- `storage` – to persist automation scripts, spoof settings, and pinned methods.
- `host_permissions: *://*/*` – required because BBB instances run on many different domains; the extension must work on all of them.
- `web_accessible_resources` – allows `inject.js` and `actions.json` to be loaded by the page.

---

## Troubleshooting

**Q: The popup shows “Not connected to BBB meeting”.**  
A: Make sure you are on a page whose URL contains `/html5client` (i.e. you are inside a BBB meeting). Refresh the BBB page first, then click the extension icon.

**Q: Automation scripts are not firing.**  
A: Check that the script is enabled (toggle in the Automation tab). For `onChatMessage`, ensure the trigger parameters (text, senderId, etc.) match exactly as intended. For `onTime`, the script must have been loaded *before* the specified time. Use the Execution Log tab to see script activity.

**Q: Reactions or mutations fail silently.**  
A: Open the **Executed** tab – it shows every operation sent. If an operation uses a mutation from `actions.json` but the query is wrong, BBB may reject it. Try re‑running the operation from the Methods or Actions File tab to see if it works manually.

**Q: Fake devices do not appear in BBB’s media settings.**  
A: After applying spoof settings in the popup, **refresh the BBB page** or **re‑join the meeting**. The fake devices will then appear as “SBC Fake Microphone” and “SBC Fake Camera”. Make sure you have uploaded a file and the toggle is ON.

**Q: The clock on a message stops or behaves erratically.**  
A: The clock uses `chatEditMessage` every second. If the mutation fails (e.g. you are not the message author or you lost connection), the interval continues but changes may not be visible. You can stop the clock via `stopClock` action or by clearing all scripts.

---

## Disclaimer

**SBC is intended for educational and testing purposes only.**  
Use of this extension may violate BigBlueButton’s terms of service. Do not use it to disrupt meetings, spam participants, or impersonate others. The developers assume no liability for any misuse or consequences arising from its use.

---

## Contributing

Contributions are welcome! Feel free to open issues or pull requests. Before submitting, please test your changes on a standard BBB 2.5+ meeting.

---

Happy automating! :robot:
