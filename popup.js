// popup.js
const connIndicator = document.getElementById('connection-indicator');
const userEls = {
    name: document.getElementById('user-name'),
    id: document.getElementById('user-id'),
    role: document.getElementById('user-role'),
    wsState: document.getElementById('ws-state')
};
const meetingUrlEl = document.getElementById('meeting-url');
const messageList = document.getElementById('message-list');
const mutationInput = document.getElementById('mutation-input');
const sendStatus = document.getElementById('send-status');
const actionStatus = document.getElementById('action-status');
const lastMsgIdDisplay = document.getElementById('last-msg-id-display');
const methodsList = document.getElementById('methods-list');
const executedList = document.getElementById('executed-list');
const responsesList = document.getElementById('responses-list');
const variablesEditor = document.getElementById('variables-editor');
const actionsListDiv = document.getElementById('actions-list');
const scriptsPreview = document.getElementById('scripts-preview');
const automationStatus = document.getElementById('automation-status');
const logContainer = document.getElementById('log-container');

let currentData = {};
let pinnedMethods = new Set();
let automationScripts = [];
let actionsMutations = [];
let currentTabUrl = '';

// Spoof state
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

// Tabs definition
const tabs = {
    actions: { btn: 'tab-actions', panel: 'panel-actions' },
    methods: { btn: 'tab-methods', panel: 'panel-methods' },
    actionsfile: { btn: 'tab-actionsfile', panel: 'panel-actionsfile' },
    automation: { btn: 'tab-automation', panel: 'panel-automation' },
    spoof: { btn: 'tab-spoof', panel: 'panel-spoof' },
    executed: { btn: 'tab-executed', panel: 'panel-executed' },
    responses: { btn: 'tab-responses', panel: 'panel-responses' },
    info: { btn: 'tab-info', panel: 'panel-info' },
    send: { btn: 'tab-send', panel: 'panel-send' }
};

Object.entries(tabs).forEach(([name, ids]) => {
    document.getElementById(ids.btn).addEventListener('click', () => setActiveTab(name));
});

function setActiveTab(name) {
    Object.values(tabs).forEach(t => {
        document.getElementById(t.btn).classList.remove('active', 'bg-blue-500', 'text-white');
        document.getElementById(t.panel).classList.add('hidden');
    });
    document.getElementById(tabs[name].btn).classList.add('active', 'bg-blue-500', 'text-white');
    document.getElementById(tabs[name].panel).classList.remove('hidden');
}

// --- Load actions.json ---
async function loadActionsFile() {
    try {
        const url = chrome.runtime.getURL('actions.json');
        const response = await fetch(url);
        const json = await response.json();
        actionsMutations = json.mutations.map(mut => {
            const args = mut.arguments || [];
            const query = buildMutationQuery(mut.name, args);
            return { name: mut.name, args, query };
        });
        renderActionsFileList();
        console.log('Loaded mutations from actions.json:', actionsMutations.length);
        pushScriptsToPage();
    } catch (e) {
        console.error('Failed to load actions.json', e);
        actionsListDiv.innerHTML = '<div class="text-red-500">Failed to load actions.json</div>';
    }
}

function buildMutationQuery(mutationName, args) {
    if (!args || args.length === 0) {
        return `mutation ${mutationName} {\n  ${mutationName} {\n    __typename\n  }\n}`;
    }
    const argDeclarations = args.map(arg => {
        let typeStr = arg.isList ? `[${arg.type}!]` : arg.type;
        if (arg.required) typeStr += '!';
        return `$${arg.name}: ${typeStr}`;
    }).join(', ');
    const argPass = args.map(arg => `${arg.name}: $${arg.name}`).join(', ');
    return `mutation ${mutationName}(${argDeclarations}) {\n  ${mutationName}(${argPass}) {\n    __typename\n  }\n}`;
}

let currentSelectedMutation = null;

