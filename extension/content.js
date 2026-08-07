// Isolated-world bridge. It injects the passive page observer and only relays
// session data to the extension service worker; it never sends BBB mutations.
(() => {
  const script = document.createElement("script");
  script.src = chrome.runtime.getURL("page-capture.js");
  script.async = false;
  (document.documentElement || document.head).appendChild(script);
  script.remove();

  const requestCapture = () => new Promise((resolve) => {
    const requestId = crypto.randomUUID();
    const timeout = setTimeout(() => {
      window.removeEventListener("message", listener);
      resolve(null);
    }, 1500);
    const listener = (event) => {
      if (event.source !== window || event.data?.type !== "SBC_PAGE_CAPTURE" || event.data.requestId !== requestId) return;
      clearTimeout(timeout);
      window.removeEventListener("message", listener);
      resolve(event.data.capture || null);
    };
    window.addEventListener("message", listener);
    window.postMessage({ type: "SBC_REQUEST_PAGE_CAPTURE", requestId }, "*");
  });

  window.addEventListener("message", (event) => {
    if (event.source !== window || event.data?.type !== "SBC_PAGE_CAPTURE_UPDATED") return;
    const capture = event.data.capture;
    if (capture?.detected && capture.session) {
      chrome.runtime.sendMessage({ type: "SBC_CAPTURE_UPDATED", session: capture.session }).catch(() => {});
    }
  });

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.type !== "SBC_GET_CAPTURE") return;
    requestCapture().then((capture) => sendResponse(capture || { detected: false }));
    return true;
  });
})();
