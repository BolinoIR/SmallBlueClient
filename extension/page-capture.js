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
    // Ephemeral credentials observed from BBB's own STUN/TURN fetch. They
    // are required by content media on deployments which do not permit
    // private host ICE candidates to reach the SFU.
    iceServers: null,
    // Export eligibility is based on browser-observed BBB media state, not
    // merely on the GraphQL session. A GraphQL-only export cannot reliably
    // create an SFU listener or microphone later in Python.
    sfu: {
      observed: false,
      socketOpen: false,
      startRequested: false,
      startAccepted: false,
      audioSuccess: false,
      audioMode: null,
      peerConnectionState: "new",
      iceConnectionState: "new",
      lastError: null,
    },
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
      // ScreenshareBroker uses these source-defined kurento.screenshare
      // fields for a send-only ``type: screenshare`` SFU session.
      screenshare_media_server: kurento.screenshare?.mediaServer ?? null,
      screenshare_bitrate: kurento.screenshare?.bitrate ?? 1500,
      signal_candidates: kurento.signalCandidates ?? false,
      // BBB's HTML5 initial settings use five seconds when no deployment
      // override is supplied.
      ice_gathering_timeout: configuredTimeout ?? 5000,
      stun_turn_url: media.stunTurnServersFetchAddress || "/bigbluebutton/api/stuns",
    };
  }
  function iceCredentialExpiry(turnServers) {
    for (const server of turnServers || []) {
      const value = String(server?.username || "").split(":", 1)[0];
      const timestamp = Number(value);
      if (Number.isFinite(timestamp) && timestamp > 0) {
        return new Date(timestamp * 1000).toISOString();
      }
    }
    return null;
  }
  function observeIceResponse(url, payload) {
    if (!payload || typeof payload !== "object") return;
    const settings = bbbWebrtcSfuSnapshot();
    const expected = String(settings.stun_turn_url || "").split("?")[0];
    const pathname = new URL(String(url), location.href).pathname;
    if (!/stuns|turn/i.test(pathname) && !pathname.endsWith(expected)) return;
    const stunServers = Array.isArray(payload.stunServers) ? payload.stunServers : [];
    const turnServers = Array.isArray(payload.turnServers) ? payload.turnServers : [];
    if (!turnServers.length) return;
    // Copy only the normalised browser response. The extension never asks for
    // credentials, retries the request, or changes BBB media behaviour.
    state.iceServers = {
      endpoint: new URL(String(url), location.href).pathname,
      captured_at: new Date().toISOString(),
      expires_at: iceCredentialExpiry(turnServers),
      stun_servers: clone(stunServers),
      turn_servers: clone(turnServers),
    };
    notify();
  }
  function observeIceText(url, text) {
    try { observeIceResponse(url, JSON.parse(text)); } catch (_) {}
  }

  function hasFreshTurnCredentials() {
    const credentials = state.iceServers;
    if (!credentials?.turn_servers?.length) return false;
    const expiresAt = Date.parse(credentials.expires_at || "");
    return !Number.isFinite(expiresAt) || expiresAt > Date.now() + 15_000;
  }

  function mediaStatus() {
    const sfu = state.sfu;
    const reasons = [];
    if (!sfu.observed || !sfu.startRequested) {
      reasons.push("Connect to BBB audio as a listener or microphone first.");
    }
    if (!state.iceServers?.turn_servers?.length) {
      reasons.push("Waiting for BBB to retrieve the meeting TURN/ICE credentials.");
    } else if (!hasFreshTurnCredentials()) {
      reasons.push("The captured TURN/ICE credentials have expired; reconnect audio and export a fresh session.");
    }
    if (sfu.startRequested && !sfu.audioSuccess) {
      reasons.push("Waiting for BBB's SFU audio success signal.");
    }
    if (sfu.audioSuccess && sfu.peerConnectionState !== "connected") {
      reasons.push("Waiting for the browser's WebRTC audio peer to connect.");
    }
    return {
      ready: state.detected && sfu.socketOpen && sfu.audioSuccess
        && sfu.peerConnectionState === "connected" && hasFreshTurnCredentials(),
      audio_mode: sfu.audioMode,
      sfu_socket_open: sfu.socketOpen,
      sfu_audio_success: sfu.audioSuccess,
      peer_connection_state: sfu.peerConnectionState,
      ice_connection_state: sfu.iceConnectionState,
      turn_credentials: hasFreshTurnCredentials(),
      reasons,
    };
  }

  const session = () => {
    const media = mediaStatus();
    return {
      detected: state.detected,
      media,
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
        ...(state.iceServers ? { ice_servers: clone(state.iceServers) } : {}),
        ...(state.meeting.screenShareBridge ? { screenshare_backend: state.meeting.screenShareBridge } : {}),
        ...(state.livekit.token ? { livekit: clone(state.livekit) } : {}),
        // Kept as provenance for Python-side diagnostics. The popup refuses
        // export until this capture is ready, so this is never a GraphQL-only
        // session package.
        media_capture: clone(media),
      },
      headers: {},
    },
    };
  };

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
        screenShareBridge: typeof value.screenShareBridge === "string"
          ? value.screenShareBridge
          : state.meeting.screenShareBridge,
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

  function isSfuUrl(url) {
    try { return /\/bbb-webrtc-sfu\/?$/i.test(new URL(String(url), location.href).pathname); } catch (_) { return false; }
  }

  function observeSfuOutgoing(payload) {
    if (payload?.id !== "start" || payload?.type !== "audio") return;
    state.sfu = {
      ...state.sfu,
      observed: true,
      startRequested: true,
      startAccepted: false,
      audioSuccess: false,
      audioMode: payload.role === "recv" ? "listener" : "microphone",
      peerConnectionState: "new",
      iceConnectionState: "new",
      lastError: null,
    };
  }

  function observeSfuIncoming(payload) {
    if (!payload || typeof payload !== "object") return;
    if (payload.id === "startResponse" && payload.response === "accepted") {
      state.sfu.startAccepted = true;
    } else if (payload.id === "webRTCAudioSuccess") {
      state.sfu.audioSuccess = true;
    } else if (payload.id === "webRTCAudioError" || payload.id === "error") {
      state.sfu.lastError = payload.reason || "BBB SFU rejected the audio connection";
    }
  }

  function observePeerConnection(peer) {
    const update = () => {
      // BBB creates its audio RTCPeerConnection immediately after sending the
      // SFU start request. Ignore unrelated peers until that exact sequence
      // has been observed.
      if (!state.sfu.startRequested) return;
      state.sfu.peerConnectionState = peer.connectionState || "new";
      state.sfu.iceConnectionState = peer.iceConnectionState || "new";
      if (state.sfu.peerConnectionState === "failed" || state.sfu.peerConnectionState === "closed") {
        state.sfu.lastError = `Browser WebRTC state: ${state.sfu.peerConnectionState}`;
      }
      notify();
    };
    peer.addEventListener("connectionstatechange", update);
    peer.addEventListener("iceconnectionstatechange", update);
  }

  const NativeWebSocket = window.WebSocket;
  function ObservedWebSocket(...args) {
    const socket = new NativeWebSocket(...args);
    const [url, protocols] = args;
    if (typeof url === "string" && (/graphql/i.test(url) || String(protocols).includes("graphql-transport-ws"))) {
      state.websocketUrl = url;
      state.protocol = Array.isArray(protocols) ? (protocols.includes("graphql-transport-ws") ? "graphql-transport-ws" : protocols[0]) : (protocols || "graphql-transport-ws");
    }
    const sfuSocket = isSfuUrl(url);
    if (sfuSocket) {
      state.sfu.observed = true;
      socket.addEventListener("open", () => { state.sfu.socketOpen = true; notify(); });
      socket.addEventListener("close", () => {
        state.sfu.socketOpen = false;
        state.sfu.audioSuccess = false;
        notify();
      });
      socket.addEventListener("error", () => { state.sfu.lastError = "BBB SFU WebSocket error"; notify(); });
    }
    const nativeSend = socket.send;
    socket.send = function (message) {
      try {
        const payload = typeof message === "string" ? JSON.parse(message) : null;
        observeGraphqlPayload(payload);
        if (sfuSocket) observeSfuOutgoing(payload);
        notify();
      } catch (_) {}
      return nativeSend.call(this, message);
    };
    socket.addEventListener("message", async (event) => {
      try {
        const text = typeof event.data === "string" ? event.data : await event.data.text();
        const payload = JSON.parse(text);
        visit(payload);
        if (sfuSocket) observeSfuIncoming(payload);
        notify();
      } catch (_) {}
    });
    return socket;
  }
  ObservedWebSocket.prototype = NativeWebSocket.prototype;
  Object.setPrototypeOf(ObservedWebSocket, NativeWebSocket);
  window.WebSocket = ObservedWebSocket;

  // This observes state changes only. It does not create, alter, or control
  // BBB's WebRTC peer; BBB remains solely responsible for its media flow.
  const NativeRTCPeerConnection = window.RTCPeerConnection;
  if (NativeRTCPeerConnection) {
    function ObservedRTCPeerConnection(...args) {
      const peer = new NativeRTCPeerConnection(...args);
      observePeerConnection(peer);
      return peer;
    }
    ObservedRTCPeerConnection.prototype = NativeRTCPeerConnection.prototype;
    Object.setPrototypeOf(ObservedRTCPeerConnection, NativeRTCPeerConnection);
    window.RTCPeerConnection = ObservedRTCPeerConnection;
  }
  // BBB source fetches STUN/TURN data with ``fetch(..., {credentials:
  // 'include'})``. Observe that already-authorized browser response so a
  // fresh exported session has the exact short-lived ICE configuration.
  const nativeFetch = window.fetch?.bind(window);
  if (nativeFetch) {
    window.fetch = async function observedFetch(...args) {
      const response = await nativeFetch(...args);
      const url = typeof args[0] === "string" ? args[0] : args[0]?.url;
      if (url) response.clone().text().then((text) => observeIceText(url, text)).catch(() => {});
      return response;
    };
  }
  const NativeXHR = window.XMLHttpRequest;
  if (NativeXHR) {
    const nativeOpen = NativeXHR.prototype.open;
    NativeXHR.prototype.open = function observedOpen(method, url, ...rest) {
      this.__sbcCaptureUrl = url;
      this.addEventListener("load", () => observeIceText(this.__sbcCaptureUrl, this.responseText));
      return nativeOpen.call(this, method, url, ...rest);
    };
  }

  window.addEventListener("message", (event) => {
    if (event.source === window && event.data?.type === "SBC_REQUEST_PAGE_CAPTURE") {
      window.postMessage({ type: "SBC_PAGE_CAPTURE", requestId: event.data.requestId, capture: session() }, "*");
    }
  });
})();