function showMutationForm(mutation) {
    currentSelectedMutation = mutation;
    let html = '<div class="space-y-2">';
    for (let arg of mutation.args) {
        const isList = arg.isList;
        const isObject = !['String', 'Int', 'Float', 'Boolean', 'ID'].includes(arg.type);
        let inputHtml;
        if (arg.type === 'Boolean' && !isList) {
            inputHtml = `<select id="arg-${arg.name}" class="var-input w-full border rounded px-2 py-1 text-xs">
                <option value="true">true</option>
                <option value="false">false</option>
            </select>`;
        } else if (isList || isObject) {
            inputHtml = `<textarea id="arg-${arg.name}" class="var-input w-full border rounded px-2 py-1 text-xs font-mono" rows="2" placeholder='${isList ? '["value1", "value2"]' : '{"field": "value"}'}'></textarea>`;
        } else {
            inputHtml = `<input type="text" id="arg-${arg.name}" class="var-input w-full border rounded px-2 py-1 text-xs" placeholder="${arg.type}">`;
        }
        html += `
            <div>
                <label class="block text-xs font-medium">${arg.name} (${arg.type}${arg.isList ? '[]' : ''})${arg.required ? ' *' : ''}</label>
                ${inputHtml}
                <div class="text-xs text-gray-400 mt-0.5">${arg.type === 'Boolean' ? 'Select true/false' : (isList ? 'Enter JSON array' : (isObject ? 'Enter JSON object' : 'Enter value'))}</div>
            </div>
        `;
    }
    html += '<button id="build-and-send-mutation" class="mt-2 bg-purple-600 text-white px-3 py-1 rounded text-xs">Build & Send</button></div>';
    variablesEditor.innerHTML = html;
    document.getElementById('build-and-send-mutation')?.addEventListener('click', () => {
        const variables = {};
        let missing = false;
        for (let arg of mutation.args) {
            const input = document.getElementById(`arg-${arg.name}`);
            let rawValue = input.value.trim();
            if (arg.required && !rawValue) {
                alert(`Missing required argument: ${arg.name}`);
                missing = true;
                break;
            }
            if (rawValue === '') {
                variables[arg.name] = null;
                continue;
            }
            try {
                if (arg.type === 'Boolean' && !arg.isList) {
                    variables[arg.name] = rawValue === 'true';
                } else {
                    variables[arg.name] = JSON.parse(rawValue);
                }
            } catch (e) {
                variables[arg.name] = rawValue;
            }
        }
        if (missing) return;
        const query = mutation.query;
        const operation = {
            type: "subscribe",
            id: String(Date.now()),
            payload: { query, variables }
        };
        mutationInput.value = JSON.stringify(operation, null, 2);
        setActiveTab('send');
        sendStatus.textContent = 'Operation built. Click Send Operation to execute.';
    });
}

// --- Automation Scripts ---
function loadStoredScripts() {
    chrome.storage.local.get(['automationScripts'], (result) => {
        automationScripts = result.automationScripts || [];
        automationScripts = automationScripts.map(s => ({ ...s, enabled: s.enabled !== false }));
        updateScriptsPreview();
        if (actionsMutations.length > 0) pushScriptsToPage();
    });
}

function pushScriptsToPage() {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        if (tabs[0]) {
            chrome.tabs.sendMessage(tabs[0].id, {
                type: 'SBC_UPDATE_SCRIPTS',
                payload: { scripts: automationScripts, actionsMutations }
            }).catch(() => {});
        }
    });
}

function updateScriptsPreview() {
    if (!scriptsPreview) return;
    if (automationScripts.length === 0) {
        scriptsPreview.innerHTML = '<div class="text-gray-400 text-center py-4">No script loaded.</div>';
        automationStatus.textContent = '';
        return;
    }
    let html = '';
    automationScripts.forEach((s, idx) => {
        const checked = s.enabled ? 'checked' : '';
        html += `<div class="script-item border-b py-2">
            <div class="flex items-center gap-2 flex-1">
                <label class="toggle-switch">
                    <input type="checkbox" class="script-toggle" data-index="${idx}" ${checked}>
                    <span class="slider"></span>
                </label>
                <div>
                    <span class="font-semibold">${escapeHtml(s.name)}</span>
                    <span class="text-gray-500 ml-2">(${s.trigger.type} → ${s.action.type})</span>
                    <span class="text-gray-400 ml-2">[${s.runMode}]</span>
                </div>
            </div>
        </div>`;
    });
    scriptsPreview.innerHTML = html;
    automationStatus.textContent = `${automationScripts.length} script(s) loaded.`;
    
    document.querySelectorAll('.script-toggle').forEach(toggle => {
        toggle.addEventListener('change', (e) => {
            const idx = parseInt(toggle.dataset.index);
            automationScripts[idx].enabled = toggle.checked;
            chrome.storage.local.set({ automationScripts });
            pushScriptsToPage();
            addLogEntry('info', `Script "${automationScripts[idx].name}" ${toggle.checked ? 'enabled' : 'disabled'}`, 'System');
        });
    });
}

