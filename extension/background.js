/**
 * SBC Session Extractor service worker.
 *
 * This extension does not automate BBB. It receives an observed GraphQL
 * connection from the page, adds browser cookies that page scripts cannot
 * access, and returns a portable session to the popup for download.
 */
const sessions = new Map();

async function attachCookies(session, pageUrl) {
  if (!pageUrl) return session;
  const cookies = await chrome.cookies.getAll({ url: pageUrl });
  const cookie = cookies.map(({ name, value }) => `${name}=${value}`).join("; ");
  return {
    ...session,
    headers: cookie ? { ...(session.headers || {}), Cookie: cookie } : (session.headers || {}),
  };
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const tabId = sender.tab?.id;

  if (message.type === "SBC_CAPTURE_UPDATED" && tabId !== undefined) {
    // Do not retain a GraphQL-only session. A saved session becomes usable for
    // SBC media only after the active BBB tab has completed its own audio
    // connection and browser-authorized TURN/ICE request.
    if (!message.media?.ready) {
      sessions.delete(tabId);
      sendResponse({ ok: true, skipped: "BBB media is not ready" });
      return;
    }
    attachCookies(message.session, sender.tab.url)
      .then((session) => {
        sessions.set(tabId, session);
        sendResponse({ ok: true });
      })
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }

  if (message.type === "SBC_GET_ACTIVE_SESSION") {
    activeTab().then((tab) => sendResponse(tab?.id === undefined ? null : sessions.get(tab.id) || null));
    return true;
  }

  if (message.type === "SBC_REFRESH_ACTIVE_SESSION") {
    activeTab().then(async (tab) => {
      if (!tab?.id) return sendResponse({ error: "No active tab" });
      try {
        const capture = await chrome.tabs.sendMessage(tab.id, { type: "SBC_GET_CAPTURE" });
        if (!capture?.detected) return sendResponse({ error: "No authenticated BBB GraphQL session detected in this tab" });
        if (!capture.media?.ready) {
          return sendResponse({
            error: "BBB media is not ready yet.",
            media: capture.media || null,
          });
        }
        const session = await attachCookies(capture.session, tab.url);
        sessions.set(tab.id, session);
        sendResponse({ session });
      } catch (error) {
        sendResponse({ error: error.message });
      }
    });
    return true;
  }
});
