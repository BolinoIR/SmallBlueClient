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

  // BBB stores the authenticated meeting name in session storage during the
  // join flow (see join-handler/presenceManager/service.ts). Read it only as
  // a fallback; the visible BBB title is normally the most useful exported
  // meeting label.
  const storedMeetingName = () => {
    try {
      const value = sessionStorage.getItem("meetingName");
      return typeof value === "string" && value.trim() ? value.trim() : null;
    } catch (_) {
      return null;
    }
  };

  const pageMeetingName = () => {
    const title = String(document.title || "").trim();
    // Do not export the generic application title as a meeting name.
    if (title && !/^BigBlueButton\s*$/i.test(title)) return title;
    return storedMeetingName();
  };

  /**
   * Read the exact public client settings used by BBB's HTML5 client for its
   * bbb-webrtc-sfu bridge. These names mirror the BBB source:
   *
   * - public.kurento.wsUrl, listenOnlyMediaServer, videoMediaServer,
   *   signalCandidates and gatheringTimeout
   * - public.media.audio.fullAudioMediaServer, fullAudioOffering,
   *   listenOnlyOffering, transparentListenOnly, iceGatheringTimeout and
   *   stunTurnServersFetchAddress
   *
   * This is a passive settings read. SBC never opens or controls an SFU
   * connection from the extension.
   */
  function bbbWebrtcSfuSnapshot() {
    const settings = window.meetingClientSettings?.public || {};
    const kurento = settings.kurento || {};
    const media = settings.media || {};
    const configuredUrl = kurento.wsUrl;
    const websocketOrigin = location.protocol === "https:" ? "wss:" : "ws:";
    let url = `${websocketOrigin}//${location.host}/bbb-webrtc-sfu`;

    if (typeof configuredUrl === "string" && configuredUrl && configuredUrl !== "HOST") {
      try {
        url = new URL(configuredUrl, location.origin).toString();
      } catch (_) {
        // Preserve the BBB HTML5 default URL when a deployment setting is not
        // a valid browser URL.
      }
    }

    const configuredTimeout = kurento.gatheringTimeout ?? media.iceGatheringTimeout;
    return {
      url,
      audio_media_server: media.audio?.fullAudioMediaServer ?? null,
      listen_only_media_server: kurento.listenOnlyMediaServer ?? null,
      // These three settings control the exact AudioBroker SDP negotiation
      // path. SBC's Python microphone publisher mirrors it rather than
      // assuming that every BBB deployment is a local SDP offerer.
      full_audio_offering: media.fullAudioOffering ?? true,
      listen_only_offering: media.listenOnlyOffering ?? false,
      transparent_listen_only: media.transparentListenOnly ?? true,
      camera_media_server: kurento.videoMediaServer ?? null,
      signal_candidates: kurento.signalCandidates ?? false,
      // BBB's HTML5 initial settings use five seconds when no deployment
      // override is supplied.
      ice_gathering_timeout: configuredTimeout ?? 5000,
      stun_turn_url: media.stunTurnServersFetchAddress || "/bigbluebutton/api/stuns",
    };
  }

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
      meeting_name: state.meeting.name || pageMeetingName() || null,
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
        bbb_webrtc_sfu: bbbWebrtcSfuSnapshot(),
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
    if (typeof value.meetingId === "string") {
      // `user` rows contain both `meetingId` and `name`; the latter is the
      // participant/bot name, *not* the meeting title. Only accept BBB's
      // explicitly named `meetingName` property here.
      state.meeting = {
        id: value.meetingId,
        name: typeof value.meetingName === "string" && value.meetingName.trim()
          ? value.meetingName.trim()
          : state.meeting.name,
      };
    }
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