function handleLoadScriptFile(file) {
    const reader = new FileReader();
    reader.onload = (e) => {
        try {
            const json = JSON.parse(e.target.result);
            if (!json.scripts || !Array.isArray(json.scripts)) throw new Error('Invalid format: missing "scripts" array');
            json.scripts.forEach((s, i) => {
                if (!s.name || !s.trigger || !s.action || !s.runMode) throw new Error(`Script at index ${i} missing required fields`);
                s.enabled = true;
            });
            automationScripts = json.scripts;
            chrome.storage.local.set({ automationScripts });
            updateScriptsPreview();
            pushScriptsToPage();
            automationStatus.textContent = `✅ Loaded ${automationScripts.length} scripts.`;
            addLogEntry('success', `Loaded ${automationScripts.length} scripts from file`, 'System');
        } catch (err) {
            alert('Invalid JSON: ' + err.message);
            automationStatus.textContent = '❌ Failed to load script.';
        }
    };
    reader.readAsText(file);
}

// --- Logging ---
function addLogEntry(level, message, scriptName = '') {
    const entry = document.createElement('div');
    entry.className = `log-entry log-${level}`;
    const time = new Date().toLocaleTimeString();
    const scriptPart = scriptName ? `[${scriptName}] ` : '';
    entry.textContent = `[${time}] ${scriptPart}${message}`;
    logContainer.appendChild(entry);
    logContainer.scrollTop = logContainer.scrollHeight;
    while (logContainer.children.length > 100) logContainer.removeChild(logContainer.firstChild);
    if (logContainer.children.length === 1 && logContainer.firstChild.textContent.includes('Logs will appear')) logContainer.innerHTML = '';
}
function clearLog() { logContainer.innerHTML = '<div class="text-gray-500">Logs cleared.</div>'; }

// --- Spoof Functions ---
async function readFileAsBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(file);
    });
}

async function updateSpoofSettings() {
    const audioToggle = document.getElementById('fake-audio-toggle');
    const videoToggle = document.getElementById('fake-video-toggle');
    const audioLoopChk = document.getElementById('audio-loop');
    const videoLoopChk = document.getElementById('video-loop');
    
    spoofSettings.audioEnabled = audioToggle.checked;
    spoofSettings.videoEnabled = videoToggle.checked;
    spoofSettings.audioLoop = audioLoopChk.checked;
    spoofSettings.videoLoop = videoLoopChk.checked;
    
    // Save to storage
    chrome.storage.local.set({ spoofSettings });
    
    // Send to page
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        if (tabs[0]) {
            chrome.tabs.sendMessage(tabs[0].id, { type: 'SBC_UPDATE_SPOOF', payload: spoofSettings }).catch(() => {});
        }
    });
    
    document.getElementById('spoof-status').innerHTML = `<span class="text-green-600">✅ Settings applied. Reload BBB page if not already active.</span>`;
    setTimeout(() => {
        if (document.getElementById('spoof-status')) document.getElementById('spoof-status').innerHTML = '';
    }, 3000);
}

async function handleAudioFile(file) {
    if (!file) return;
    const base64 = await readFileAsBase64(file);
    spoofSettings.audioBase64 = base64;
    spoofSettings.audioMime = file.type;
    document.getElementById('audio-file-name').innerText = `📁 ${file.name}`;
    chrome.storage.local.set({ spoofSettings });
    updateSpoofSettings();
}

