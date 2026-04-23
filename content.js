// content.js
(function() {
    if (!window.location.pathname.includes('/html5client')) return;

    console.log('[ 📊 SBC] Content script loaded. Injecting interceptor...');

    const script = document.createElement('script');
    script.src = chrome.runtime.getURL('inject.js');
    script.onload = () => script.remove();
    (document.head || document.documentElement).appendChild(script);

    // Forward messages from injected script to popup/background
    window.addEventListener('message', (event) => {
        if (event.source !== window) return;
        const { type, payload, requestId } = event.data;
        if (type === 'SBC_DATA_RESPONSE') {
            chrome.runtime.sendMessage({ type: 'SBC_DATA_RESPONSE', payload }).catch(() => {});
        }
        if (type === 'SBC_SEND_RESULT') {
            chrome.runtime.sendMessage({ type: 'SBC_SEND_RESULT', payload, requestId }).catch(() => {});
        }
        if (type === 'SBC_ACTION_RESULT') {
            chrome.runtime.sendMessage({ type: 'SBC_ACTION_RESULT', payload, requestId }).catch(() => {});
        }
        if (type === 'SBC_TOAST') {
            chrome.runtime.sendMessage({ type: 'SBC_TOAST', payload }).catch(() => {});
        }
        if (type === 'SBC_SCRIPT_LOG') {
            chrome.runtime.sendMessage({ type: 'SBC_SCRIPT_LOG', payload }).catch(() => {});
        }
    });

    // Load scripts from storage and send to page on startup
    chrome.storage.local.get(['automationScripts'], (result) => {
        const scripts = result.automationScripts || [];
        window.postMessage({ type: 'SBC_UPDATE_SCRIPTS', payload: { scripts } }, '*');
    });

    // Listen for requests from popup
    chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
        if (message.type === 'SBC_GET_DATA') {
            window.postMessage({ type: 'SBC_GET_DATA' }, '*');
            const listener = (event) => {
                if (event.source !== window) return;
                if (event.data && event.data.type === 'SBC_DATA_RESPONSE') {
                    window.removeEventListener('message', listener);
                    sendResponse(event.data.payload);
                }
            };
            window.addEventListener('message', listener);
            setTimeout(() => {
                window.removeEventListener('message', listener);
                sendResponse({ error: 'No response from page' });
            }, 2000);
            return true;
        }
        
        if (message.type === 'SBC_SEND_OPERATION') {
            window.postMessage({
                type: 'SBC_SEND_OPERATION',
                payload: message.payload,
                requestId: message.requestId
            }, '*');
            const listener = (event) => {
                if (event.source !== window) return;
                if (event.data && event.data.type === 'SBC_SEND_RESULT' && event.data.requestId === message.requestId) {
                    window.removeEventListener('message', listener);
                    sendResponse(event.data.payload);
                }
            };
            window.addEventListener('message', listener);
            setTimeout(() => {
                window.removeEventListener('message', listener);
                sendResponse({ success: false, error: 'Timeout waiting for response' });
            }, 5000);
            return true;
        }
        
        if (message.type === 'SBC_ACTION') {
            window.postMessage({
                type: 'SBC_ACTION',
                payload: message.payload,
                requestId: message.requestId
            }, '*');
            const listener = (event) => {
                if (event.source !== window) return;
                if (event.data && event.data.type === 'SBC_ACTION_RESULT' && event.data.requestId === message.requestId) {
                    window.removeEventListener('message', listener);
                    sendResponse(event.data.payload);
                }
            };
            window.addEventListener('message', listener);
            setTimeout(() => {
                window.removeEventListener('message', listener);
                sendResponse({ success: false, error: 'Timeout' });
            }, 5000);
            return true;
        }
        
        if (message.type === 'SBC_UPDATE_SCRIPTS') {
            window.postMessage({ type: 'SBC_UPDATE_SCRIPTS', payload: message.payload }, '*');
            sendResponse({ success: true });
            return true;
        }
    });
})();