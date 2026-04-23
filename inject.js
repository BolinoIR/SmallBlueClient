// inject.js
(function() {
    const LOG_PREFIX = '[📊 SBC]';
    const GRAPHQL_WS_SUBPROTOCOL = 'graphql-transport-ws';

    // User info
    let userInfo = {
        name: null, userId: null, role: null, authed: false,
        joined: false, meetingId: null, rawData: null
    };
    let userInfoCaptured = false;
    let graphqlWs = null;

    // Message histories
    const messageHistory = [];
    const outgoingOperations = [];
    const executedOperations = [];
    const operationResponses = new Map();
    const discoveredMethods = new Map();
    const MAX_HISTORY = 100;

    // Last chat message ID (for reactions)
    let lastChatMessageId = null;
    // Last sent message ID (for potential editing)
    let lastSentMessageId = null;

    // Active automation scripts – loaded from storage
    let activeScripts = [];
    let scriptTriggersFired = new Set();
    
    // Actions.json mutation definitions (sent from popup)
    let actionsMutations = [];

    // Cached data for quick access
    let cachedUsers = new Map(); // userId -> user object
    let cachedMessages = new Map(); // messageId -> message object

    // Active clocks: messageId -> { intervalId, chatId, format }
    const activeClocks = new Map();

    // Time-based trigger interval
    let timeCheckInterval = null;

    // Debug overlay
    let debugOverlay = null;

    // --- Spoofing state and helpers ---
    let spoofSettings = {
        audioEnabled: false,
        videoEnabled: false,
        audioBase64: null,
        videoBase64: null,
        audioMime: null,
        videoMime: null,
        audioLoop: true,
        videoLoop: true
    };
    let originalGetUserMedia = null;
    let originalEnumerateDevices = null;

    // Cache for fake streams to avoid re‑creating them on every call
    const fakeStreamCache = {
        audio: null,   // MediaStream
        video: null    // MediaStream
    };

    // --- Enhanced Logging ---
    function log(level, ...args) {
        const timestamp = new Date().toISOString();
        console[level](`${LOG_PREFIX} [${timestamp}]`, ...args);
        // Send logs to the popup for easy viewing
        window.postMessage({
            type: 'SBC_DEBUG_LOG',
            payload: { level, timestamp, args: args.map(String) }
        }, '*');
    }

    // --- Helper: Convert base64 to Blob ---
    async function base64ToBlob(base64, mime) {
        const response = await fetch(base64);
        return await response.blob();
    }

    // --- Decorate a fake track with overridden methods/properties ---
    function decorateFakeTrack(track, kind, deviceId, label) {
        // Override basic properties
        Object.defineProperty(track, 'label', { value: label, configurable: true });
        Object.defineProperty(track, 'deviceId', { value: deviceId, configurable: true });
        Object.defineProperty(track, 'kind', { value: kind, configurable: true });

        // Store original methods
        const originalGetSettings = track.getSettings;
        const originalGetCapabilities = track.getCapabilities;
        const originalGetConstraints = track.getConstraints;

        // Override getSettings to return the fake deviceId
        track.getSettings = function() {
            const settings = originalGetSettings.call(this);
            settings.deviceId = deviceId;
            // Also ensure other common properties are present
            if (kind === 'audioinput') {
                settings.echoCancellation = false;
                settings.noiseSuppression = false;
                settings.autoGainControl = false;
            } else if (kind === 'videoinput') {
                settings.frameRate = 30;
                settings.width = 640;
                settings.height = 480;
            }
            return settings;
        };

        // Override getCapabilities if it exists
        if (originalGetCapabilities) {
            track.getCapabilities = function() {
                const caps = originalGetCapabilities.call(this) || {};
                caps.deviceId = deviceId;
                return caps;
            };
        }

        // Override getConstraints if it exists
        if (originalGetConstraints) {
            track.getConstraints = function() {
                const constraints = originalGetConstraints.call(this) || {};
                if (constraints.deviceId) {
                    constraints.deviceId = { exact: deviceId };
                } else {
                    constraints.deviceId = { exact: deviceId };
                }
                return constraints;
            };
        }

        // Ensure the track reports as live
        Object.defineProperty(track, 'readyState', {
            get: () => 'live',
            configurable: true
        });

        return track;
    }

    // Override MediaStreamTrack.prototype.getSettings globally for any track that might be a fake one
    // This is a safety net in case our decoration doesn't catch everything.
    (function patchMediaStreamTrack() {
        const originalGetSettings = MediaStreamTrack.prototype.getSettings;
        MediaStreamTrack.prototype.getSettings = function() {
            const settings = originalGetSettings.call(this);
            // If this track looks like one of our fake ones, inject the correct deviceId
            if (this.label === 'SBC Fake Microphone' || this.label === 'SBC Fake Camera') {
                if (this.kind === 'audio') {
                    settings.deviceId = 'sbc-fake-audio-device';
                } else if (this.kind === 'video') {
                    settings.deviceId = 'sbc-fake-video-device';
                }
            }
            return settings;
        };
    })();

    // --- Set fake device as default in BBB's localStorage (aggressive) ---
    function forceFakeDeviceAsDefault() {
        try {
            const audioKey = 'audioInputDeviceId';
            const videoKey = 'videoInputDeviceId';
            if (spoofSettings.audioEnabled) {
                localStorage.setItem(audioKey, 'sbc-fake-audio-device');
                log('info', 'Forced default audio input to fake device.');
            }
            if (spoofSettings.videoEnabled) {
                localStorage.setItem(videoKey, 'sbc-fake-video-device');
                log('info', 'Forced default video input to fake device.');
            }
            // Also override any stored device list to force re-enumeration
            localStorage.removeItem('audioOutputDeviceId'); // optional
        } catch (e) {
            log('warn', 'Could not set fake device default:', e);
        }
    }

    // --- Creates and caches a fake audio stream using Web Audio API. ---
    async function getFakeAudioStream() {
        if (!spoofSettings.audioEnabled || !spoofSettings.audioBase64) {
            log('warn', 'Audio spoofing disabled or no base64.');
            return null;
        }
        if (fakeStreamCache.audio) {
            if (fakeStreamCache.audio.getAudioTracks().some(t => t.readyState === 'live')) {
                log('info', 'Returning cached fake audio stream.');
                return fakeStreamCache.audio;
            } else {
                log('warn', 'Cached audio stream dead, recreating...');
                cleanupFakeStreams();
            }
        }
        try {
            log('info', 'Creating fake audio stream via Web Audio API...');
            const audioContext = new (window.AudioContext || window.webkitAudioContext)();
            const blob = await base64ToBlob(spoofSettings.audioBase64, spoofSettings.audioMime);
            const arrayBuffer = await blob.arrayBuffer();
            const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);
            const source = audioContext.createBufferSource();
            source.buffer = audioBuffer;
            source.loop = spoofSettings.audioLoop;
            const destination = audioContext.createMediaStreamDestination();
            source.connect(destination);
            source.connect(audioContext.destination); // local playback for debugging
            source.start(0);
            const stream = destination.stream;
            
            // Decorate all audio tracks
            stream.getAudioTracks().forEach(track => {
                decorateFakeTrack(track, 'audioinput', 'sbc-fake-audio-device', 'SBC Fake Microphone');
            });
            
            fakeStreamCache.audio = stream;
            stream._audioContext = audioContext;
            stream._sourceNode = source;
            
            log('info', 'Fake audio stream created.');
            return stream;
        } catch (err) {
            log('error', 'Failed to create fake audio stream:', err);
            return null;
        }
    }

    // --- Creates and caches a fake video stream using Canvas. ---
    async function getFakeVideoStream() {
        if (!spoofSettings.videoEnabled || !spoofSettings.videoBase64) {
            log('warn', 'Video spoofing disabled or no base64.');
            return null;
        }
        if (fakeStreamCache.video) {
            if (fakeStreamCache.video.getVideoTracks().some(t => t.readyState === 'live')) {
                log('info', 'Returning cached fake video stream.');
                return fakeStreamCache.video;
            } else {
                log('warn', 'Cached video stream dead, recreating...');
                cleanupFakeStreams();
            }
        }
        try {
            log('info', 'Creating fake video stream via Canvas...');
            const canvas = document.createElement('canvas');
            const ctx = canvas.getContext('2d');
            const blob = await base64ToBlob(spoofSettings.videoBase64, spoofSettings.videoMime);
            const url = URL.createObjectURL(blob);
            let stream;
            
            if (spoofSettings.videoMime.startsWith('image/')) {
                log('info', 'Using image for video spoof.');
                const img = new Image();
                await new Promise((resolve, reject) => {
                    img.onload = resolve;
                    img.onerror = reject;
                    img.src = url;
                });
                canvas.width = img.width || 640;
                canvas.height = img.height || 480;
                ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
                stream = canvas.captureStream(30);
                stream._canvas = canvas;
                stream._cleanup = () => URL.revokeObjectURL(url);
                if (spoofSettings.videoLoop) {
                    const draw = () => {
                        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
                        requestAnimationFrame(draw);
                    };
                    draw();
                }
            } else {
                log('info', 'Using video file for video spoof.');
                const video = document.createElement('video');
                video.src = url;
                video.loop = spoofSettings.videoLoop;
                video.muted = true;
                video.crossOrigin = 'anonymous';
                await new Promise(resolve => {
                    video.onloadedmetadata = () => {
                        canvas.width = video.videoWidth || 640;
                        canvas.height = video.videoHeight || 480;
                        resolve();
                    };
                    video.play().catch(e => log('warn', 'Video play error (muted):', e));
                });
                const draw = () => {
                    if (!video.paused && !video.ended) {
                        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
                    }
                    requestAnimationFrame(draw);
                };
                draw();
                stream = canvas.captureStream(30);
                stream._videoElement = video;
                stream._canvas = canvas;
                stream._cleanup = () => {
                    video.pause();
                    URL.revokeObjectURL(url);
                };
            }
            
            // Decorate all video tracks
            stream.getVideoTracks().forEach(track => {
                decorateFakeTrack(track, 'videoinput', 'sbc-fake-video-device', 'SBC Fake Camera');
            });
            
            fakeStreamCache.video = stream;
            log('info', 'Fake video stream created and cached successfully.');
            return stream;
        } catch (err) {
            log('error', 'Failed to create fake video stream:', err);
            return null;
        }
    }

    // --- Clean up cached fake streams ---
    function cleanupFakeStreams() {
        if (fakeStreamCache.audio) {
            fakeStreamCache.audio.getTracks().forEach(t => t.stop());
            if (fakeStreamCache.audio._audioContext) {
                fakeStreamCache.audio._audioContext.close();
            }
            fakeStreamCache.audio = null;
            log('info', 'Audio stream cache cleaned up.');
        }
        if (fakeStreamCache.video) {
            fakeStreamCache.video.getTracks().forEach(t => t.stop());
            if (fakeStreamCache.video._cleanup) {
                fakeStreamCache.video._cleanup();
            }
            fakeStreamCache.video = null;
            log('info', 'Video stream cache cleaned up.');
        }
    }

    // Helper to extract the exact deviceId from constraints
    function extractExactDeviceId(constraints) {
        if (typeof constraints !== 'object' || constraints === null) return null;
        
        // Handle deviceId as string (ideal constraint)
        if (typeof constraints.deviceId === 'string') {
            return constraints.deviceId;
        }
        // Handle exact constraint
        if (constraints.deviceId && typeof constraints.deviceId === 'object') {
            if (constraints.deviceId.exact) {
                return Array.isArray(constraints.deviceId.exact) ? constraints.deviceId.exact[0] : constraints.deviceId.exact;
            }
            if (constraints.deviceId.ideal) {
                return Array.isArray(constraints.deviceId.ideal) ? constraints.deviceId.ideal[0] : constraints.deviceId.ideal;
            }
        }
        // Handle advanced constraints array (older spec)
        if (Array.isArray(constraints.advanced)) {
            for (const adv of constraints.advanced) {
                if (adv.deviceId) {
                    if (typeof adv.deviceId === 'string') return adv.deviceId;
                    if (adv.deviceId.exact) return adv.deviceId.exact;
                }
            }
        }
        // Handle mandatory constraints (very old spec)
        if (constraints.mandatory && constraints.mandatory.deviceId) {
            return constraints.mandatory.deviceId;
        }
        return null;
    }

    // --- Media spoofing installation with EXACT deviceId matching ---
    function installMediaSpoof() {
        if (!navigator.mediaDevices) {
            log('error', 'navigator.mediaDevices is not available.');
            return;
        }
        if (!originalGetUserMedia) originalGetUserMedia = navigator.mediaDevices.getUserMedia;
        if (!originalEnumerateDevices) originalEnumerateDevices = navigator.mediaDevices.enumerateDevices;

        navigator.mediaDevices.getUserMedia = async function(constraints) {
            log('info', 'getUserMedia called with constraints:', JSON.stringify(constraints));
            const audioConstraints = constraints.audio;
            const videoConstraints = constraints.video;

            // --- Handle Audio Spoofing ---
            if (audioConstraints && spoofSettings.audioEnabled) {
                let requestedDeviceId = extractExactDeviceId(audioConstraints);
                log('info', 'Audio requested deviceId:', requestedDeviceId);
                if (requestedDeviceId === 'sbc-fake-audio-device') {
                    log('info', 'Exact match for fake audio device. Returning fake stream.');
                    const fakeAudioStream = await getFakeAudioStream();
                    if (fakeAudioStream) {
                        // If video is also requested, combine them
                        if (videoConstraints && spoofSettings.videoEnabled) {
                            const fakeVideoStream = await getFakeVideoStream();
                            if (fakeVideoStream) {
                                log('info', 'Returning combined fake audio/video stream.');
                                return new MediaStream([...fakeAudioStream.getTracks(), ...fakeVideoStream.getTracks()]);
                            }
                        }
                        log('info', 'Returning fake audio stream only.');
                        return fakeAudioStream;
                    }
                }
            }

            // --- Handle Video Spoofing ---
            if (videoConstraints && spoofSettings.videoEnabled) {
                let requestedDeviceId = extractExactDeviceId(videoConstraints);
                log('info', 'Video requested deviceId:', requestedDeviceId);
                if (requestedDeviceId === 'sbc-fake-video-device') {
                    log('info', 'Exact match for fake video device. Returning fake stream.');
                    const fakeVideoStream = await getFakeVideoStream();
                    if (fakeVideoStream) {
                        if (audioConstraints && spoofSettings.audioEnabled) {
                            const fakeAudioStream = await getFakeAudioStream();
                            if (fakeAudioStream) {
                                log('info', 'Returning combined fake audio/video stream.');
                                return new MediaStream([...fakeAudioStream.getTracks(), ...fakeVideoStream.getTracks()]);
                            }
                        }
                        log('info', 'Returning fake video stream only.');
                        return fakeVideoStream;
                    }
                }
            }

            // If we reach here, the request doesn't match our fake device, so fall back.
            log('info', 'Falling back to real getUserMedia.');
            try {
                const realStream = await originalGetUserMedia.call(navigator.mediaDevices, constraints);
                log('info', 'Real getUserMedia succeeded.');
                return realStream;
            } catch (error) {
                log('error', 'Real getUserMedia failed:', error);
                throw error;
            }
        };

        navigator.mediaDevices.enumerateDevices = async function() {
            log('info', 'enumerateDevices called.');
            const realDevices = await originalEnumerateDevices.call(navigator.mediaDevices);
            const fakeDevices = [];
            if (spoofSettings.audioEnabled && spoofSettings.audioBase64) {
                fakeDevices.push({
                    deviceId: 'sbc-fake-audio-device',
                    kind: 'audioinput',
                    label: 'SBC Fake Microphone',
                    groupId: 'sbc-fake-group'
                });
            }
            if (spoofSettings.videoEnabled && spoofSettings.videoBase64) {
                fakeDevices.push({
                    deviceId: 'sbc-fake-video-device',
                    kind: 'videoinput',
                    label: 'SBC Fake Camera',
                    groupId: 'sbc-fake-group'
                });
            }
            const allDevices = [...fakeDevices, ...realDevices];
            log('info', `Enumerated ${allDevices.length} devices (${fakeDevices.length} fake).`);
            return allDevices;
        };

        // Dispatch devicechange event so BBB knows the device list has changed
        navigator.mediaDevices.dispatchEvent(new Event('devicechange'));
        // Set the fake device as the default in localStorage for convenience
        forceFakeDeviceAsDefault();
        log('info', 'Media spoofing installed with exact deviceId matching.');
    }

    function resetMediaSpoof() {
        if (originalGetUserMedia) navigator.mediaDevices.getUserMedia = originalGetUserMedia;
        if (originalEnumerateDevices) navigator.mediaDevices.enumerateDevices = originalEnumerateDevices;
        cleanupFakeStreams();
        navigator.mediaDevices.dispatchEvent(new Event('devicechange'));
        log('info', 'Media spoofing reset.');
    }

    function updateSpoofSettings(newSettings) {
        const wasEnabled = spoofSettings.audioEnabled || spoofSettings.videoEnabled;
        spoofSettings = { ...spoofSettings, ...newSettings };
        log('info', 'Spoof settings updated:', spoofSettings);
        if (spoofSettings.audioEnabled || spoofSettings.videoEnabled) {
            installMediaSpoof();
        } else if (wasEnabled && !spoofSettings.audioEnabled && !spoofSettings.videoEnabled) {
            resetMediaSpoof();
        }
    }

    // Test function: plays a short preview without affecting cached stream
    async function testSpoofStream(type) {
        let testStream = null;
        if (type === 'audio' && spoofSettings.audioEnabled && spoofSettings.audioBase64) {
            try {
                testStream = await getFakeAudioStream();
                if (testStream) {
                    const audioOutput = new Audio();
                    audioOutput.srcObject = testStream;
                    audioOutput.play().catch(e => console.warn);
                    showToast('🔊 Playing fake audio (test) for 5 seconds', 'success');
                    setTimeout(() => {
                        audioOutput.pause();
                        testStream.getTracks().forEach(t => t.stop());
                    }, 5000);
                }
            } catch (e) {
                showToast('Failed to play test audio: ' + e.message, 'error');
            }
        } else if (type === 'video' && spoofSettings.videoEnabled && spoofSettings.videoBase64) {
            try {
                testStream = await getFakeVideoStream();
                if (testStream) {
                    const video = document.createElement('video');
                    video.style.position = 'fixed';
                    video.style.bottom = '10px';
                    video.style.right = '10px';
                    video.style.width = '200px';
                    video.style.zIndex = '1000000';
                    video.srcObject = testStream;
                    video.muted = true;
                    video.autoplay = true;
                    document.body.appendChild(video);
                    showToast('📹 Playing fake video (test) – close in 5s', 'success');
                    setTimeout(() => {
                        video.remove();
                        testStream.getTracks().forEach(t => t.stop());
                    }, 5000);
                }
            } catch (e) {
                showToast('Failed to play test video: ' + e.message, 'error');
            }
        } else {
            showToast('No fake stream available. Enable and select a file first.', 'error');
        }
    }

    function showDebugOverlay(text, isError = false) {
        if (!debugOverlay) {
            debugOverlay = document.createElement('div');
            debugOverlay.style.cssText = `
                position: fixed;
                top: 10px;
                right: 10px;
                z-index: 1000000;
                background: rgba(0,0,0,0.8);
                color: white;
                padding: 8px 16px;
                border-radius: 20px;
                font-size: 12px;
                font-family: monospace;
                pointer-events: none;
                transition: opacity 0.3s;
                max-width: 300px;
                word-break: break-word;
            `;
            document.body.appendChild(debugOverlay);
        }
        debugOverlay.style.background = isError ? 'rgba(200,0,0,0.8)' : 'rgba(0,0,0,0.8)';
        debugOverlay.textContent = text;
        debugOverlay.style.opacity = '1';
        setTimeout(() => {
            if (debugOverlay) debugOverlay.style.opacity = '0';
        }, 3000);
    }

    const OriginalWebSocket = window.WebSocket;

    window.WebSocket = function(...args) {
        const ws = new OriginalWebSocket(...args);
        const url = args[0];
        const protocol = args[1];

        if (protocol === GRAPHQL_WS_SUBPROTOCOL || url.includes('graphql')) {
            graphqlWs = ws;
            interceptWebSocket(ws, url);
            ws.addEventListener('open', () => addControlButtons(), { once: true });
        }
        return ws;
    };

    window.WebSocket.prototype = OriginalWebSocket.prototype;
    window.WebSocket.CONNECTING = OriginalWebSocket.CONNECTING;
    window.WebSocket.OPEN = OriginalWebSocket.OPEN;
    window.WebSocket.CLOSING = OriginalWebSocket.CLOSING;
    window.WebSocket.CLOSED = OriginalWebSocket.CLOSED;

    function interceptWebSocket(ws, url) {
        const originalSend = ws.send;

        ws.send = function(data) {
            let modifiedData = data;
            // Parse and potentially modify the GraphQL message
            if (typeof data === 'string') {
                try {
                    const parsed = JSON.parse(data);
                    // Check if it's a subscribe message for UserJoin
                    if (parsed && parsed.type === 'subscribe' && parsed.payload) {
                        const query = parsed.payload.query || '';
                        const operationName = parsed.payload.operationName || '';
                        // Match either operationName === "UserJoin" or query containing "userJoinMeeting"
                        if (operationName === 'UserJoin' || query.includes('userJoinMeeting')) {
                            // Force clientIsMobile to true
                            if (!parsed.payload.variables) parsed.payload.variables = {};
                            parsed.payload.variables.clientIsMobile = true;
                            log('info', '🔧 Forced clientIsMobile = true in UserJoin mutation');
                            modifiedData = JSON.stringify(parsed);
                        }
                    }
                } catch (e) {
                    // Not JSON, ignore
                }
            }
            const parsed = parseGraphQLMessage(modifiedData);
            log('info', '➡️ SEND:', parsed);
            
            if (parsed && parsed.type === 'subscribe' && parsed.payload && parsed.payload.query) {
                const operationType = extractOperationType(parsed.payload.query);
                const operationName = extractOperationName(parsed.payload.query);
                const variables = parsed.payload.variables || {};
                
                const opRecord = {
                    timestamp: new Date(),
                    type: operationType,
                    name: operationName,
                    query: parsed.payload.query,
                    variables: variables,
                    id: parsed.id
                };
                outgoingOperations.push(opRecord);
                if (outgoingOperations.length > 50) outgoingOperations.shift();
                
                if (operationName) {
                    const existing = discoveredMethods.get(operationName);
                    discoveredMethods.set(operationName, {
                        type: operationType,
                        query: parsed.payload.query,
                        variables: variables,
                        count: (existing?.count || 0) + 1,
                        lastSeen: new Date()
                    });
                }
            }
            originalSend.call(this, modifiedData);
        };

        ws.addEventListener('message', (event) => {
            const parsed = parseGraphQLMessage(event.data);
            log('info', '⬅️ RECEIVED:', parsed);
            messageHistory.push({ timestamp: new Date(), direction: 'received', data: parsed });
            if (messageHistory.length > MAX_HISTORY) messageHistory.shift();
            
            if (parsed && parsed.id) {
                operationResponses.set(parsed.id, {
                    timestamp: new Date(),
                    data: parsed
                });
                if (operationResponses.size > 100) {
                    const firstKey = operationResponses.keys().next().value;
                    operationResponses.delete(firstKey);
                }
            }
            
            // Process the incoming data for events and user info
            if (parsed && parsed.type === 'next' && parsed.payload?.data) {
                processIncomingData(parsed.payload.data);
            }
            
            if (!userInfoCaptured) scanForSelfUserInfo(parsed);
        });
    }

    // --- Robust Event Extraction & Caching ---
    function processIncomingData(data) {
        if (!data || typeof data !== 'object') return;

        const events = [];

        // 1. Handle patch operations (user join/leave)
        if (Array.isArray(data.patch)) {
            for (const op of data.patch) {
                if (op.op === 'add' && op.path && op.value) {
                    const user = op.value;
                    if (user.__typename === 'user' && user.userId) {
                        cachedUsers.set(user.userId, user);
                        if (!userInfoCaptured && user.name && user.userId) {
                            captureUserInfo(user);
                        }
                        events.push({
                            type: 'userJoinedMeeting',
                            data: user,
                            userId: user.userId,
                            userName: user.name
                        });
                    }
                }
            }
        }

        // 2. Handle user array
        if (Array.isArray(data.user)) {
            data.user.forEach(user => {
                if (user.userId) {
                    cachedUsers.set(user.userId, user);
                    if (!userInfoCaptured && user.name && user.userId) {
                        captureUserInfo(user);
                    }
                }
            });
        }

        // 3. Voice activity
        if (Array.isArray(data.user_voice_activity_stream)) {
            for (const voiceEvent of data.user_voice_activity_stream) {
                const userId = voiceEvent.userId;
                const userRef = voiceEvent.user;
                const leftVoiceConf = voiceEvent.leftVoiceConf;
                if (userId) {
                    const cached = cachedUsers.get(userId);
                    if (cached) {
                        cached.voice = cached.voice || {};
                        cached.voice.leftVoiceConf = leftVoiceConf;
                        cached.voice.voiceUserId = voiceEvent.voiceUserId;
                        cached.voice.muted = voiceEvent.muted;
                        cached.voice.talking = voiceEvent.talking;
                    }
                    const eventType = leftVoiceConf ? 'userVoiceDisconnected' : 'userVoiceConnected';
                    events.push({
                        type: eventType,
                        data: voiceEvent,
                        userId: userId,
                        userName: userRef?.name || cached?.name,
                        voiceUserId: voiceEvent.voiceUserId,
                        leftVoiceConf: leftVoiceConf
                    });
                }
            }
        }

        // 4. Deep traversal for chat messages etc.
        const queue = [data];
        while (queue.length) {
            const obj = queue.shift();
            if (!obj || typeof obj !== 'object') continue;
            
            // Single chat message added
            if (obj.chatMessageAdded || obj.chat_message_added) {
                const msg = obj.chatMessageAdded || obj.chat_message_added;
                const messageId = msg.id || msg.messageId || msg.chatMessageId;
                const text = msg.message || msg.text || msg.content || '';
                const userId = msg.userId || msg.senderId;
                const userName = msg.userName || msg.senderName;
                const chatId = msg.chatId || 'MAIN-PUBLIC-GROUP-CHAT';
                const replyToMessageId = msg.replyToMessageId || msg.replyTo || null;
                
                events.push({
                    type: 'chatMessageAdded',
                    data: msg,
                    messageId,
                    text,
                    userId,
                    userName,
                    chatId,
                    replyToMessageId
                });
                if (messageId) {
                    lastChatMessageId = messageId;
                    cachedMessages.set(messageId, { ...msg, messageId, text, userId, userName, chatId, replyToMessageId });
                }
                if (userId && userName) {
                    cachedUsers.set(userId, { userId, name: userName });
                }
            }
            
            // Public chat messages array
            if (Array.isArray(obj.chat_message_public) && obj.chat_message_public.length > 0) {
                obj.chat_message_public.forEach(msg => {
                    const messageId = msg.messageId || msg.id;
                    const text = msg.message || msg.text || msg.content || '';
                    const userObj = msg.user;
                    const userId = userObj ? userObj.userId : msg.userId || msg.senderId;
                    const userName = userObj ? userObj.name : msg.senderName || msg.userName;
                    const chatId = msg.chatId || 'MAIN-PUBLIC-GROUP-CHAT';
                    const replyToMessageId = msg.replyToMessageId || msg.replyTo || null;
                    if (messageId) {
                        cachedMessages.set(messageId, { ...msg, messageId, text, userId, userName, chatId, replyToMessageId });
                    }
                    if (userId) {
                        cachedUsers.set(userId, { userId, name: userName, ...userObj });
                    }
                });
                const lastMsg = obj.chat_message_public[obj.chat_message_public.length - 1];
                const messageId = lastMsg.messageId || lastMsg.id;
                const text = lastMsg.message || lastMsg.text || lastMsg.content || '';
                const userObj = lastMsg.user;
                const userId = userObj ? userObj.userId : lastMsg.userId || lastMsg.senderId;
                const userName = userObj ? userObj.name : lastMsg.senderName || lastMsg.userName;
                const chatId = lastMsg.chatId || 'MAIN-PUBLIC-GROUP-CHAT';
                const replyToMessageId = lastMsg.replyToMessageId || lastMsg.replyTo || null;
                events.push({
                    type: 'chatMessageAdded',
                    data: lastMsg,
                    messageId,
                    text,
                    userId,
                    userName,
                    chatId,
                    replyToMessageId
                });
                lastChatMessageId = messageId;
            }
            
            if (obj.userRaisedHand || obj.user_raised_hand) {
                const raised = obj.userRaisedHand || obj.user_raised_hand;
                if (raised.raiseHand === true || raised.handRaised === true) {
                    events.push({
                        type: 'userRaisedHand',
                        data: raised,
                        userId: raised.userId || raised.user_id
                    });
                }
            }
            
            if (Array.isArray(obj.chatMessages) && obj.chatMessages.length > 0) {
                obj.chatMessages.forEach(msg => {
                    const messageId = msg.id || msg.messageId;
                    const text = msg.message || msg.text || msg.content || '';
                    const userId = msg.userId || msg.senderId;
                    const userName = msg.userName || msg.senderName;
                    const chatId = msg.chatId || 'MAIN-PUBLIC-GROUP-CHAT';
                    const replyToMessageId = msg.replyToMessageId || msg.replyTo || null;
                    if (messageId) {
                        cachedMessages.set(messageId, { ...msg, messageId, text, userId, userName, chatId, replyToMessageId });
                    }
                    if (userId) cachedUsers.set(userId, { userId, name: userName });
                });
                const lastMsg = obj.chatMessages[obj.chatMessages.length - 1];
                const messageId = lastMsg.id || lastMsg.messageId;
                const text = lastMsg.message || lastMsg.text || lastMsg.content || '';
                const userId = lastMsg.userId || lastMsg.senderId;
                const userName = lastMsg.userName || lastMsg.senderName;
                const chatId = lastMsg.chatId || 'MAIN-PUBLIC-GROUP-CHAT';
                const replyToMessageId = lastMsg.replyToMessageId || lastMsg.replyTo || null;
                events.push({
                    type: 'chatMessageAdded',
                    data: lastMsg,
                    messageId,
                    text,
                    userId,
                    userName,
                    chatId,
                    replyToMessageId
                });
                lastChatMessageId = messageId;
            }
            
            if (Array.isArray(obj)) {
                queue.push(...obj);
            } else {
                for (const key in obj) {
                    if (obj.hasOwnProperty(key) && obj[key] && typeof obj[key] === 'object') {
                        queue.push(obj[key]);
                    }
                }
            }
        }
        
        for (const ev of events) {
            evaluateScriptsForEvent(ev);
        }
    }

    function evaluateScriptsForEvent(event) {
        if (!activeScripts.length) return;
        
        for (let script of activeScripts) {
            if (script.enabled === false) continue;
            if (script.runMode === 'once' && scriptTriggersFired.has(script.name)) continue;
            
            let conditionMet = false;
            let context = { event };
            
            const trigger = script.trigger;
            
            if (trigger.type === 'onChatMessage' && event.type === 'chatMessageAdded') {
                const params = trigger.params || {};
                let match = true;
                
                // Special handling for SELF senderId
                if (params.senderId === 'SELF') {
                    if (event.userId !== userInfo.userId) match = false;
                } else if (params.senderId && params.senderId !== event.userId) {
                    match = false;
                }
                if (match && params.senderName && event.userName) {
                    if (!event.userName.toLowerCase().includes(params.senderName.toLowerCase())) {
                        match = false;
                    }
                }
                if (match && params.text) {
                    // Exact match or includes?
                    if (params.text !== event.text) match = false;
                }
                if (match && params.chatId && params.chatId !== event.chatId) {
                    match = false;
                }
                
                if (match) {
                    conditionMet = true;
                    context.messageId = event.messageId;
                    context.messageText = event.text;
                    context.userId = event.userId;
                    context.userName = event.userName;
                    context.chatId = event.chatId;
                    context.replyToMessageId = event.replyToMessageId;
                }
            } else if (trigger.type === 'onUserJoin' && event.type === 'userJoinedMeeting') {
                conditionMet = true;
                context.userId = event.userId;
                context.userName = event.userName;
            } else if (trigger.type === 'onUserLeave' && event.type === 'userLeftMeeting') {
                conditionMet = true;
                context.userId = event.userId;
            } else if (trigger.type === 'onRaiseHand' && event.type === 'userRaisedHand') {
                conditionMet = true;
                context.userId = event.userId;
            } else if (trigger.type === 'onUserVoiceConnect' && event.type === 'userVoiceConnected') {
                conditionMet = true;
                context.userId = event.userId;
                context.userName = event.userName;
                context.voiceUserId = event.voiceUserId;
            } else if (trigger.type === 'onUserVoiceDisconnect' && event.type === 'userVoiceDisconnected') {
                conditionMet = true;
                context.userId = event.userId;
                context.userName = event.userName;
                context.voiceUserId = event.voiceUserId;
            }
            
            if (conditionMet) {
                logScriptEvent('info', script.name, `Triggered by ${event.type}`);
                showDebugOverlay(`🤖 Script "${script.name}" triggered`);
                showToast(`🤖 Script "${script.name}" triggered`, 'info');
                executeScriptAction(script, context);
                if (script.runMode === 'once') {
                    scriptTriggersFired.add(script.name);
                }
            }
        }
    }

    function parseGraphQLMessage(data) {
        if (typeof data !== 'string') return data;
        try { return JSON.parse(data); } catch (e) { return data; }
    }

    function extractOperationType(query) {
        const match = query.match(/^(mutation|query|subscription)/);
        return match ? match[1] : 'unknown';
    }

    function extractOperationName(query) {
        const match = query.match(/^(mutation|query|subscription)\s+(\w+)/);
        return match ? match[2] : null;
    }

    function scanForSelfUserInfo(message) {
        if (!message || typeof message !== 'object') return;
        function deepSearch(obj) {
            if (!obj || typeof obj !== 'object') return;
            if (obj.__typename === 'user_current') {
                captureUserInfo(obj);
                return;
            }
            if (Array.isArray(obj)) {
                obj.forEach(item => deepSearch(item));
            } else {
                Object.values(obj).forEach(v => { if (v && typeof v === 'object') deepSearch(v); });
            }
        }
        deepSearch(message);
    }

    function captureUserInfo(data) {
        if (!data || userInfoCaptured) return;
        if (data.name && data.userId) {
            userInfo = { name: data.name, userId: data.userId, role: data.role, authed: true, joined: true, rawData: data };
            userInfoCaptured = true;
            cachedUsers.set(data.userId, data);
            log('info', '✅ Captured user:', userInfo);
            updateInfoButtonText();
            window.sbcUserInfo = userInfo;
            showDebugOverlay(`👤 User captured: ${userInfo.name}`);
        }
    }

    function sendGraphQLMessage(message, source = 'custom') {
        if (!graphqlWs || graphqlWs.readyState !== WebSocket.OPEN) {
            return { success: false, error: 'WebSocket not open' };
        }
        try {
            graphqlWs.send(JSON.stringify(message));
            log('info', '✨ Operation sent:', message);
            
            const execRecord = {
                timestamp: new Date(),
                type: extractOperationType(message.payload.query),
                name: extractOperationName(message.payload.query) || 'unknown',
                query: message.payload.query,
                variables: message.payload.variables || {},
                id: message.id,
                source: source
            };
            executedOperations.push(execRecord);
            if (executedOperations.length > 50) executedOperations.shift();
            
            return { success: true, id: message.id };
        } catch (e) {
            log('error', 'Send error:', e);
            return { success: false, error: e.message };
        }
    }

    function showToast(message, type = 'info') {
        const toast = document.createElement('div');
        toast.textContent = message;
        toast.style.cssText = `
            position: fixed;
            bottom: 20px;
            right: 20px;
            z-index: 1000000;
            background: ${type === 'error' ? '#ef4444' : type === 'success' ? '#10b981' : '#3b82f6'};
            color: white;
            padding: 12px 20px;
            border-radius: 8px;
            font-size: 14px;
            font-family: sans-serif;
            box-shadow: 0 4px 12px rgba(0,0,0,0.2);
            max-width: 300px;
            pointer-events: none;
            transition: opacity 0.3s;
        `;
        document.body.appendChild(toast);
        setTimeout(() => {
            toast.style.opacity = '0';
            setTimeout(() => toast.remove(), 300);
        }, 3000);
        window.postMessage({ type: 'SBC_TOAST', payload: { message, type } }, '*');
    }

    function logScriptEvent(level, scriptName, message) {
        const logEntry = {
            timestamp: new Date().toISOString(),
            level,
            script: scriptName,
            message
        };
        window.postMessage({ type: 'SBC_SCRIPT_LOG', payload: logEntry }, '*');
        log(level, `[${scriptName}] ${message}`);
    }

    // --- Helper Functions ---
    function getUserNameById(userId) {
        const user = cachedUsers.get(userId);
        return user ? user.name || user.userName : null;
    }

    function getMessageById(messageId) {
        return cachedMessages.get(messageId) || null;
    }

    function getAllUsers() {
        return Array.from(cachedUsers.values());
    }

    function getRecentMessages(count = 20) {
        return Array.from(cachedMessages.values()).slice(-count);
    }

    // --- Template Resolution ---
    function resolveTemplate(template, context) {
        if (typeof template !== 'string') return template;
        return template.replace(/\{\{([^}]+)\}\}/g, (match, key) => {
            const trimmedKey = key.trim();
            if (context[trimmedKey] !== undefined) return context[trimmedKey];
            if (userInfo[trimmedKey] !== undefined) return userInfo[trimmedKey];
            if (trimmedKey === 'time') return new Date().toLocaleTimeString();
            if (trimmedKey === 'date') return new Date().toLocaleDateString();
            if (trimmedKey === 'meetingId') return userInfo.meetingId || '';
            return match;
        });
    }

    // --- Action Handlers ---
    async function executeScriptAction(script, context) {
        const action = script.action;
        const params = action.params || {};

        try {
            if (action.type === 'sendReaction') {
                let messageId = params.messageId;
                // Resolve template if it's a string like "{{replyToMessageId}}"
                if (typeof messageId === 'string' && messageId.startsWith('{{') && messageId.endsWith('}}')) {
                    messageId = resolveTemplate(messageId, context);
                }
                if (!messageId) {
                    messageId = context.messageId;
                }
                if (!messageId) {
                    logScriptEvent('error', script.name, 'No message ID for reaction');
                    return;
                }
                if (params.mode === 'select') {
                    const emojis = params.emojis || [];
                    logScriptEvent('info', script.name, `Sending ${emojis.length} selected reactions`);
                    for (let emoji of emojis) {
                        await sendReactionToMessageId(messageId, emoji);
                        await delay(200);
                    }
                    logScriptEvent('success', script.name, `Sent ${emojis.length} reactions`);
                    showToast(`✅ Sent ${emojis.length} reaction(s)`, 'success');
                } else if (params.mode === 'random') {
                    const count = Math.min(1000, Math.max(5, parseInt(params.count) || 10));
                    const emojis = getRandomEmojis(count);
                    logScriptEvent('info', script.name, `Sending ${count} random reactions`);
                    for (let emoji of emojis) {
                        await sendReactionToMessageId(messageId, emoji);
                        await delay(100);
                    }
                    logScriptEvent('success', script.name, `Sent ${count} random reactions`);
                    showToast(`✅ Sent ${count} random reactions`, 'success');
                }
            } else if (action.type === 'mutation') {
                const mutationName = params.mutationName;
                const variables = { ...params.variables };
                for (const key in variables) {
                    const val = variables[key];
                    if (val === 'AUTO_CAPTURE_ID' || val === 'USER_ID') {
                        variables[key] = context.userId || userInfo.userId;
                    } else if (val === 'TARGET_ID' && context.userId) {
                        variables[key] = context.userId;
                    } else if (val === 'MESSAGE_ID' && context.messageId) {
                        variables[key] = context.messageId;
                    }
                }
                const mutationDef = actionsMutations.find(m => m.name === mutationName);
                let query;
                if (mutationDef) {
                    query = mutationDef.query;
                } else {
                    const learned = findLearnedOperation([mutationName]);
                    if (learned) {
                        query = learned.query;
                    } else {
                        logScriptEvent('error', script.name, `Mutation "${mutationName}" not found`);
                        return;
                    }
                }
                const operation = {
                    type: "subscribe",
                    id: String(Date.now()),
                    payload: { query, variables }
                };
                logScriptEvent('info', script.name, `Executing mutation "${mutationName}"`);
                const result = sendGraphQLMessage(operation, 'script');
                if (result.success) {
                    logScriptEvent('success', script.name, `Mutation "${mutationName}" sent`);
                    showToast(`✅ Mutation "${mutationName}" sent`, 'success');
                } else {
                    logScriptEvent('error', script.name, `Mutation failed: ${result.error}`);
                    showToast(`❌ Mutation failed: ${result.error}`, 'error');
                }
            } else if (action.type === 'sendDirectMessage') {
                const userId = resolvePlaceholder(params.userId, context);
                const message = resolveTemplate(params.message || 'Hello!', context);
                if (!userId) return;
                const result = await sendDirectMessage(userId, message);
                if (result.success) {
                    logScriptEvent('success', script.name, `DM sent to ${userId}`);
                    showToast(`✅ DM sent`, 'success');
                } else {
                    logScriptEvent('error', script.name, `DM failed: ${result.error}`);
                    showToast(`❌ DM failed: ${result.error}`, 'error');
                }
            } else if (action.type === 'sendChatMessage') {
                const chatId = resolvePlaceholder(params.chatId, context) || context.chatId || 'MAIN-PUBLIC-GROUP-CHAT';
                const message = resolveTemplate(params.message || '', context);
                if (!message) return;
                const result = await sendChatMessage(chatId, message);
                if (result.success) {
                    logScriptEvent('success', script.name, `Message sent to ${chatId}`);
                    showToast(`✅ Message sent`, 'success');
                } else {
                    logScriptEvent('error', script.name, `Message send failed: ${result.error}`);
                    showToast(`❌ Message failed: ${result.error}`, 'error');
                }
            } else if (action.type === 'startClock') {
                let messageId = params.messageId;
                if (typeof messageId === 'string' && messageId.startsWith('{{') && messageId.endsWith('}}')) {
                    messageId = resolveTemplate(messageId, context);
                }
                if (!messageId) messageId = context.messageId;
                if (!messageId) {
                    logScriptEvent('error', script.name, 'No message ID for clock');
                    return;
                }
                // Stop existing clock for this message
                stopClockForMessage(messageId);
                const chatId = context.chatId || (cachedMessages.get(messageId)?.chatId) || 'MAIN-PUBLIC-GROUP-CHAT';
                const format = params.format || '⏰ {{time}}';
                const intervalId = setInterval(() => {
                    const currentTime = new Date().toLocaleTimeString();
                    const newText = format.replace(/\{\{time\}\}/g, currentTime);
                    editChatMessage(chatId, messageId, newText);
                }, 1000);
                activeClocks.set(messageId, { intervalId, chatId, format });
                logScriptEvent('success', script.name, `Clock started on message ${messageId}`);
                showToast(`⏰ Clock started`, 'success');
            } else if (action.type === 'stopClock') {
                let messageId = params.messageId;
                if (typeof messageId === 'string' && messageId.startsWith('{{') && messageId.endsWith('}}')) {
                    messageId = resolveTemplate(messageId, context);
                }
                // If no specific messageId, stop all clocks? For simplicity, stop the one from context if any.
                if (!messageId) messageId = context.messageId;
                if (messageId) {
                    stopClockForMessage(messageId);
                    if (params.finalMessage) {
                        const chatId = context.chatId || (cachedMessages.get(messageId)?.chatId) || 'MAIN-PUBLIC-GROUP-CHAT';
                        editChatMessage(chatId, messageId, params.finalMessage);
                    }
                    logScriptEvent('success', script.name, `Clock stopped on message ${messageId}`);
                    showToast(`⏰ Clock stopped`, 'success');
                } else {
                    // Stop all clocks?
                    for (const [mid, clock] of activeClocks.entries()) {
                        clearInterval(clock.intervalId);
                        activeClocks.delete(mid);
                    }
                    logScriptEvent('success', script.name, `All clocks stopped`);
                    showToast(`⏰ All clocks stopped`, 'success');
                }
            } else if (action.type === 'getMessageInfo') {
                const messageId = resolvePlaceholder(params.messageId, context) || context.messageId;
                const msg = getMessageById(messageId);
                if (msg) {
                    logScriptEvent('info', script.name, `Message info: from ${msg.userName}, text: "${msg.text}"`);
                    showToast(`📨 From ${msg.userName}: ${msg.text.substring(0,30)}...`, 'info');
                } else {
                    logScriptEvent('error', script.name, `Message ${messageId} not found`);
                }
            } else if (action.type === 'getUserInfo') {
                const userId = resolvePlaceholder(params.userId, context) || context.userId;
                const user = cachedUsers.get(userId);
                if (user) {
                    logScriptEvent('info', script.name, `User info: ${user.name} (${user.role || 'unknown'})`);
                    showToast(`👤 ${user.name} (${user.role || 'user'})`, 'info');
                } else {
                    logScriptEvent('error', script.name, `User ${userId} not found`);
                }
            } else if (action.type === 'listUsers') {
                const users = getAllUsers();
                const names = users.map(u => u.name || u.userName).join(', ');
                logScriptEvent('info', script.name, `Users in meeting: ${names}`);
                showToast(`👥 ${users.length} users: ${names.substring(0,50)}...`, 'info');
            } else if (action.type === 'getChatHistory') {
                const count = params.count || 10;
                const messages = getRecentMessages(count);
                const summary = messages.map(m => `${m.userName}: ${m.text}`).join(' | ');
                logScriptEvent('info', script.name, `Recent chat: ${summary}`);
                showToast(`💬 Last ${messages.length} messages`, 'info');
            } else {
                logScriptEvent('error', script.name, `Unknown action type: ${action.type}`);
            }
        } catch (err) {
            logScriptEvent('error', script.name, `Action error: ${err.message}`);
        }
    }

    function stopClockForMessage(messageId) {
        const clock = activeClocks.get(messageId);
        if (clock) {
            clearInterval(clock.intervalId);
            activeClocks.delete(messageId);
        }
    }

    function resolvePlaceholder(value, context) {
        if (typeof value !== 'string') return value;
        if (value === 'AUTO_CAPTURE_ID' || value === 'USER_ID') return context.userId || userInfo.userId;
        if (value === 'TARGET_ID') return context.userId;
        if (value === 'MESSAGE_ID') return context.messageId;
        if (value === 'SELF_ID') return userInfo.userId;
        return value;
    }

    async function sendDirectMessage(userId, text) {
        const learnedCreate = findLearnedOperation(['chatCreateWithUser', 'createChat']);
        let chatId = null;
        if (learnedCreate) {
            const op = {
                type: "subscribe",
                id: String(Date.now()),
                payload: { query: learnedCreate.query, variables: { userId } }
            };
            sendGraphQLMessage(op, 'script');
            chatId = 'public';
        }
        const learnedSend = findLearnedOperation(['chatSendMessage']);
        let query, variables;
        if (learnedSend) {
            query = learnedSend.query;
            variables = { ...learnedSend.variables, chatId: chatId || 'public', chatMessageInMarkdownFormat: text };
        } else {
            query = `mutation chatSendMessage($chatId: String!, $chatMessageInMarkdownFormat: String!) { chatSendMessage(chatId: $chatId, chatMessageInMarkdownFormat: $chatMessageInMarkdownFormat) { __typename } }`;
            variables = { chatId: chatId || 'public', chatMessageInMarkdownFormat: text };
        }
        const op = { type: "subscribe", id: String(Date.now()), payload: { query, variables } };
        return sendGraphQLMessage(op, 'script_dm');
    }

    async function sendChatMessage(chatId, text) {
        const learnedSend = findLearnedOperation(['chatSendMessage']);
        let query, variables;
        if (learnedSend) {
            query = learnedSend.query;
            variables = { ...learnedSend.variables, chatId: chatId, chatMessageInMarkdownFormat: text };
        } else {
            query = `mutation chatSendMessage($chatId: String!, $chatMessageInMarkdownFormat: String!) { chatSendMessage(chatId: $chatId, chatMessageInMarkdownFormat: $chatMessageInMarkdownFormat) { __typename } }`;
            variables = { chatId: chatId, chatMessageInMarkdownFormat: text };
        }
        const op = { type: "subscribe", id: String(Date.now()), payload: { query, variables } };
        return sendGraphQLMessage(op, 'script_chat');
    }

    async function editChatMessage(chatId, messageId, newText) {
        const learnedEdit = findLearnedOperation(['chatEditMessage']);
        let query, variables;
        if (learnedEdit) {
            query = learnedEdit.query;
            variables = { ...learnedEdit.variables, chatId, messageId, chatMessageInMarkdownFormat: newText };
        } else {
            query = `mutation chatEditMessage($chatId: String!, $messageId: String!, $chatMessageInMarkdownFormat: String!) { chatEditMessage(chatId: $chatId, messageId: $messageId, chatMessageInMarkdownFormat: $chatMessageInMarkdownFormat) { __typename } }`;
            variables = { chatId, messageId, chatMessageInMarkdownFormat: newText };
        }
        const op = { type: "subscribe", id: String(Date.now()), payload: { query, variables } };
        return sendGraphQLMessage(op, 'script_edit');
    }

    function sendReactionToMessageId(messageId, emoji) {
        const msg = cachedMessages.get(messageId);
        if (!msg) {
            log('error', 'Message not found in cache');
            return { success: false, error: 'Message not found in cache' };
        }
        const chatId = msg.chatId || 'MAIN-PUBLIC-GROUP-CHAT';
        const mutation = {
            type: "subscribe",
            id: String(Date.now()),
            payload: {
                query: `mutation chatSendMessageReaction($chatId: String!, $messageId: String!, $reactionEmoji: String!) {
                    chatSendMessageReaction(chatId: $chatId, messageId: $messageId, reactionEmoji: $reactionEmoji) {
                        __typename
                    }
                }`,
                variables: { chatId, messageId, reactionEmoji: emoji }
            }
        };
        return sendGraphQLMessage(mutation, 'script_reaction');
    }

    function getRandomEmojis(count) {
        const commonEmojis = [
            "🐙", "💎", "🥮", "🎲", "🪶", "🐡", "🧸", "🎈", "📚", "🔮", "🧨", "🎉", "🪅", "🎊", "🎁", "🔔", "📯", "🕯️", "⚰️", "⚱️", "🧿", "🔮", "🪞", "🪟", "🧲", "🧪", "🧫", "🧬", "🔬", "🔭", "📡", "💊", "🩺", "🩹", "🩻", "⚕️", "🌀", "🌁", "🌂", "☂️", "☔", "⛱️", "⚡", "❄️", "☃️", "⛄", "🔥", "💧", "🌊", "⭐", "🌟", "✨", "⚡", "☄️", "💥", "💫", "💢", "🕳️", "💣", "💬", "🗯️", "💭", "💤", "🕛", "🕧", "🕐", "🕜", "🕑", "🕝", "🕒", "🕞", "🕓", "🕟", "🕔", "🕠", "🕕", "🕡", "🕖", "🕢", "🕗", "🕣", "🕘", "🕤", "🕙", "🕥", "🕚", "🕦", "🧭", "🧱", "🪢", "🧶", "🧵", "🪡", "🪤", "🔪", "🗡️", "⚔️", "🛡️", "🚬", "⚰️", "🪦", "🧨", "💣", "🏹", "🪃", "🪄", "🧹", "🧺", "🧻", "🚽", "🚿", "🛁", "🧼", "🪥", "🧽", "🧴", "🪒", "💈", "🪞", "🪟", "🪑", "🚪", "🛏️", "🛋️", "🪆", "🧸", "🖼️", "🎨", "🧵", "🪡", "🧶", "🎭", "🩰", "🎤", "🎧", "🎷", "🎺", "🎸", "🪕", "🎻", "🥁", "🪘", "🎮", "🕹️", "🧩", "🎲", "♟️", "🎯", "🎳", "🎱", "🀄", "🃏", "🎴", "🎭", "🪄", "🐭", "🐹", "🐰", "🦊", "🐻", "🐼", "🐨", "🐯", "🦁", "🐮", "🐷", "🐽", "🐸", "🐵", "🙈", "🙉", "🙊", "🐒", "🐔", "🐧", "🐦", "🐤", "🐣", "🐥", "🦆", "🦅", "🦉", "🐺", "🐗", "🐴", "🦄", "🐝", "🐛", "🦋", "🐌", "🐞", "🐜", "🕷️", "🦂", "🦟", "🦠", "🐢", "🐍", "🦎", "🐊", "🐉", "🐲", "🐳", "🐋", "🐬", "🐟", "🐠", "🐡", "🦈", "🐙", "🦑", "🐚", "🪸", "🐌", "🦀", "🦞", "🦐", "🦑", "🐧", "🐦‍⬛", "🕊️", "🦜", "🦚", "🦩", "🦢", "🦃", "🐓", "🦤", "🐪", "🐫", "🦙", "🦒", "🐘", "🦣", "🦏", "🦛", "🐃", "🐂", "🐄", "🐎", "🐖", "🐏", "🐑", "🦌", "🐐", "🦔", "🐿️", "🦫", "🦨", "🦡", "🦦", "🦥", "🐁", "🐀", "🐇", "🦔", "🍏", "🍎", "🍐", "🍊", "🍋", "🍌", "🍉", "🍇", "🍓", "🫐", "🍒", "🍑", "🥭", "🍍", "🥥", "🥝", "🍅", "🍆", "🥑", "🥦", "🥬", "🥒", "🌶️", "🫑", "🌽", "🥕", "🫒", "🧄", "🧅", "🥔", "🍠", "🥐", "🥯", "🍞", "🥖", "🥨", "🧀", "🥚", "🍳", "🧈", "🥞", "🧇", "🥓", "🥩", "🍗", "🍖", "🦴", "🌭", "🍔", "🍟", "🍕", "🫓", "🥪", "🥙", "🧆", "🌮", "🌯", "🫔", "🥗", "🥘", "🫕", "🥫", "🍝", "🍜", "🍲", "🍛", "🍣", "🍱", "🥟", "🦪", "🍤", "🍙", "🍚", "🍘", "🍥", "🥠", "🥡", "🍦", "🍧", "🍨", "🍩", "🍪", "🎂", "🍰", "🧁", "🥧", "🍫", "🍬", "🍭", "🍮", "🍯", "🍼", "🥛", "☕", "🫖", "🍵", "🧃", "🥤", "🧋", "🍶", "🍺", "🍻", "🥂", "🍷", "🥃", "🍸", "🍹", "🧉", "🍾", "🧊", "🥢", "🍽️", "🍴", "🥄", "🔪", "🏺", "🎃", "🎄", "🎆", "🎇", "🧨", "✨", "🎈", "🎉", "🎊", "🎋", "🎍", "🎎", "🎏", "🎐", "🎑", "🧧", "🪔", "🎀", "🎁", "🎗️", "🎟️", "🎫", "🎖️", "🏆", "🏅", "🥇", "🥈", "🥉", "⚽", "⚾", "🥎", "🏀", "🏐", "🏈", "🏉", "🎾", "🥏", "🎱", "🪀", "🏓", "🏸", "🏒", "🏑", "🥍", "🏏", "🥅", "⛳", "🪃", "🪁", "🔫", "🏹", "🎣", "🤿", "🥊", "🥋", "🎽", "🛹", "🛼", "🛷", "⛸️", "🥌", "🎿", "⛷️", "🏂", "🪂", "🏋️", "🤼", "🤸", "⛹️", "🤾", "🏌️", "🏇", "🧘", "🏄", "🏊", "🤽", "🚣", "🧗", "🚵", "🚴", "🏆", "🥇", "🥈", "🥉", "🏅", "🎖️", "🏵️", "🎗️", "🎫", "🎟️", "🎪", "🤹", "🎭", "🩰", "🎨", "🎬", "🎤", "🎧", "🎷", "🎺", "🎸", "🪕", "🎻", "🥁", "🪘", "🎹", "🎛️", "🎙️", "📻", "🎚️", "🎚️", "📀", "💿", "📀", "🖥️", "💻", "🖨️", "⌨️", "🖱️", "🖲️", "📷", "📸", "📹", "🎥", "📽️", "🎞️", "📞", "☎️", "📟", "📠", "📺", "📻", "🎙️", "🎚️", "⏯️", "⏮️", "⏭️", "⏩", "⏪", "🔄", "⏸️", "⏹️", "⏺️", "🎦", "🔇", "🔈", "🔉", "🔊", "📢", "📣", "📯", "🔔", "🔕", "🎵", "🎶", "🎼", "🎤", "🎧", "📻", "🎷", "🎺", "🎸", "🪕", "🎻", "🥁", "🪘", "🎹", "🎛️", "🕹️", "🧩", "🎲", "♟️", "🎯", "🎳", "🎱", "🀄", "🃏", "🎴", "🎭", "🪄", "🪅", "🪆", "🪩", "🪞", "🪟", "🪑", "🚪", "🛏️", "🛋️", "🚽", "🚿", "🛁", "🪥", "🧴", "🧼", "🧽", "🧹", "🧺", "🧻", "🪒", "💈", "🪞", "🪟", "🪑", "🚪", "🛏️", "🛋️", "🪆", "🧸", "🖼️", "🎨", "🧵", "🪡", "🧶", "🧩", "🪢", "🧿", "🔮", "🪄", "🕯️", "💡", "🔦", "🏮", "🪔", "📔", "📕", "📖", "📗", "📘", "📙", "📚", "📓", "📒", "📃", "📜", "📄", "📰", "🗞️", "📑", "🔖", "🏷️", "💰", "💴", "💵", "💶", "💷", "💸", "💳", "🧾", "💎", "⚖️", "🔧", "🔨", "⚒️", "🛠️", "⛏️", "🔩", "⚙️", "🧰", "🧲", "🔪", "🗡️", "⚔️", "🛡️", "🚬", "⚰️", "🪦", "⚱️", "🧿", "🔮", "📿", "💈", "🪞", "🪟", "🪑", "🚪", "🛏️", "🛋️", "🚽", "🚿", "🛁", "🧼", "🧽", "🧹", "🧺", "🧻", "🪒", "💈", "🪞", "🪟", "🪑", "🚪", "🛏️", "🛋️", "🪆", "🧸", "🖼️", "🎨", "🧵", "🪡", "🧶", "🧩", "🪢", "🧿", "🔮", "🪄", "🕯️", "💡", "🔦", "🏮", "🪔", "📔", "📕", "📖", "📗", "📘", "📙", "📚", "📓", "📒", "📃", "📜", "📄", "📰", "🗞️", "📑", "🔖", "🏷️", "💰", "💴", "💵", "💶", "💷", "💸", "💳", "🧾", "💎", "⚖️", "🔧", "🔨", "⚒️", "🛠️", "⛏️", "🔩", "⚙️", "🧰", "🧲", "🔪", "🗡️", "⚔️", "🛡️", "🚬", "⚰️", "🪦", "⚱️", "📿", "💈", "🪞", "🪟", "🪑", "🚪", "🛏️", "🛋️", "🚽", "🚿", "🛁", "🧼", "🧽", "🧹", "🧺", "🧻", "🪒"
        ];
        const emojis = [];
        for (let i = 0; i < count; i++) {
            emojis.push(commonEmojis[Math.floor(Math.random() * commonEmojis.length)]);
        }
        return emojis;
    }

    function delay(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    function findLearnedOperation(patterns) {
        if (!Array.isArray(patterns)) patterns = [patterns];
        for (const pattern of patterns) {
            const found = outgoingOperations.find(m => m.query.includes(pattern));
            if (found) return found;
        }
        return null;
    }

    // Quick actions
    function toggleRaiseHand(raise) {
        const learned = findLearnedOperation(['userSetRaiseHand', 'setRaiseHand', 'raiseHand']);
        let mutation;
        if (learned) {
            mutation = {
                type: "subscribe",
                id: String(Date.now()),
                payload: { query: learned.query, variables: { ...learned.variables, raiseHand: raise } }
            };
        } else {
            mutation = {
                type: "subscribe",
                id: String(Date.now()),
                payload: {
                    query: `mutation userSetRaiseHand($raiseHand: Boolean!) { userSetRaiseHand(raiseHand: $raiseHand) { __typename } }`,
                    variables: { raiseHand: raise }
                }
            };
        }
        return sendGraphQLMessage(mutation, 'action:raiseHand');
    }

    function setMobile(isMobile) {
        const learned = findLearnedOperation(['userSetMobile', 'setMobile', 'mobile']);
        let mutation;
        if (learned) {
            mutation = {
                type: "subscribe",
                id: String(Date.now()),
                payload: { query: learned.query, variables: { ...learned.variables, mobile: isMobile } }
            };
        } else {
            mutation = {
                type: "subscribe",
                id: String(Date.now()),
                payload: {
                    query: `mutation userSetMobile($mobile: Boolean!) { userSetMobile(mobile: $mobile) { __typename } }`,
                    variables: { mobile: isMobile }
                }
            };
        }
        return sendGraphQLMessage(mutation, 'action:setMobile');
    }

    function setAway(away) {
        const learned = findLearnedOperation(['userSetAway', 'setAway', 'away']);
        let mutation;
        if (learned) {
            mutation = {
                type: "subscribe",
                id: String(Date.now()),
                payload: { query: learned.query, variables: { ...learned.variables, away: away } }
            };
        } else {
            mutation = {
                type: "subscribe",
                id: String(Date.now()),
                payload: {
                    query: `mutation userSetAway($away: Boolean!) { userSetAway(away: $away) { __typename } }`,
                    variables: { away: away }
                }
            };
        }
        return sendGraphQLMessage(mutation, 'action:setAway');
    }

    function sendReactionToLastMessage(emoji) {
        if (!lastChatMessageId) {
            return { success: false, error: 'No chat message ID available' };
        }
        return sendReactionToMessageId(lastChatMessageId, emoji);
    }

    function addControlButtons() {
        if (document.getElementById('sbc-button-container')) return;
        const container = document.createElement('div');
        container.id = 'sbc-button-container';
        container.style.cssText = 'position:fixed;bottom:20px;left:20px;z-index:999999;display:flex;gap:10px;';
        const infoBtn = createButton('ℹ️ Info', '#667eea', () => {
            alert(`👤 ${userInfo.name}\n🆔 ${userInfo.userId}\n🎭 ${userInfo.role}`);
        });
        container.appendChild(infoBtn);
        document.body.appendChild(container);
    }

    function createButton(text, color, onClick) {
        const btn = document.createElement('button');
        btn.innerHTML = text;
        btn.style.cssText = `padding:10px 16px;background:${color};color:white;border:none;border-radius:30px;font-size:14px;font-weight:bold;box-shadow:0 4px 15px rgba(0,0,0,0.2);cursor:pointer;`;
        btn.onclick = onClick;
        return btn;
    }

    function updateInfoButtonText() {
        const btn = document.getElementById('sbc-info-button');
        if (btn && userInfo.name) btn.innerHTML = `ℹ️ ${userInfo.name.split(' ')[0]}`;
    }

    // --- Time-based trigger system ---
    function startTimeBasedTriggers() {
        if (timeCheckInterval) clearInterval(timeCheckInterval);
        const hasTimeTriggers = activeScripts.some(s => s.trigger.type === 'onTime' && s.enabled !== false);
        if (!hasTimeTriggers) return;

        timeCheckInterval = setInterval(() => {
            const now = new Date();
            const hours = now.getHours().toString().padStart(2, '0');
            const minutes = now.getMinutes().toString().padStart(2, '0');
            const seconds = now.getSeconds();
            const currentTime = `${hours}:${minutes}`;

            for (let script of activeScripts) {
                if (script.enabled === false) continue;
                if (script.trigger.type !== 'onTime') continue;
                const pattern = script.trigger.params?.timePattern;
                if (!pattern) continue;

                const times = pattern.split(',').map(t => t.trim());
                if (times.includes(currentTime) && seconds === 0) {
                    if (script.runMode === 'once' && scriptTriggersFired.has(script.name)) continue;

                    logScriptEvent('info', script.name, `Triggered by onTime at ${currentTime}`);
                    showToast(`🕐 Script "${script.name}" triggered at ${currentTime}`, 'info');

                    const context = { time: currentTime };
                    executeScriptAction(script, context);
                    if (script.runMode === 'once') {
                        scriptTriggersFired.add(script.name);
                    }
                }
            }
        }, 1000);
    }

    window.addEventListener('message', (event) => {
        if (event.source !== window) return;
        const { type, payload, requestId } = event.data;
        
        if (type === 'SBC_GET_DATA') {
            const methodsArray = Array.from(discoveredMethods.entries()).map(([name, data]) => ({
                name,
                type: data.type,
                query: data.query,
                variables: data.variables,
                count: data.count,
                lastSeen: data.lastSeen
            }));
            window.postMessage({
                type: 'SBC_DATA_RESPONSE',
                payload: {
                    userInfo,
                    messageHistory: messageHistory.slice(-30),
                    wsReadyState: graphqlWs ? graphqlWs.readyState : null,
                    hasConnection: !!graphqlWs,
                    lastChatMessageId,
                    discoveredMethods: methodsArray,
                    executedOperations: executedOperations.slice(-30),
                    operationResponses: Array.from(operationResponses.entries()).map(([id, resp]) => ({
                        id,
                        timestamp: resp.timestamp,
                        data: resp.data
                    })),
                    users: getAllUsers(),
                    recentMessages: getRecentMessages(20)
                }
            }, '*');
        }
        
        if (type === 'SBC_SEND_OPERATION') {
            const result = sendGraphQLMessage(payload.operation, 'custom');
            window.postMessage({
                type: 'SBC_SEND_RESULT',
                payload: result,
                requestId: requestId
            }, '*');
            if (!result.success) {
                showToast(`❌ Send failed: ${result.error}`, 'error');
            } else {
                showToast(`✅ Operation sent`, 'success');
            }
        }
        
        if (type === 'SBC_ACTION') {
            let result = { success: false, error: 'Unknown action' };
            switch (payload.action) {
                case 'raiseHand': result = toggleRaiseHand(payload.value); break;
                case 'setMobile': result = setMobile(payload.value); break;
                case 'sendReaction': result = sendReactionToLastMessage(payload.emoji); break;
                case 'setAway': result = setAway(payload.value); break;
                case 'sendDirectMessage': result = sendDirectMessage(payload.userId, payload.message); break;
            }
            window.postMessage({
                type: 'SBC_ACTION_RESULT',
                payload: result,
                requestId: requestId
            }, '*');
            if (result.success) {
                showToast(`✅ Action ${payload.action} succeeded`, 'success');
            } else {
                showToast(`❌ Action ${payload.action} failed: ${result.error}`, 'error');
            }
        }
        
        if (type === 'SBC_UPDATE_SCRIPTS') {
            activeScripts = payload.scripts || [];
            actionsMutations = payload.actionsMutations || [];
            scriptTriggersFired.clear();
            startTimeBasedTriggers();
            log('info', 'Scripts updated:', activeScripts.length);
            showDebugOverlay(`📜 Loaded ${activeScripts.length} script(s)`);
            showToast(`🤖 Loaded ${activeScripts.length} automation script(s)`, 'info');
        }

        // --- Spoofing message handlers ---
        if (type === 'SBC_UPDATE_SPOOF') {
            updateSpoofSettings(payload);
        }
        if (type === 'SBC_TEST_SPOOF_STREAM') {
            testSpoofStream(payload.type);
        }
    });

    // Optional: request initial spoof settings from popup (popup will send them when available)
    window.postMessage({ type: 'SBC_GET_SPOOF_SETTINGS' }, '*');

    log('info', 'SBC Inject script loaded. Waiting for spoof settings.');
})();