async function handleVideoFile(file) {
    if (!file) return;
    const base64 = await readFileAsBase64(file);
    spoofSettings.videoBase64 = base64;
    spoofSettings.videoMime = file.type;
    document.getElementById('video-file-name').innerText = `📁 ${file.name}`;
    chrome.storage.local.set({ spoofSettings });
    updateSpoofSettings();
}

function testStream(type) {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        if (tabs[0]) {
            chrome.tabs.sendMessage(tabs[0].id, { type: 'SBC_TEST_SPOOF_STREAM', payload: { type } }).catch(() => {
                alert('Cannot test: Please refresh BBB page and apply spoof first.');
            });
        }
    });
}

function resetSpoof() {
    spoofSettings = {
        audioEnabled: false,
        videoEnabled: false,
        audioBase64: null,
        videoBase64: null,
        audioMime: null,
        videoMime: null,
        audioLoop: true,
        videoLoop: true
    };
    document.getElementById('fake-audio-toggle').checked = false;
    document.getElementById('fake-video-toggle').checked = false;
    document.getElementById('audio-loop').checked = true;
    document.getElementById('video-loop').checked = true;
    document.getElementById('audio-file-name').innerText = '';
    document.getElementById('video-file-name').innerText = '';
    chrome.storage.local.set({ spoofSettings });
    updateSpoofSettings();
    document.getElementById('spoof-status').innerHTML = `<span class="text-blue-600">🔄 Spoof reset. Real devices will be used.</span>`;
    setTimeout(() => {
        if (document.getElementById('spoof-status')) document.getElementById('spoof-status').innerHTML = '';
    }, 3000);
}

function loadSpoofSettingsFromStorage() {
    chrome.storage.local.get(['spoofSettings'], (result) => {
        if (result.spoofSettings) {
            spoofSettings = result.spoofSettings;
            document.getElementById('fake-audio-toggle').checked = spoofSettings.audioEnabled || false;
            document.getElementById('fake-video-toggle').checked = spoofSettings.videoEnabled || false;
            document.getElementById('audio-loop').checked = spoofSettings.audioLoop !== false;
            document.getElementById('video-loop').checked = spoofSettings.videoLoop !== false;
            if (spoofSettings.audioBase64) document.getElementById('audio-file-name').innerText = '📁 Audio loaded';
            if (spoofSettings.videoBase64) document.getElementById('video-file-name').innerText = '📁 Video loaded';
        }
    });
}

// --- Data loading and UI updates ---
async function loadData() {
    try {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        currentTabUrl = tab.url;
        const response = await chrome.tabs.sendMessage(tab.id, { type: 'SBC_GET_DATA' });
        if (response && !response.error) {
            currentData = response;
            updateUI(response);
        } else {
            showError('Not connected to BBB meeting');
        }
    } catch (e) {
        showError('Refresh the BBB page');
    }
}

