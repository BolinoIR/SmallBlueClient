// Page-world passive observer for BigBlueButton's GraphQL connection. The
// values saved below are observed from BBB's own websocket messages, not
// inferred operation names and never replayed by the extension.
(() => {
  if (window.__SBC_SESSION_EXTRACTOR__) return;
  window.__SBC_SESSION_EXTRACTOR__ = true;

  const state = {
    detected: false,
    websocketUrl: null,
    protocol: "graphql-transport-ws",
    connectionPayload: {},
    currentUser: {},
    livekit: {},
    meeting: {},
  };
  const bbbWords = /\b(meeting|user|chat|voice|presentation|breakoutRoom|screenshare)\b/gi;

  const clone = (value) => JSON.parse(JSON.stringify(value));
  const notify = () => window.postMessage({ type: "SBC_PAGE_CAPTURE_UPDATED", capture: session() }, "*");
  const session = () => ({
    detected: state.detected,
    session: {
      version: 1,
      metadata: {
        exported_by: "SBC Session Extractor",
        exported_at: new Date().toISOString(),
        page_url: location.href,
      },
      server: location.origin,
      websocket_url: state.websocketUrl,
      meeting_id: state.meeting.id || null,
      meeting_name: state.meeting.name || document.title || null,
      user_id: state.currentUser.id || null,
      user_name: state.currentUser.name || null,
      role: state.currentUser.role || null,
      protocol: state.protocol,
      connection_payload: clone(state.connectionPayload),
      snapshot: {
        current_user: state.currentUser.id ? {
          user_id: state.currentUser.id,
          auth_token: state.currentUser.authToken || null,
          joined: Boolean(state.currentUser.joined),
          currently_in_meeting: Boolean(state.currentUser.currentlyInMeeting),
          logged_out: Boolean(state.currentUser.loggedOut),
          ejected: Boolean(state.currentUser.ejected),
          join_error_code: state.currentUser.joinErrorCode || null,
          join_error_message: state.currentUser.joinErrorMessage || null,
        } : {},
        ...(state.livekit.token ? { livekit: clone(state.livekit) } : {}),
      },
      headers: {},
    },
  });

  function visit(value, seen = new WeakSet()) {
    if (!value || typeof value !== "object" || seen.has(value)) return;
    seen.add(value);
    if (typeof value.userId === "string") {
      state.currentUser = { ...state.currentUser, id: value.userId, name: value.name || state.currentUser.name, role: value.role || state.currentUser.role, authToken: value.authToken || state.currentUser.authToken, joined: value.joined, currentlyInMeeting: value.currentlyInMeeting, loggedOut: value.loggedOut, ejected: value.ejected, joinErrorCode: value.joinErrorCode, joinErrorMessage: value.joinErrorMessage };
    }
    if (typeof value.meetingId === "string") state.meeting = { id: value.meetingId, name: value.name || value.meetingName || state.meeting.name };
    if (typeof value.livekitToken === "string") state.livekit = { token: value.livekitToken, url: value.livekitUrl || `wss://${location.host}/livekit` };
    Object.values(value).forEach((child) => visit(child, seen));
  }

  function observeGraphqlPayload(payload) {
    if (payload?.type === "connection_init" && payload.payload && typeof payload.payload === "object") {
      state.connectionPayload = clone(payload.payload);
    }
    const query = payload?.payload?.query;
    const hits = String(query || "").match(bbbWords) || [];
    if (hits.length >= 2 && /\b(subscription|mutation|query)\b/i.test(String(query))) state.detected = true;
  }

  const NativeWebSocket = window.WebSocket;
  function ObservedWebSocket(...args) {
    const socket = new NativeWebSocket(...args);
    const [url, protocols] = args;
    if (typeof url === "string" && (/graphql/i.test(url) || String(protocols).includes("graphql-transport-ws"))) {
      state.websocketUrl = url;
      state.protocol = Array.isArray(protocols) ? (protocols.includes("graphql-transport-ws") ? "graphql-transport-ws" : protocols[0]) : (protocols || "graphql-transport-ws");
    }
    const nativeSend = socket.send;
    socket.send = function (message) {
      try { observeGraphqlPayload(typeof message === "string" ? JSON.parse(message) : null); notify(); } catch (_) {}
      return nativeSend.call(this, message);
    };
    socket.addEventListener("message", async (event) => {
      try {
        const text = typeof event.data === "string" ? event.data : await event.data.text();
        visit(JSON.parse(text));
        notify();
      } catch (_) {}
    });
    return socket;
  }
  ObservedWebSocket.prototype = NativeWebSocket.prototype;
  Object.setPrototypeOf(ObservedWebSocket, NativeWebSocket);
  window.WebSocket = ObservedWebSocket;

  window.addEventListener("message", (event) => {
    if (event.source === window && event.data?.type === "SBC_REQUEST_PAGE_CAPTURE") {
      window.postMessage({ type: "SBC_PAGE_CAPTURE", requestId: event.data.requestId, capture: session() }, "*");
    }
  });
})();
