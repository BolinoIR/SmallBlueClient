const statusElement = document.querySelector("#status");
const exportButton = document.querySelector("#export");
let activeSession = null;

function stableStringify(value) {
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

async function refresh() {
  statusElement.textContent = "Checking the active tab…";
  exportButton.disabled = true;
  const response = await chrome.runtime.sendMessage({ type: "SBC_REFRESH_ACTIVE_SESSION" });
  if (response?.error) {
    activeSession = null;
    if (response.media) {
      renderMediaRequired(response.media);
    } else {
      statusElement.textContent = response.error;
    }
    return;
  }
  activeSession = response.session;
  const user = activeSession.user_name || activeSession.user_id || "authenticated BBB user";
  const meeting = activeSession.meeting_name || activeSession.meeting_id || "BBB meeting";
  statusElement.innerHTML = `<strong>BBB session detected</strong><br><span class="muted">${escapeHtml(user)} · ${escapeHtml(meeting)}</span>`;
  exportButton.disabled = false;
}

function renderMediaRequired(media) {
  const reasons = Array.isArray(media?.reasons) ? media.reasons : [];
  const detail = reasons.length
    ? `<ul>${reasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>`
    : "Join BBB audio before exporting.";
  statusElement.innerHTML = [
    "<strong>Connect BBB audio before export</strong>",
    "<p class=\"muted\">Select <b>Listen only</b> or <b>Microphone</b> in BBB. Wait until BBB shows the headphone or microphone icon, then reopen this popup.</p>",
    detail,
  ].join("");
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

exportButton.addEventListener("click", async () => {
  if (!activeSession) return;
  exportButton.disabled = true;
  try {
    const canonical = stableStringify(activeSession);
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical));
    const sha256 = [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
    // A .sbc file is JSON data, but it is a credential package rather than a
    // document for a browser JSON viewer.  Using a generic binary MIME type
    // keeps Chrome from selecting/appending the .json extension in Save As.
    const blob = new Blob([JSON.stringify({ session: activeSession, sha256 }, null, 2)], { type: "application/octet-stream" });
    const url = URL.createObjectURL(blob);
    const meeting = (activeSession.meeting_name || "bbb-session").replace(/[^a-z0-9]+/gi, "-").replace(/(^-|-$)/g, "").toLowerCase();
    await chrome.downloads.download({
      url,
      filename: `${meeting || "bbb-session"}.sbc`,
      saveAs: true,
      conflictAction: "uniquify",
    });
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    statusElement.textContent = "Media-ready session exported. Keep the .sbc file private.";
  } catch (error) {
    statusElement.textContent = `Export failed: ${error.message}`;
  } finally {
    exportButton.disabled = !activeSession;
  }
});

refresh().catch((error) => { statusElement.textContent = `Unable to inspect tab: ${error.message}`; });