function updateUI(data) {
    const user = data.userInfo || {};
    userEls.name.textContent = user.name || '-';
    userEls.id.textContent = user.userId || '-';
    userEls.role.textContent = user.role || '-';
    const states = ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'];
    userEls.wsState.textContent = data.wsReadyState !== null ? states[data.wsReadyState] : 'Not connected';
    connIndicator.className = `w-3 h-3 rounded-full mr-2 ${data.hasConnection ? 'bg-green-500' : 'bg-red-500'}`;
    
    messageList.innerHTML = (data.messageHistory || []).slice(-5).reverse().map(m => {
        const time = new Date(m.timestamp).toLocaleTimeString();
        const type = m.data?.type || '?';
        return `<div class="border-b py-1"><span class="text-gray-500">${time}</span> ${m.direction==='received'?'⬅':'➡'} ${type}</div>`;
    }).join('') || 'No messages';
    
    lastMsgIdDisplay.textContent = data.lastChatMessageId ? `ID: ${data.lastChatMessageId.slice(0,15)}...` : 'No message ID';
    
    // Discovered methods
    const methods = data.discoveredMethods || [];
    if (methods.length === 0) {
        methodsList.innerHTML = '<div class="text-gray-400 text-center py-4">No methods discovered yet.</div>';
    } else {
        const sorted = [...methods].sort((a, b) => {
            const aPin = pinnedMethods.has(a.name) ? 1 : 0;
            const bPin = pinnedMethods.has(b.name) ? 1 : 0;
            if (aPin !== bPin) return bPin - aPin;
            return new Date(b.lastSeen || 0) - new Date(a.lastSeen || 0);
        });
        methodsList.innerHTML = sorted.map(m => `
            <div class="border-b py-1 flex justify-between items-center hover:bg-gray-100 cursor-pointer ${pinnedMethods.has(m.name) ? 'pinned' : ''}" data-method='${JSON.stringify(m).replace(/'/g, "&apos;")}'>
                <span><span class="font-mono font-semibold">${m.name}</span> <span class="text-gray-500 text-xs">(${m.type})</span></span>
                <span class="text-gray-500">${m.count}x</span>
            </div>
        `).join('');
        methodsList.querySelectorAll('[data-method]').forEach(el => {
            el.addEventListener('click', () => {
                try {
                    const method = JSON.parse(el.dataset.method.replace(/&apos;/g, "'"));
                    loadMethodToSend(method);
                } catch (e) { console.warn(e); }
            });
        });
    }
    
    // Executed
    const executed = data.executedOperations || [];
    if (executed.length === 0) {
        executedList.innerHTML = '<div class="text-gray-400 text-center py-4">No executions yet.</div>';
    } else {
        const sorted = [...executed].reverse();
        executedList.innerHTML = sorted.map(m => `
            <div class="border-b py-1 flex items-center hover:bg-gray-100 ${pinnedMethods.has(m.name) ? 'pinned' : ''}">
                <button class="pin-executed text-yellow-500 mr-1" data-opname="${m.name}">⭐</button>
                <div class="flex-1 cursor-pointer" data-executed='${JSON.stringify(m).replace(/'/g, "&apos;")}'>
                    <span class="font-mono font-semibold">${m.name || 'unknown'}</span>
                    <span class="text-gray-500 ml-1">(${m.type})</span>
                    <span class="text-gray-400 ml-2">${new Date(m.timestamp).toLocaleTimeString()}</span>
                </div>
                <button class="rerun-executed text-blue-500 text-xs px-1" data-executed='${JSON.stringify(m).replace(/'/g, "&apos;")}'>↻</button>
            </div>
        `).join('');
        executedList.querySelectorAll('[data-executed]').forEach(el => {
            el.addEventListener('click', (e) => {
                if (e.target.classList.contains('pin-executed') || e.target.classList.contains('rerun-executed')) return;
                try {
                    const exec = JSON.parse(el.dataset.executed.replace(/&apos;/g, "'"));
                    loadExecutedToSend(exec);
                } catch (e) { console.warn(e); }
            });
        });
        executedList.querySelectorAll('.pin-executed').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const opName = btn.dataset.opname;
                pinnedMethods.has(opName) ? pinnedMethods.delete(opName) : pinnedMethods.add(opName);
                savePinned();
                updateUI(currentData);
            });
        });
        executedList.querySelectorAll('.rerun-executed').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                try {
                    const exec = JSON.parse(btn.dataset.executed.replace(/&apos;/g, "'"));
                    sendExactOperation(exec);
                } catch (e) { console.warn(e); }
            });
        });
    }
    
    // Responses
    const responses = data.operationResponses || [];
    if (responses.length === 0) {
        responsesList.innerHTML = '<div class="text-gray-400 text-center py-4">No responses yet.</div>';
    } else {
        responsesList.innerHTML = responses.reverse().map(r => `
            <div class="border-b py-1 hover:bg-gray-100 cursor-pointer" data-response='${JSON.stringify(r).replace(/'/g, "&apos;")}'>
                <span class="font-mono">ID: ${r.id}</span>
                <span class="text-gray-500 ml-2">${new Date(r.timestamp).toLocaleTimeString()}</span>
                <span class="text-blue-500 ml-2">Click to view</span>
            </div>
        `).join('');
        responsesList.querySelectorAll('[data-response]').forEach(el => {
            el.addEventListener('click', () => {
                try {
                    const resp = JSON.parse(el.dataset.response.replace(/&apos;/g, "'"));
                    alert(JSON.stringify(resp.data, null, 2));
                } catch (e) { console.warn(e); }
            });
        });
    }
    
    renderActionsFileList();
}

