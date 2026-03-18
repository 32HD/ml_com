const EXECUTION_MODES = ["inspect", "workspace", "full-auto"];
export const CUSTOM_QUICK_SLOTS = 2;

function readStorage(key, fallback = "") {
  try {
    return window.localStorage.getItem(key) ?? fallback;
  } catch (_error) {
    return fallback;
  }
}

function writeStorage(key, value) {
  try {
    if (value === null || value === undefined || value === "") {
      window.localStorage.removeItem(key);
    } else {
      window.localStorage.setItem(key, value);
    }
  } catch (_error) {
    // Ignore storage failures in mobile browsers.
  }
}

function repoKey(prefix, repoId) {
  return `${prefix}_${repoId || "default"}`;
}

export function getPreferredExecutionMode(repoId) {
  const raw = String(readStorage(repoKey("codex_execution_mode", repoId), "full-auto")).trim();
  return EXECUTION_MODES.includes(raw) ? raw : "full-auto";
}

export function setPreferredExecutionMode(repoId, mode) {
  writeStorage(repoKey("codex_execution_mode", repoId), mode || "full-auto");
}

export function getCustomQuickPrompts(repoId) {
  const raw = readStorage(repoKey("codex_custom_quicks", repoId), "");
  if (!raw) return [];
  try {
    const data = JSON.parse(raw);
    if (!Array.isArray(data)) return [];
    return data.slice(0, CUSTOM_QUICK_SLOTS).map((item) => ({
      label: String(item?.label || "").trim(),
      template: String(item?.template || "").trim(),
    }));
  } catch (_error) {
    return [];
  }
}

export function setCustomQuickPrompts(repoId, items) {
  writeStorage(repoKey("codex_custom_quicks", repoId), JSON.stringify(items || []));
}

export function buildCustomQuickInputs(items) {
  return Array.from({ length: CUSTOM_QUICK_SLOTS }, (_, index) => ({
    label: String(items?.[index]?.label || ""),
    template: String(items?.[index]?.template || ""),
  }));
}

export function getChatLayout() {
  const compact = readStorage("codex_chat_compact") === "1";
  const fontSize = Number(readStorage("codex_chat_font_size", "15"));
  return {
    compact,
    fontSize: Number.isFinite(fontSize) ? Math.min(18, Math.max(11, fontSize)) : 15,
  };
}

export function setChatLayout({ compact, fontSize }) {
  writeStorage("codex_chat_compact", compact ? "1" : "0");
  writeStorage("codex_chat_font_size", String(fontSize));
}

export function getPromptDraft(repoId, sessionId) {
  const key = `codex_prompt_draft_${sessionId || repoId || "default"}`;
  return readStorage(key, "");
}

export function setPromptDraft(repoId, sessionId, value) {
  const key = `codex_prompt_draft_${sessionId || repoId || "default"}`;
  writeStorage(key, value || "");
}

export function getLastPrompt(repoId) {
  return String(readStorage(repoKey("codex_last_prompt", repoId), "")).trim();
}

export function setLastPrompt(repoId, value) {
  writeStorage(repoKey("codex_last_prompt", repoId), value || "");
}

export function getPendingPrompt(sessionId) {
  if (!sessionId) return "";
  const raw = readStorage(`codex_pending_prompt_${sessionId}`, "");
  if (!raw) return "";
  try {
    const data = JSON.parse(raw);
    return String(data?.prompt || "").trim();
  } catch (_error) {
    return "";
  }
}

export function setPendingPrompt(sessionId, prompt) {
  if (!sessionId) return;
  writeStorage(
    `codex_pending_prompt_${sessionId}`,
    JSON.stringify({ prompt, savedAt: new Date().toISOString() }),
  );
}

export function clearPendingPrompt(sessionId) {
  if (!sessionId) return;
  writeStorage(`codex_pending_prompt_${sessionId}`, null);
}

export function getActiveSessionId(repoId) {
  return String(readStorage(repoKey("codex_active_session", repoId), "")).trim();
}

export function setActiveSessionId(repoId, sessionId) {
  writeStorage(repoKey("codex_active_session", repoId), sessionId || null);
}

export function getActiveRepoId() {
  return String(readStorage("codex_active_repo", "")).trim();
}

export function setActiveRepoId(repoId) {
  writeStorage("codex_active_repo", repoId || null);
}