function renderActionsFileList(filter = '') {
    if (!actionsListDiv) return;
    actionsListDiv.innerHTML = '';
    const headerDiv = document.createElement('div');
    headerDiv.className = 'mb-2 p-2 bg-gray-100 rounded text-xs';
    const count = actionsMutations.length;
    headerDiv.innerHTML = `✅ ${count} mutations loaded from actions.json`;
    actionsListDiv.appendChild(headerDiv);
    
    let filtered = actionsMutations;
    if (filter) filtered = actionsMutations.filter(m => m.name.toLowerCase().includes(filter.toLowerCase()));
    
    const listContainer = document.createElement('div');
    if (filtered.length === 0) {
        listContainer.innerHTML = '<div class="text-gray-400 text-center py-4">No mutations found.</div>';
    } else {
        let listHtml = '';
        for (const m of filtered) {
            listHtml += `<div class="border-b py-1 hover:bg-gray-100 cursor-pointer" data-action-mutation='${JSON.stringify(m).replace(/'/g, "&apos;")}'>
                <span class="font-mono font-semibold">${m.name}</span>
            </div>`;
        }
        listContainer.innerHTML = listHtml;
    }
    actionsListDiv.appendChild(listContainer);
    
    actionsListDiv.querySelectorAll('[data-action-mutation]').forEach(el => {
        el.addEventListener('click', () => {
            try {
                const mut = JSON.parse(el.dataset.actionMutation.replace(/&apos;/g, "'"));
                showMutationForm(mut);
                setActiveTab('send');
            } catch (e) { console.warn(e); }
        });
    });
}

function loadMethodToSend(method) {
    const template = { type: "subscribe", id: String(Date.now()), payload: { query: method.query, variables: method.variables } };
    mutationInput.value = JSON.stringify(template, null, 2);
    updateVariablesEditor(method.variables);
    setActiveTab('send');
}

function loadExecutedToSend(exec) {
    const template = { type: "subscribe", id: String(Date.now()), payload: { query: exec.query, variables: exec.variables } };
    mutationInput.value = JSON.stringify(template, null, 2);
    updateVariablesEditor(exec.variables);
    setActiveTab('send');
}

function updateVariablesEditor(variables) {
    if (!variables || Object.keys(variables).length === 0) {
        variablesEditor.innerHTML = '<div class="text-gray-400 text-center">No variables</div>';
        return;
    }
    let html = '<div class="grid grid-cols-2 gap-1">';
    for (const [key, value] of Object.entries(variables)) {
        const valStr = typeof value === 'string' ? value : JSON.stringify(value);
        html += `<div class="text-xs font-medium">${key}:</div>`;
        html += `<input type="text" class="var-input text-xs border rounded px-1" data-var="${key}" value="${valStr.replace(/"/g, '&quot;')}" />`;
    }
    html += '</div><button id="apply-vars-btn" class="mt-2 text-xs bg-blue-500 text-white px-2 py-1 rounded">Apply to JSON</button>';
    variablesEditor.innerHTML = html;
    document.getElementById('apply-vars-btn').addEventListener('click', applyVariablesToJson);
}

function applyVariablesToJson() {
    try {
        let mutation = JSON.parse(mutationInput.value);
        if (!mutation.payload) mutation.payload = {};
        mutation.payload.variables = {};
        document.querySelectorAll('.var-input').forEach(input => {
            const key = input.dataset.var;
            let value = input.value;
            try { value = JSON.parse(value); } catch (e) {}
            mutation.payload.variables[key] = value;
        });
        mutationInput.value = JSON.stringify(mutation, null, 2);
        sendStatus.textContent = 'Variables applied';
    } catch (e) {
        sendStatus.textContent = 'Invalid JSON';
    }
}

function showError(msg) {
    userEls.name.textContent = 'Error';
    userEls.id.textContent = msg;
}

async function sendAction(action, value) {
    actionStatus.textContent = '⏳ Sending...';
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const requestId = Date.now();
    try {
        const response = await new Promise((resolve, reject) => {
            const timeout = setTimeout(() => reject(new Error('Timeout')), 5000);
            const listener = (message) => {
                if (message.type === 'SBC_ACTION_RESULT' && message.requestId === requestId) {
                    clearTimeout(timeout);
                    chrome.runtime.onMessage.removeListener(listener);
                    resolve(message.payload);
                }
            };
            chrome.runtime.onMessage.addListener(listener);
            chrome.tabs.sendMessage(tab.id, { type: 'SBC_ACTION', payload: { action, value }, requestId }).catch(reject);
        });
        if (response && response.success) {
            actionStatus.textContent = `✅ ${action} succeeded`;
            loadData();
        } else {
            actionStatus.textContent = `❌ ${action} failed: ${response?.error || 'Unknown'}`;
        }
    } catch (e) {
        actionStatus.textContent = `❌ Error: ${e.message}`;
    }
}

async function sendExactOperation(exec) {
    const operation = { type: "subscribe", id: String(Date.now()), payload: { query: exec.query, variables: exec.variables } };
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const requestId = Date.now();
    try {
        const response = await new Promise((resolve, reject) => {
            const timeout = setTimeout(() => reject(new Error('Timeout')), 5000);
            const listener = (message) => {
                if (message.type === 'SBC_SEND_RESULT' && message.requestId === requestId) {
                    clearTimeout(timeout);
                    chrome.runtime.onMessage.removeListener(listener);
                    resolve(message.payload);
                }
            };
            chrome.runtime.onMessage.addListener(listener);
            chrome.tabs.sendMessage(tab.id, { type: 'SBC_SEND_OPERATION', payload: { operation }, requestId }).catch(reject);
        });
        sendStatus.textContent = response.success ? '✅ Sent' : `❌ Failed: ${response.error || ''}`;
        if (response.success) loadData();
    } catch (e) {
        sendStatus.textContent = 'Error sending: ' + e.message;
    }
}

function savePinned() {
    chrome.storage.local.set({ pinnedMethods: Array.from(pinnedMethods) });
}

function escapeHtml(str) {
    return str.replace(/[&<>]/g, function(m) {
        if (m === '&') return '&amp;';
        if (m === '<') return '&lt;';
        if (m === '>') return '&gt;';
        return m;
    });
}

// --- Event listeners ---
document.getElementById('hand-raise-btn').addEventListener('click', () => sendAction('raiseHand', true));
document.getElementById('hand-lower-btn').addEventListener('click', () => sendAction('raiseHand', false));
document.getElementById('mobile-on-btn').addEventListener('click', () => sendAction('setMobile', true));
document.getElementById('mobile-off-btn').addEventListener('click', () => sendAction('setMobile', false));
document.getElementById('away-on-btn').addEventListener('click', () => sendAction('setAway', true));
document.getElementById('away-off-btn').addEventListener('click', () => sendAction('setAway', false));
document.querySelectorAll('.reaction-btn').forEach(btn => {
    btn.addEventListener('click', () => sendAction('sendReaction', { emoji: btn.dataset.emoji }));
});

document.getElementById('send-mutation-btn').addEventListener('click', async () => {
    try {
        const operation = JSON.parse(mutationInput.value);
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        const requestId = Date.now();
        const response = await new Promise((resolve, reject) => {
            const timeout = setTimeout(() => reject(new Error('Timeout')), 5000);
            const listener = (message) => {
                if (message.type === 'SBC_SEND_RESULT' && message.requestId === requestId) {
                    clearTimeout(timeout);
                    chrome.runtime.onMessage.removeListener(listener);
                    resolve(message.payload);
                }
            };
            chrome.runtime.onMessage.addListener(listener);
            chrome.tabs.sendMessage(tab.id, { type: 'SBC_SEND_OPERATION', payload: { operation }, requestId }).catch(reject);
        });
        sendStatus.textContent = response.success ? '✅ Sent' : `❌ Failed: ${response.error || ''}`;
        if (response.success) loadData();
    } catch (e) {
        sendStatus.textContent = '❌ Invalid JSON or send error: ' + e.message;
    }
});

document.getElementById('load-example-btn').addEventListener('click', () => {
    mutationInput.value = JSON.stringify({
        type: "subscribe",
        id: String(Date.now()),
        payload: {
            query: "mutation userSetRaiseHand($raiseHand: Boolean!) { userSetRaiseHand(raiseHand: $raiseHand) { __typename } }",
            variables: { raiseHand: true }
        }
    }, null, 2);
    updateVariablesEditor({ raiseHand: true });
});

document.getElementById('format-json-btn').addEventListener('click', () => {
    try {
        mutationInput.value = JSON.stringify(JSON.parse(mutationInput.value), null, 2);
        sendStatus.textContent = 'Formatted';
    } catch (e) {
        sendStatus.textContent = 'Invalid JSON';
    }
});

document.getElementById('update-vars-from-json').addEventListener('click', () => {
    try {
        const op = JSON.parse(mutationInput.value);
        updateVariablesEditor(op.payload?.variables || {});
    } catch (e) {}
});

document.getElementById('refresh-btn').addEventListener('click', loadData);
document.getElementById('refresh-methods-btn').addEventListener('click', loadData);
document.getElementById('clear-executed-btn').addEventListener('click', () => {
    executedList.innerHTML = '<div class="text-gray-400 text-center py-4">Cleared. Refresh to reload.</div>';
});
document.getElementById('clear-responses-btn').addEventListener('click', () => {
    responsesList.innerHTML = '<div class="text-gray-400 text-center py-4">Cleared. Refresh to reload.</div>';
});

document.getElementById('actions-filter')?.addEventListener('input', (e) => renderActionsFileList(e.target.value));

// Automation events
document.getElementById('load-script-btn').addEventListener('click', () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json,application/json';
    input.onchange = (e) => { if (e.target.files[0]) handleLoadScriptFile(e.target.files[0]); };
    input.click();
});
document.getElementById('clear-scripts-btn').addEventListener('click', () => {
    automationScripts = [];
    chrome.storage.local.set({ automationScripts: [] });
    updateScriptsPreview();
    pushScriptsToPage();
    automationStatus.textContent = 'Scripts cleared.';
    addLogEntry('info', 'All scripts cleared', 'System');
});
document.getElementById('clear-log-btn').addEventListener('click', clearLog);

// Spoof events
document.getElementById('fake-audio-file').addEventListener('change', (e) => { if (e.target.files[0]) handleAudioFile(e.target.files[0]); });
document.getElementById('fake-video-file').addEventListener('change', (e) => { if (e.target.files[0]) handleVideoFile(e.target.files[0]); });
document.getElementById('apply-spoof-btn').addEventListener('click', updateSpoofSettings);
document.getElementById('reset-spoof-btn').addEventListener('click', resetSpoof);
document.getElementById('test-audio-btn').addEventListener('click', () => testStream('audio'));
document.getElementById('test-video-btn').addEventListener('click', () => testStream('video'));
document.getElementById('fake-audio-toggle').addEventListener('change', updateSpoofSettings);
document.getElementById('fake-video-toggle').addEventListener('change', updateSpoofSettings);
document.getElementById('audio-loop').addEventListener('change', updateSpoofSettings);
document.getElementById('video-loop').addEventListener('change', updateSpoofSettings);

// Initialize
loadActionsFile();
loadStoredScripts();
loadSpoofSettingsFromStorage();
chrome.storage.local.get(['pinnedMethods'], (result) => {
    if (result.pinnedMethods) pinnedMethods = new Set(result.pinnedMethods);
});
loadData();
setActiveTab('actions');

chrome.runtime.onMessage.addListener((message) => {
    if (message.type === 'SBC_DATA_RESPONSE') {
        currentData = message.payload;
        updateUI(message.payload);
    }
    if (message.type === 'SBC_SCRIPT_LOG') {
        const { level, script, message: logMsg } = message.payload;
        addLogEntry(level, logMsg, script);
    }
});