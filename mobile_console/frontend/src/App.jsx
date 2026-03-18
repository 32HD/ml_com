import React, { useEffect, useRef, useState } from "react";

import { RichText } from "./components/RichText";
import { SessionHub } from "./components/SessionHub";
import { api, apiUrl } from "./lib/api";
import {
  QUICK_PROMPT_DEFAULTS,
  STATUS_LABELS,
  bubbleTimeLabel,
  buildSessionHubView,
  buildConversationItems,
  buildDisplayLogLines,
  cleanAssistantMessageText,
  executionModeLabel,
  formatTs,
  isSharedSession,
  latestLogPreview,
  normalizeInitialPage,
  normalizeLogLine,
  sessionActivityAt,
  sessionDisplayTitle,
  sessionOriginLabel,
  sessionPreviewText,
  statusClass,
  typingStatusMessage,
} from "./lib/chat";
import {
  CUSTOM_QUICK_SLOTS,
  buildCustomQuickInputs,
  clearPendingPrompt,
  getActiveRepoId,
  getActiveSessionId,
  getChatLayout,
  getCustomQuickPrompts,
  getLastPrompt,
  getPendingPrompt,
  getPreferredExecutionMode,
  getPromptDraft,
  setActiveSessionId,
  setActiveRepoId,
  setChatLayout,
  setCustomQuickPrompts,
  setLastPrompt,
  setPendingPrompt,
  setPreferredExecutionMode,
  setPromptDraft,
} from "./lib/storage";

const MAX_LOG_LINES = 1200;
const SNAPSHOT_LOG_LINES = 120;
const SNAPSHOT_TIMELINE_LIMIT = 320;
const ACTIVE_REPO_POLL_MS = 2500;
const BACKGROUND_REPO_POLL_MS = 6000;
const PAGE_LABELS = {
  chat: "对话",
  workspace: "工作区",
  settings: "设置",
};

function parseInitialNav() {
  const query = new URLSearchParams(window.location.search);
  return {
    repoId: query.get("repo") || "",
    sessionId: query.get("session") || "",
    page: normalizeInitialPage(query.get("page") || ""),
  };
}

function currentUrlBase() {
  return `${window.location.origin}${window.location.pathname}`;
}

function flattenTree(items, out = []) {
  for (const item of items || []) {
    if (item.type === "file" && item.path) {
      out.push(item.path);
    }
    if (item.children) flattenTree(item.children, out);
  }
  return out;
}

function clamp(number, min, max) {
  return Math.min(max, Math.max(min, number));
}

function normalizeTimelineText(text) {
  return String(text || "").replace(/\s+/g, " ").trim().toLowerCase();
}

function shouldSkipBootstrapDuplicate(event, terminalPromptKeys) {
  if (String(event?.kind || "") !== "user_message") return false;
  const eventId = String(event?.id || "").trim();
  if (!eventId.startsWith("bootstrap:")) return false;
  const textKey = normalizeTimelineText(event?.text || "");
  return !!textKey && terminalPromptKeys.has(textKey);
}

function buildSessionMeta(session, extra = "") {
  if (!session) return "未连接会话";
  const meta = [];
  meta.push(sessionOriginLabel(session));
  const sessionName = String(session.name || "").trim();
  if (sessionName) meta.push(sessionName);
  if (session.execution_mode) meta.push(executionModeLabel(session.execution_mode));
  if (session.status) meta.push(STATUS_LABELS[session.status] || session.status);
  if (session.codex_model) meta.push(session.codex_model);
  if (extra) meta.push(extra);
  return meta.join(" · ") || "Codex 会话";
}

function sessionActivityText(session) {
  const value = sessionActivityAt(session);
  return value ? `最近活动 ${formatTs(value)}` : `同步于 ${formatTs(session?.updated_at)}`;
}

function mergeSession(prev, next) {
  if (!prev) return next;
  return {
    ...prev,
    ...next,
    codex_session_id: next.codex_session_id ?? prev.codex_session_id ?? null,
    codex_source: next.codex_source ?? prev.codex_source ?? null,
    codex_model: next.codex_model ?? prev.codex_model ?? null,
  };
}

function sessionsEquivalent(left, right) {
  if (!left || !right) return false;
  if (left.id && right.id && left.id === right.id) return true;
  const leftCodex = String(left.codex_session_id || "").trim();
  const rightCodex = String(right.codex_session_id || "").trim();
  if (leftCodex && rightCodex && leftCodex === rightCodex) return true;
  const leftTmux = String(left.tmux_session || "").trim();
  const rightTmux = String(right.tmux_session || "").trim();
  return !!leftTmux && !!rightTmux && leftTmux === rightTmux;
}

function ensureArrayResult(data, label) {
  if (Array.isArray(data)) return data;
  throw new Error(`${label} 返回了非列表结果，请确认页面是否通过 nginx /api 代理访问。`);
}

function shouldStreamStatus(status) {
  return ["running", "waiting_input", "waiting_approval"].includes(String(status || "").trim().toLowerCase());
}

function shouldCollapseMessage(text, maxChars = 320, maxLines = 8) {
  const value = String(text || "").trim();
  if (!value) return false;
  const lines = value.split(/\n+/).filter(Boolean).length;
  return value.length > maxChars || lines > maxLines;
}

function repoSessionPriority(repo) {
  const status = String(repo?.session_status || "").trim().toLowerCase();
  if (["running", "waiting_input", "waiting_approval"].includes(status)) return 3;
  if (status === "completed") return 2;
  if (status === "failed") return 1;
  return 0;
}

export function App() {
  const initialNavRef = useRef(parseInitialNav());
  const timelineRef = useRef(null);
  const logRef = useRef(null);
  const promptRef = useRef(null);
  const streamRef = useRef(null);
  const composerShellRef = useRef(null);
  const navRef = useRef(null);
  const currentRepoRef = useRef(null);
  const currentSessionRef = useRef(null);
  const timelineEventIdsRef = useRef(new Set());
  const [repos, setRepos] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [sessionHub, setSessionHub] = useState(null);
  const [currentRepo, setCurrentRepo] = useState(null);
  const [currentSession, setCurrentSession] = useState(null);
  const [sessionLoadToken, setSessionLoadToken] = useState(0);
  const [status, setStatus] = useState("idle");
  const [timelineEvents, setTimelineEvents] = useState([]);
  const [pendingTimelinePrompts, setPendingTimelinePrompts] = useState([]);
  const [rawLogLines, setRawLogLines] = useState([]);
  const [activePage, setActivePage] = useState(initialNavRef.current.page || "chat");
  const [activeSheet, setActiveSheet] = useState("none");
  const [workspaceTab, setWorkspaceTab] = useState("changes");
  const [composerToolsOpen, setComposerToolsOpen] = useState(false);
  const [approvalRequest, setApprovalRequest] = useState(null);
  const [logOpen, setLogOpen] = useState(false);
  const [keypadOpen, setKeypadOpen] = useState(false);
  const [logUnread, setLogUnread] = useState(0);
  const [showJumpBottom, setShowJumpBottom] = useState(false);
  const [sendingPrompt, setSendingPrompt] = useState(false);
  const [promptValue, setPromptValue] = useState("");
  const [sessionHint, setSessionHint] = useState("");
  const [createProjectMsg, setCreateProjectMsg] = useState({ text: "", error: false });
  const [runOutput, setRunOutput] = useState("");
  const [changesFiles, setChangesFiles] = useState([]);
  const [diffView, setDiffView] = useState("");
  const [recentFiles, setRecentFiles] = useState([]);
  const [treeFiles, setTreeFiles] = useState([]);
  const [filePath, setFilePath] = useState("");
  const [fileContent, setFileContent] = useState("");
  const [createProjectForm, setCreateProjectForm] = useState({
    name: "",
    githubOwner: "",
    githubRepo: "",
    createGithub: true,
    privateRepo: true,
  });
  const initialLayout = getChatLayout();
  const [chatCompact, setChatCompact] = useState(initialLayout.compact);
  const [chatFontSize, setChatFontSize] = useState(initialLayout.fontSize);
  const [customQuickInputs, setCustomQuickInputs] = useState(buildCustomQuickInputs([]));

  currentRepoRef.current = currentRepo;
  currentSessionRef.current = currentSession;

  const repoId = currentRepo?.id || "";
  const sessionId = currentSession?.id || "";
  const preferredExecutionMode = getPreferredExecutionMode(repoId);
  const customQuickPrompts = getCustomQuickPrompts(repoId).filter((item) => item.label && item.template);
  const quickPrompts = [...QUICK_PROMPT_DEFAULTS, ...customQuickPrompts.map((item) => ({ ...item, custom: true }))];
  const pendingEvents = pendingTimelinePrompts.map((item) => ({
    id: item.id,
    kind: "user_message",
    title: "你",
    text: item.text,
    timestamp: item.timestamp,
    pending: true,
  }));
  const conversationItems = buildConversationItems([...timelineEvents, ...pendingEvents]);
  const displayLogLines = buildDisplayLogLines(rawLogLines);
  const sessionHubView = buildSessionHubView(sessionHub, sessions);
  const suggestedSession = sessionHubView.suggested;
  const currentSessionCard = currentSession || sessionHubView.focus || null;
  const displaySuggestedSession = suggestedSession && !sessionsEquivalent(suggestedSession, currentSessionCard) ? suggestedSession : null;
  const displaySharedPrimarySession =
    sessionHubView.shared &&
    !sessionsEquivalent(sessionHubView.shared, displaySuggestedSession) &&
    !sessionsEquivalent(sessionHubView.shared, currentSessionCard)
      ? sessionHubView.shared
      : null;
  const recentPrimarySessions = sessionHubView.recent.filter((session) => !sessionsEquivalent(session, displaySharedPrimarySession));
  const externalRecentSessions = sessionHubView.externalRecent.filter((session) => !sessionsEquivalent(session, displaySuggestedSession));
  const archivedSessions = sessionHubView.archived;
  const latestPreview = latestLogPreview(rawLogLines, 3);
  const typingInfo = typingStatusMessage(status);
  const headerTitle = currentRepo ? (activePage === "chat" ? currentRepo.name : `${currentRepo.name} · ${PAGE_LABELS[activePage]}`) : "Codex Mobile";
  const composerStatusText = resolveComposerStatus({ sendingPrompt, currentSession, status });
  const chatPrimaryLabel = sendingPrompt ? "发送中" : promptValue.trim() ? "发送" : "继续";

  useEffect(() => {
    document.documentElement.style.setProperty("--chat-font-size", `${chatFontSize}px`);
    document.body.classList.toggle("chat-compact", chatCompact);
    setChatLayout({ compact: chatCompact, fontSize: chatFontSize });
  }, [chatCompact, chatFontSize]);

  useEffect(() => {
    const prompt = promptRef.current;
    if (!prompt) return;
    prompt.style.height = "auto";
    prompt.style.height = `${Math.min(148, Math.max(56, prompt.scrollHeight))}px`;
  }, [promptValue]);

  useEffect(() => {
    const updateShellHeights = () => {
      const composerHeight = composerShellRef.current?.offsetHeight || 0;
      const navHeight = navRef.current?.offsetHeight || 0;
      document.documentElement.style.setProperty("--composer-height", `${composerHeight}px`);
      document.documentElement.style.setProperty("--nav-height", `${navHeight}px`);
    };

    updateShellHeights();
    window.addEventListener("resize", updateShellHeights);

    if (typeof ResizeObserver === "undefined") {
      return () => window.removeEventListener("resize", updateShellHeights);
    }

    const observer = new ResizeObserver(updateShellHeights);
    if (composerShellRef.current) observer.observe(composerShellRef.current);
    if (navRef.current) observer.observe(navRef.current);

    return () => {
      observer.disconnect();
      window.removeEventListener("resize", updateShellHeights);
    };
  }, []);

  useEffect(() => {
    const query = new URLSearchParams();
    if (repoId) query.set("repo", repoId);
    if (sessionId) query.set("session", sessionId);
    if (activePage && activePage !== "chat") query.set("page", activePage);
    const next = `${window.location.pathname}${query.toString() ? `?${query.toString()}` : ""}`;
    const current = `${window.location.pathname}${window.location.search}`;
    if (next !== current) {
      window.history.replaceState(null, "", next);
    }
  }, [activePage, repoId, sessionId]);

  useEffect(() => {
    setPromptValue(getPromptDraft(repoId, sessionId));
  }, [repoId, sessionId]);

  useEffect(() => {
    setPromptDraft(repoId, sessionId, promptValue);
  }, [promptValue, repoId, sessionId]);

  useEffect(() => {
    setCustomQuickInputs(buildCustomQuickInputs(getCustomQuickPrompts(repoId)));
  }, [repoId]);

  useEffect(() => {
    if (logOpen) {
      setLogUnread(0);
    }
  }, [logOpen]);

  useEffect(() => {
    if (!composerToolsOpen) return;
    if (activePage !== "chat") {
      setComposerToolsOpen(false);
    }
  }, [activePage, composerToolsOpen]);

  useEffect(() => {
    const handleKeydown = (event) => {
      if (event.key === "Escape" && activeSheet !== "none") {
        event.preventDefault();
        setActiveSheet("none");
        return;
      }

      if (activePage !== "chat") return;
      const tag = String(event.target?.tagName || "").toLowerCase();
      const inInput = tag === "textarea" || tag === "input" || event.target?.isContentEditable;
      if (inInput) return;

      const keyMap = {
        ArrowUp: "Up",
        ArrowDown: "Down",
        ArrowLeft: "Left",
        ArrowRight: "Right",
        Enter: "Enter",
        Tab: "Tab",
        Escape: "Escape",
        Backspace: "Backspace",
      };
      let controlKey = keyMap[event.key];
      if (!controlKey && event.ctrlKey && (event.key === "c" || event.key === "C")) {
        controlKey = "Ctrl+C";
      }
      if (!controlKey) return;
      event.preventDefault();
      sendControlKey(controlKey).catch(handleChatError);
    };

    document.addEventListener("keydown", handleKeydown);
    return () => document.removeEventListener("keydown", handleKeydown);
  }, [activePage, activeSheet]);

  useEffect(() => {
    let cancelled = false;

    async function init() {
      try {
        const repoRows = await loadRepos();
        if (cancelled || !repoRows.length) return;
        const navRepoId = initialNavRef.current.repoId;
        const storedRepoId = getActiveRepoId();
        const initialRepo =
          repoRows.find((repo) => repo.id === navRepoId) ||
          repoRows.find((repo) => repo.id === storedRepoId) ||
          repoRows.find((repo) => repo.is_default) ||
          [...repoRows].sort((left, right) => {
            const bySession = repoSessionPriority(right) - repoSessionPriority(left);
            if (bySession) return bySession;
            return String(right.updated_at || "").localeCompare(String(left.updated_at || ""));
          })[0];
        await switchRepo(initialRepo, {
          preferredSessionId: initialNavRef.current.sessionId || "",
          autoResume: false,
          closeSheet: false,
        });
      } catch (error) {
        handleChatError(error);
      }
    }

    init();
    return () => {
      cancelled = true;
      closeStream();
    };
  }, []);

  useEffect(() => {
    if (!repoId) return undefined;
    let cancelled = false;
    let timer = null;
    let inFlight = false;

    const nextDelay = () => (document.visibilityState === "visible" ? ACTIVE_REPO_POLL_MS : BACKGROUND_REPO_POLL_MS);

    const schedule = (delay) => {
      if (cancelled) return;
      timer = window.setTimeout(runPoll, delay);
    };

    const runPoll = async (force = true) => {
      if (cancelled || inFlight) {
        schedule(nextDelay());
        return;
      }
      inFlight = true;
      try {
        await loadSessionsForRepo(repoId, { force });
      } catch (_error) {
        // Ignore transient polling failures.
      } finally {
        inFlight = false;
        schedule(nextDelay());
      }
    };

    const wakePoll = () => {
      if (cancelled) return;
      if (timer) window.clearTimeout(timer);
      runPoll(true).catch(() => {
        // Ignore transient polling failures.
      });
    };

    schedule(nextDelay());
    document.addEventListener("visibilitychange", wakePoll);
    window.addEventListener("focus", wakePoll);
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", wakePoll);
      window.removeEventListener("focus", wakePoll);
    };
  }, [repoId]);

  useEffect(() => {
    if (!sessionId) {
      closeStream();
      return undefined;
    }

    let cancelled = false;

    async function connect() {
      try {
        const data = await api(`/api/sessions/${sessionId}/snapshot?lines=${SNAPSHOT_LOG_LINES}&timeline_limit=${SNAPSHOT_TIMELINE_LIMIT}`);
        if (cancelled || currentSessionRef.current?.id !== sessionId) return;
        replaceTimeline(data.timeline || []);
        setRawLogLines(Array.isArray(data.lines) ? data.lines.slice(-MAX_LOG_LINES).map(normalizeLogLine) : []);
        const nextStatus = data.status || currentSessionRef.current?.status || "idle";
        setStatus(nextStatus);
        setCurrentSession((prev) => (prev && prev.id === sessionId ? mergeSession(prev, data) : prev));
        setSessionHint("已同步");

        const pendingPrompt = getPendingPrompt(sessionId);
        setPromptValue((prev) => (prev.trim() ? prev : pendingPrompt));

        if (!shouldStreamStatus(nextStatus)) {
          closeStream();
          return;
        }

        closeStream();
        const stream = new EventSource(apiUrl(`/api/sessions/${sessionId}/stream?seed_lines=0`));
        streamRef.current = stream;

        stream.addEventListener("status", (event) => {
          const payload = JSON.parse(event.data);
          if (payload.session_id !== currentSessionRef.current?.id) return;
          setStatus(payload.status || "idle");
          setCurrentSession((prev) => (prev && prev.id === payload.session_id ? { ...prev, status: payload.status } : prev));
        });

        stream.addEventListener("message", (event) => {
          const payload = JSON.parse(event.data);
          appendLogBlock(payload.line || "");
        });

        stream.addEventListener("session_meta", (event) => {
          const payload = JSON.parse(event.data);
          if (payload.session_id !== currentSessionRef.current?.id) return;
          setCurrentSession((prev) => (prev && prev.id === payload.session_id ? mergeSession(prev, payload) : prev));
        });

        stream.addEventListener("timeline_event", (event) => {
          const payload = JSON.parse(event.data);
          appendTimelineEvent(payload);
        });

        stream.addEventListener("approval_request", (event) => {
          const payload = JSON.parse(event.data);
          if (payload.session_id !== currentSessionRef.current?.id) return;
          setApprovalRequest(payload);
          setStatus("waiting_approval");
          setCurrentSession((prev) => (prev && prev.id === payload.session_id ? { ...prev, status: "waiting_approval" } : prev));
          setSessionHint("等待批准");
        });

        stream.addEventListener("error", () => {
          // Browser EventSource handles reconnects automatically.
        });
      } catch (error) {
        handleChatError(error);
      }
    }

    connect();
    return () => {
      cancelled = true;
      closeStream();
    };
  }, [sessionId, sessionLoadToken]);

  useEffect(() => {
    const timeline = timelineRef.current;
    if (!timeline) return;
    if (conversationItems.length === 0) return;
    const nearBottom = timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight <= 140;
    if (nearBottom) {
      timeline.scrollTop = timeline.scrollHeight;
      setShowJumpBottom(false);
    }
  }, [conversationItems.length]);

  async function loadRepos() {
    const repoRows = ensureArrayResult(await api("/api/repos"), "/api/repos");
    setRepos(repoRows);
    return repoRows;
  }

  async function loadSessionsForRepo(targetRepoId, options = {}) {
    if (!targetRepoId) return [];
    const query = options.force ? "?force=1" : "";
    const hub = await api(`/api/repos/${targetRepoId}/session-hub${query}`);
    const sessionRows = ensureArrayResult(
      hub?.sessions,
      `/api/repos/${targetRepoId}/session-hub${query}`,
    );
    if (currentRepoRef.current?.id !== targetRepoId) return sessionRows;
    setSessionHub(hub);
    setSessions(sessionRows);
    const prevCurrent = currentSessionRef.current;
    if (prevCurrent?.id) {
      const fresh = sessionRows.find((item) => item.id === prevCurrent.id);
      if (fresh) {
        const merged = mergeSession(prevCurrent, fresh);
        const activityChanged =
          String(prevCurrent.status || "") !== String(merged.status || "") ||
          String(prevCurrent.updated_at || "") !== String(merged.updated_at || "") ||
          String(prevCurrent.last_activity_at || "") !== String(merged.last_activity_at || "") ||
          String(prevCurrent.tmux_session || "") !== String(merged.tmux_session || "");
        currentSessionRef.current = merged;
        setCurrentSession(merged);
        setStatus(merged.status || "idle");
        if (!streamRef.current && activityChanged) {
          setSessionLoadToken((prev) => prev + 1);
        }
      }
    }
    return sessionRows;
  }

  async function markSessionFocus(targetSessionId, reason = "manual") {
    if (!targetSessionId) return;
    try {
      await api(`/api/sessions/${targetSessionId}/focus`, {
        method: "POST",
        body: JSON.stringify({ reason }),
      });
      setSessionHub((prev) =>
        prev
          ? {
              ...prev,
              current_session_id: targetSessionId,
              focus_session_id: targetSessionId,
              focus_reason: reason,
              focus_updated_at: new Date().toISOString(),
            }
          : prev,
      );
    } catch (_error) {
      // Best effort only.
    }
  }

  function closeStream() {
    if (streamRef.current) {
      streamRef.current.close();
      streamRef.current = null;
    }
  }

  function resetSessionView(repoStatus = "idle") {
    closeStream();
    timelineEventIdsRef.current = new Set();
    setSessionHub(null);
    setCurrentSession(null);
    setSessionLoadToken((prev) => prev + 1);
    setTimelineEvents([]);
    setPendingTimelinePrompts([]);
    setRawLogLines([]);
    setApprovalRequest(null);
    setLogUnread(0);
    setLogOpen(false);
    setKeypadOpen(false);
    setSessionHint("");
    setStatus(repoStatus);
  }

  function replaceTimeline(events) {
    const terminalPromptKeys = new Set(
      (events || [])
        .filter((event) => String(event?.kind || "") === "user_message")
        .filter((event) => !String(event?.id || "").trim().startsWith("bootstrap:"))
        .map((event) => normalizeTimelineText(event?.text || ""))
        .filter(Boolean),
    );

    const unique = [];
    const seen = new Set();
    for (const event of events || []) {
      const id = String(event?.id || "").trim();
      if (!id || seen.has(id)) continue;
      if (shouldSkipBootstrapDuplicate(event, terminalPromptKeys)) continue;
      seen.add(id);
      unique.push({ ...event, id });
    }
    timelineEventIdsRef.current = seen;
    setTimelineEvents(unique);
  }

  function appendTimelineEvent(event) {
    const id = String(event?.id || "").trim();
    const nextEvent = { ...event, id };
    if (!id || timelineEventIdsRef.current.has(id)) return;
    setTimelineEvents((prev) => {
      const terminalPromptKeys = new Set(
        prev
          .filter((item) => String(item?.kind || "") === "user_message")
          .filter((item) => !String(item?.id || "").trim().startsWith("bootstrap:"))
          .map((item) => normalizeTimelineText(item?.text || ""))
          .filter(Boolean),
      );
      if (shouldSkipBootstrapDuplicate(nextEvent, terminalPromptKeys)) {
        return prev;
      }
      timelineEventIdsRef.current.add(id);
      return [...prev, nextEvent];
    });
    if (event?.kind === "user_message") {
      const text = String(event.text || "").trim();
      setPendingTimelinePrompts((prev) => prev.filter((item) => item.text !== text));
    }
  }

  function addLocalTimelineNotice(kind, title, text) {
    const message = String(text || "").trim();
    if (!message) return;
    appendTimelineEvent({
      id: `local:${Date.now()}:${Math.random().toString(16).slice(2, 8)}`,
      kind,
      title,
      text: message,
      timestamp: new Date().toISOString(),
    });
  }

  function appendLogBlock(block) {
    const lines = String(block || "")
      .split(/\r?\n/)
      .map((line) => normalizeLogLine(line))
      .filter(Boolean);
    if (!lines.length) return;
    setRawLogLines((prev) => [...prev, ...lines].slice(-MAX_LOG_LINES));
    if (!logOpen) {
      setLogUnread((prev) => Math.min(99, prev + 1));
    }
  }

  function handleChatError(error) {
    const message = error?.message || String(error);
    appendLogBlock(`[error] ${message}`);
    setStatus("failed");
    setApprovalRequest(null);
    addLocalTimelineNotice("task_aborted", "系统", message);
  }

  async function selectSession(targetSession, options = {}) {
    if (!targetSession?.id) return null;
    const session = options.mergeWithCurrent && currentSessionRef.current?.id === targetSession.id
      ? mergeSession(targetSession, currentSessionRef.current)
      : targetSession;
    setCurrentSession(session);
    currentSessionRef.current = session;
    setSessionLoadToken((prev) => prev + 1);
    setActiveSessionId(currentRepoRef.current?.id, session.id);
    setStatus(session.status || "idle");
    setSessionHint(options.hint || (shouldStreamStatus(session.status) ? "已连接" : "历史预览"));
    if (options.closeSheet) setActiveSheet("none");
    markSessionFocus(session.id, options.focusReason || "preview");
    return session;
  }

  async function switchRepo(repo, options = {}) {
    if (!repo) return;
    currentRepoRef.current = repo;
    setCurrentRepo(repo);
    setActiveRepoId(repo.id);
    setActivePage(options.focusPage || "chat");
    resetSessionView(repo.session_status || "idle");
    setPromptValue(getPromptDraft(repo.id, ""));

    const sessionRows = await loadSessionsForRepo(repo.id, { force: true });
    const currentHub = await api(`/api/repos/${repo.id}/session-hub`);
    if (currentRepoRef.current?.id === repo.id) {
      setSessionHub(currentHub);
    }
    const preferredSessionId =
      options.preferredSessionId ||
      getActiveSessionId(repo.id) ||
      String(currentHub.current_session_id || "").trim() ||
      "";
    if (preferredSessionId) {
      const found = sessionRows.find((item) => item.id === preferredSessionId);
      if (found) {
        if (options.autoResume) {
          await activateSession(found.id, { closeSheet: !!options.closeSheet, refreshSessions: false });
        } else {
          await selectSession(found, { closeSheet: !!options.closeSheet });
        }
        return;
      }
    }
    const suggested =
      sessionRows.find((item) => item.id === String(currentHub.suggested_session_id || "").trim()) ||
      sessionRows[0] ||
      null;
    if (suggested) {
      await selectSession(suggested, { closeSheet: !!options.closeSheet });
      return;
    }
    if (options.autoResume) {
      await resumeSharedSession(repo.id, { closeSheet: !!options.closeSheet, refreshSessions: false });
    }
  }

  async function activateSession(targetSessionId, options = {}) {
    if (!targetSessionId) return null;
    const session = await api(`/api/sessions/${targetSessionId}/resume`, { method: "POST" });
    setCurrentSession(session);
    currentSessionRef.current = session;
    setSessionLoadToken((prev) => prev + 1);
    setActiveSessionId(currentRepoRef.current?.id, session.id);
    setStatus(session.status || "idle");
    setSessionHint(options.hint || "");
    if (options.closeSheet) setActiveSheet("none");
    markSessionFocus(session.id, options.focusReason || "resume");
    if (options.refreshSessions !== false && currentRepoRef.current?.id) {
      await loadSessionsForRepo(currentRepoRef.current.id, { force: true });
    }
    return session;
  }

  async function resumeSharedSession(targetRepoId = currentRepoRef.current?.id, options = {}) {
    if (!targetRepoId) return null;
    const session = await api(`/api/repos/${targetRepoId}/sessions/resume`, {
      method: "POST",
      body: JSON.stringify({ execution_mode: getPreferredExecutionMode(targetRepoId) }),
    });
    setCurrentSession(session);
    currentSessionRef.current = session;
    setSessionLoadToken((prev) => prev + 1);
    setActiveSessionId(targetRepoId, session.id);
    setStatus(session.status || "idle");
    setSessionHint("共享会话已恢复");
    if (options.closeSheet) setActiveSheet("none");
    markSessionFocus(session.id, "shared_resume");
    if (options.refreshSessions !== false) {
      await loadSessionsForRepo(targetRepoId, { force: true });
    }
    return session;
  }

  async function createNewSession() {
    const targetRepoId = currentRepoRef.current?.id;
    if (!targetRepoId) return;
    const session = await api(`/api/repos/${targetRepoId}/sessions/new`, {
      method: "POST",
      body: JSON.stringify({ execution_mode: getPreferredExecutionMode(targetRepoId) }),
    });
    resetSessionView("running");
    setCurrentSession(session);
    currentSessionRef.current = session;
    setSessionLoadToken((prev) => prev + 1);
    setActiveSessionId(targetRepoId, session.id);
    setStatus(session.status || "running");
    setSessionHint("新会话");
    setActiveSheet("none");
    markSessionFocus(session.id, "new");
    await loadSessionsForRepo(targetRepoId, { force: true });
  }

  async function ensureSession() {
    const current = currentSessionRef.current;
    if (current?.id) {
      if (shouldStreamStatus(current.status)) return current;
      if (currentRepoRef.current?.id) {
        if (isSharedSession(current)) {
          return resumeSharedSession(currentRepoRef.current.id, { refreshSessions: true });
        }
        try {
          return await activateSession(current.id, { refreshSessions: true });
        } catch (error) {
          const message = String(error?.message || "").toLowerCase();
          const staleSession = message.includes("tmux session not found") || message.includes("no longer running in tmux");
          if (!staleSession) throw error;
          addLocalTimelineNotice(
            "commentary",
            "系统",
            "当前查看的是历史会话，原执行终端已经结束；已自动切回项目主会话，继续从可恢复入口发送。",
          );
          return resumeSharedSession(currentRepoRef.current.id, { refreshSessions: true });
        }
      }
    }
    if (!currentRepoRef.current?.id) return null;
    return resumeSharedSession(currentRepoRef.current.id, { refreshSessions: true });
  }

  async function sendPrompt(overrideText = "") {
    if (sendingPrompt) return;
    const prompt = String(overrideText || promptValue).trim();
    if (!prompt) {
      return sendQuickPrompt("继续推进当前任务，直接实施下一步并汇报结果。");
    }

    const session = await ensureSession();
    if (!session?.id) return;

    setPendingPrompt(session.id, prompt);
    setPendingTimelinePrompts((prev) => [
      ...prev,
      { id: `pending:${Date.now()}:${Math.random().toString(16).slice(2, 8)}`, text: prompt, timestamp: new Date().toISOString() },
    ]);

    setSendingPrompt(true);
    try {
      await api(`/api/sessions/${session.id}/prompt`, {
        method: "POST",
        body: JSON.stringify({ prompt }),
      });
      setPendingTimelinePrompts((prev) => prev.filter((item) => item.text !== prompt));
      clearPendingPrompt(session.id);
      setLastPrompt(currentRepoRef.current?.id, prompt);
      setPromptValue("");
      setCurrentSession((prev) => (prev && prev.id === session.id ? { ...prev, status: "running" } : prev));
      setStatus("running");
      setApprovalRequest(null);
      setSessionHint("已发送任务，Codex 会自动开始执行");
    } catch (error) {
      setPendingTimelinePrompts((prev) => prev.filter((item) => item.text !== prompt));
      setPromptValue((prev) => (prev.trim() ? prev : prompt));
      throw error;
    } finally {
      setSendingPrompt(false);
    }
  }

  async function sendQuickPrompt(template) {
    return sendPrompt(template);
  }

  async function resendLastPrompt() {
    const lastPrompt = getLastPrompt(currentRepoRef.current?.id);
    if (!lastPrompt) {
      throw new Error("当前项目没有可重发的上一条任务");
    }
    return sendPrompt(lastPrompt);
  }

  async function sendControlKey(key, repeat = 1) {
    const session = await ensureSession();
    if (!session?.id) return;
    await api(`/api/sessions/${session.id}/key`, {
      method: "POST",
      body: JSON.stringify({ key, repeat }),
    });
  }

  async function pauseCurrentTask() {
    await sendControlKey("Escape", 1);
    addLocalTimelineNotice(
      "commentary",
      "已请求暂停",
      "已向 Codex 发送 Esc，当前步骤会尽量暂停或退出当前交互。\n如仍在运行，可继续点“停止”。",
    );
    setSessionHint("已发送暂停指令");
  }

  async function stopCurrentTask() {
    await sendControlKey("Ctrl+C", 1);
    addLocalTimelineNotice(
      "task_aborted",
      "已请求停止",
      "已向 Codex 发送 Ctrl+C，中止当前这轮执行。\n如果还有残留步骤，可以再发一条新消息继续。",
    );
    setStatus("failed");
    setCurrentSession((prev) => (prev ? { ...prev, status: "failed" } : prev));
    setSessionHint("已发送停止指令");
  }

  async function approveDecision(approve) {
    const current = currentSessionRef.current;
    if (!current?.id) return;
    await api(`/api/sessions/${current.id}/${approve ? "approve" : "reject"}`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    setApprovalRequest(null);
  }

  async function syncChat() {
    const targetRepoId = currentRepoRef.current?.id;
    if (!targetRepoId) return;
    const sessionRows = await loadSessionsForRepo(targetRepoId);
    const current = currentSessionRef.current;
    if (current?.id) {
      const data = await api(`/api/sessions/${current.id}/snapshot?lines=${SNAPSHOT_LOG_LINES}&timeline_limit=${SNAPSHOT_TIMELINE_LIMIT}`);
      replaceTimeline(data.timeline || []);
      setRawLogLines(Array.isArray(data.lines) ? data.lines.slice(-MAX_LOG_LINES).map(normalizeLogLine) : []);
      setStatus(data.status || current.status || "idle");
      setCurrentSession((prev) => (prev && prev.id === current.id ? mergeSession(prev, data) : prev));
    } else {
      const latestHub = await api(`/api/repos/${targetRepoId}/session-hub`);
      if (currentRepoRef.current?.id === targetRepoId) {
        setSessionHub(latestHub);
      }
      const suggested = sessionRows.find((item) => item.id === String(latestHub?.suggested_session_id || "").trim()) || sessionRows[0] || null;
      if (suggested) {
        await selectSession(suggested, { mergeWithCurrent: true });
      }
    }
  }

  async function switchToLatestSession() {
    const target =
      sessions.find((item) => item.id === String(sessionHub?.suggested_session_id || "").trim()) ||
      sessionHubView.focus ||
      sessions[0] ||
      null;
    if (!target) return;
    if (isSharedSession(target)) {
      await resumeSharedSession(currentRepoRef.current?.id, { closeSheet: true, refreshSessions: true });
    } else {
      await activateSession(target.id, { closeSheet: true, refreshSessions: true });
    }
    setActivePage("chat");
  }

  async function previewSession(session, options = {}) {
    await selectSession(session, { closeSheet: !!options.closeSheet, hint: "历史预览", focusReason: "preview" });
    setActivePage("chat");
  }

  async function continueSession(session, options = {}) {
    if (!session?.id && !isSharedSession(session)) return;
    if (isSharedSession(session)) {
      await resumeSharedSession(currentRepoRef.current?.id, { closeSheet: !!options.closeSheet, refreshSessions: true });
    } else {
      await activateSession(session.id, { closeSheet: !!options.closeSheet, refreshSessions: true });
    }
    setActivePage("chat");
  }

  async function renameCurrentSession() {
    const current = currentSessionRef.current;
    if (!current?.id) throw new Error("请先选择会话");
    const nextName = window.prompt("输入会话名称", (current.name || "").trim());
    if (nextName == null) return;
    const name = nextName.trim();
    if (!name) throw new Error("会话名称不能为空");
    const updated = await api(`/api/sessions/${current.id}/rename`, {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    setCurrentSession(updated);
    setSessionHint("已重命名");
    await loadSessionsForRepo(currentRepoRef.current?.id);
  }

  async function copySessionLink() {
    if (!currentRepoRef.current?.id || !currentSessionRef.current?.id) throw new Error("请先进入一个会话");
    const query = new URLSearchParams();
    query.set("repo", currentRepoRef.current.id);
    query.set("session", currentSessionRef.current.id);
    query.set("page", "chat");
    const url = `${currentUrlBase()}?${query.toString()}`;
    try {
      await navigator.clipboard.writeText(url);
      setSessionHint("会话链接已复制");
    } catch (_error) {
      window.prompt("复制会话链接", url);
    }
  }

  async function createProject() {
    const payload = {
      name: createProjectForm.name.trim(),
      github_owner: createProjectForm.githubOwner.trim() || null,
      github_repo: createProjectForm.githubRepo.trim() || null,
      create_github_repo: createProjectForm.createGithub,
      private: createProjectForm.privateRepo,
      push_initial_commit: true,
    };
    if (!payload.name) {
      setCreateProjectMsg({ text: "请输入项目名", error: true });
      return;
    }

    setCreateProjectMsg({ text: "正在创建项目...", error: false });
    try {
      const result = await api("/api/projects/init", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const repoRows = await loadRepos();
      const nextRepo = repoRows.find((repo) => repo.id === result.repo.id) || result.repo;
      setCreateProjectForm({
        name: "",
        githubOwner: "",
        githubRepo: "",
        createGithub: true,
        privateRepo: true,
      });
      setCreateProjectMsg({
        text: `创建成功: ${result.repo.name}${result.github_url ? ` | ${result.github_url}` : ""}`,
        error: false,
      });
      await switchRepo(nextRepo, { autoResume: false, closeSheet: true, focusPage: "chat" });
    } catch (error) {
      setCreateProjectMsg({ text: `创建失败: ${error.message}`, error: true });
    }
  }

  async function refreshChanges() {
    if (!currentRepoRef.current?.id) return;
    const statusData = await api(`/api/repos/${currentRepoRef.current.id}/git/status`);
    const dirtyFiles = statusData.dirty_files || [];
    setChangesFiles(dirtyFiles);
    const diffData = await api(`/api/repos/${currentRepoRef.current.id}/git/diff`);
    setDiffView(String(diffData.diff || "").slice(0, 150000));
  }

  async function openDiff(path) {
    if (!currentRepoRef.current?.id) return;
    const diffData = await api(`/api/repos/${currentRepoRef.current.id}/git/diff/${encodeURIComponent(path)}`);
    setDiffView(diffData.diff || "");
  }

  async function refreshFiles() {
    if (!currentRepoRef.current?.id) return;
    const recentData = await api(`/api/repos/${currentRepoRef.current.id}/files/recent`);
    setRecentFiles(Array.isArray(recentData.files) ? recentData.files.slice(0, 20) : []);
    const treeData = await api(`/api/repos/${currentRepoRef.current.id}/files/tree?path=.&depth=2`);
    setTreeFiles(Array.isArray(treeData.items) ? flattenTree(treeData.items).slice(0, 120) : []);
  }

  async function openFile(path) {
    if (!currentRepoRef.current?.id) return;
    const fileData = await api(`/api/repos/${currentRepoRef.current.id}/file?path=${encodeURIComponent(path)}`);
    setFilePath(fileData.path || "");
    setFileContent(fileData.content || "");
    setWorkspaceTab("files");
    setActiveSheet("workspace");
  }

  async function saveFile() {
    if (!currentRepoRef.current?.id || !filePath) return;
    await api(`/api/repos/${currentRepoRef.current.id}/file`, {
      method: "PUT",
      body: JSON.stringify({ path: filePath, content: fileContent }),
    });
    window.alert("保存成功");
  }

  async function runCommand(command) {
    if (!currentRepoRef.current?.id) return;
    const result = await api(`/api/repos/${currentRepoRef.current.id}/run/cmd`, {
      method: "POST",
      body: JSON.stringify({ cmd: command }),
    });
    setRunOutput(`exit=${result.code}\n\n${result.stdout || ""}\n${result.stderr || ""}`);
    setWorkspaceTab("run");
    setActiveSheet("workspace");
  }

  function openWorkspace(targetTab = "changes") {
    setWorkspaceTab(targetTab);
    setActiveSheet("workspace");
  }

  function onTimelineScroll() {
    const element = timelineRef.current;
    if (!element) return;
    const hidden = element.scrollHeight - element.scrollTop - element.clientHeight <= 140;
    setShowJumpBottom(!hidden);
  }

  function scrollTimelineToBottom(smooth = false) {
    const element = timelineRef.current;
    if (!element) return;
    element.scrollTo({ top: element.scrollHeight, behavior: smooth ? "smooth" : "auto" });
    setShowJumpBottom(false);
  }

  async function copyText(text) {
    const value = String(text || "").trim();
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
    } catch (_error) {
      window.prompt("复制消息", value);
    }
  }

  function updateExecutionMode(mode) {
    setPreferredExecutionMode(repoId, mode);
    setSessionHint(`默认模式已切到${executionModeLabel(mode)}`);
    setCurrentRepo((prev) => (prev ? { ...prev } : prev));
  }

  function saveCustomQuickEditor() {
    const items = customQuickInputs
      .map((item) => ({
        label: item.label.trim(),
        template: item.template.trim(),
      }))
      .filter((item) => item.label && item.template);
    setCustomQuickPrompts(repoId, items);
    setCustomQuickInputs(buildCustomQuickInputs(items));
  }

  function resetCustomQuickEditor() {
    setCustomQuickPrompts(repoId, []);
    setCustomQuickInputs(buildCustomQuickInputs([]));
  }

  const secondaryComposerTools = [
    {
      label: logOpen ? "收起日志" : "日志",
      onClick: () => {
        setLogOpen((prev) => !prev);
        setKeypadOpen(false);
      },
    },
    {
      label: keypadOpen ? "收起控制键" : "控制键",
      onClick: () => {
        setKeypadOpen((prev) => !prev);
        setLogOpen(false);
      },
    },
    { label: "暂停", onClick: () => pauseCurrentTask().catch(handleChatError) },
    { label: "停止", onClick: () => stopCurrentTask().catch(handleChatError), danger: true },
    { label: "清空", onClick: () => setPromptValue("") },
  ];

  return (
    <>
      <div className={`sheet-overlay${activeSheet === "none" ? " hidden" : ""}`} onClick={() => setActiveSheet("none")} />

      <aside className={`sheet side-sheet ${activeSheet === "switcher" ? "open" : "hidden"}`} aria-hidden={activeSheet !== "switcher"}>
        <div className="sheet-handle" aria-hidden="true" />
        <div className="sheet-head">
          <div>
            <div className="eyebrow">Workspace</div>
            <h2>项目与会话</h2>
          </div>
          <button className="icon-btn" type="button" aria-label="关闭" onClick={() => setActiveSheet("none")}>×</button>
        </div>

        <section className="sheet-section">
          <div className="section-title">页面入口</div>
          <div className="section-copy">聊天优先，其他能力从这里切入。</div>
          <div className="stack-actions two-up">
            <button type="button" onClick={() => { setActivePage("chat"); setActiveSheet("none"); }}>回到对话</button>
            <button type="button" onClick={() => { setActivePage("workspace"); setActiveSheet("none"); }}>工作区</button>
          </div>
          <div className="stack-actions">
            <button type="button" onClick={() => { setActivePage("settings"); setActiveSheet("none"); }}>设置</button>
          </div>
        </section>

        <section className="sheet-section">
          <div className="section-head">
            <div>
              <div className="section-title">当前项目</div>
              <div className="section-copy">切换仓库、恢复共享会话，或开启一个临时会话。</div>
            </div>
          </div>
          <select
            value={repoId}
            onChange={(event) => {
              const repo = repos.find((item) => item.id === event.target.value);
              if (repo) {
                switchRepo(repo, { autoResume: false, closeSheet: false }).catch(handleChatError);
              }
            }}
          >
            {repos.map((repo) => (
              <option key={repo.id} value={repo.id}>
                {repo.name} ({repo.branch})
              </option>
            ))}
          </select>
          <div className="stack-actions two-up">
            <button className="primary" type="button" onClick={() => resumeSharedSession().catch(handleChatError)}>继续主会话</button>
            <button type="button" onClick={() => createNewSession().catch(handleChatError)}>新建临时会话</button>
          </div>
        </section>

        <section className="sheet-section">
          <div className="section-head">
            <div>
              <div className="section-title">最近项目</div>
            </div>
          </div>
          <div className="card-stack">
            {repos.map((repo) => (
              <button
                key={repo.id}
                className={`repo-card${repo.id === repoId ? " active" : ""}`}
                type="button"
                onClick={() => switchRepo(repo, { autoResume: false, closeSheet: true }).catch(handleChatError)}
              >
                <span className="preview-label">{repo.branch}</span>
                <strong>{repo.name}</strong>
                <small>{repo.path}</small>
                <small>{repo.dirty_files} 个改动 · {STATUS_LABELS[repo.session_status] || repo.session_status || "空闲"}</small>
              </button>
            ))}
          </div>
        </section>

        <section className="sheet-section">
          <div className="section-head">
            <div>
              <div className="section-title">会话</div>
              <div className="section-copy">先告诉你当前打开的是哪条，再给服务器推荐；同一条 Codex 线程的重复入口会自动折叠。</div>
              {sessionHubView.syncHint ? <div className="section-copy">{sessionHubView.syncHint}</div> : null}
            </div>
            <button className="ghost-btn small-btn" type="button" onClick={() => loadSessionsForRepo(repoId, { force: true }).catch(handleChatError)}>刷新</button>
          </div>
          {currentSessionCard ? (
            <div className="session-spotlight active">
              <div className="approval-copy">
                <div className="approval-label">{initialNavRef.current.sessionId && currentSessionCard.id === initialNavRef.current.sessionId ? "当前打开（链接指定）" : "当前打开"}</div>
                <div className="session-spotlight-title">{sessionDisplayTitle(currentSessionCard)}</div>
                <div className="session-item-sub">
                  {(currentSessionCard.execution_mode ? `${executionModeLabel(currentSessionCard.execution_mode)} · ` : "")}
                  {STATUS_LABELS[currentSessionCard.status] || currentSessionCard.status} · {sessionActivityText(currentSessionCard)}
                </div>
                <div className="session-spotlight-copy">{sessionPreviewText(currentSessionCard)}</div>
              </div>
              <div className="stack-actions two-up">
                <button className="primary" type="button" onClick={() => continueSession(currentSessionCard, { closeSheet: true }).catch(handleChatError)}>继续这个会话</button>
                <button type="button" onClick={() => copySessionLink().catch(handleChatError)}>复制链接</button>
              </div>
            </div>
          ) : null}
          {displaySuggestedSession ? (
            <div className="session-spotlight">
              <div className="approval-copy">
                <div className="approval-label">服务器推荐</div>
                <div className="session-spotlight-title">{sessionDisplayTitle(displaySuggestedSession)}</div>
                <div className="session-item-sub">
                  {(displaySuggestedSession.execution_mode ? `${executionModeLabel(displaySuggestedSession.execution_mode)} · ` : "")}
                  {STATUS_LABELS[displaySuggestedSession.status] || displaySuggestedSession.status} · {sessionActivityText(displaySuggestedSession)}
                </div>
                <div className="session-spotlight-copy">{sessionPreviewText(displaySuggestedSession)}</div>
              </div>
              <div className="stack-actions two-up">
                <button className="primary" type="button" onClick={() => continueSession(displaySuggestedSession, { closeSheet: true }).catch(handleChatError)}>继续这个会话</button>
                <button type="button" onClick={() => previewSession(displaySuggestedSession, { closeSheet: true }).catch(handleChatError)}>只看历史</button>
              </div>
            </div>
          ) : null}
          {displaySharedPrimarySession ? (
            <div className="session-secondary-card">
              <div className="approval-copy">
                <div className="approval-label">项目主会话</div>
                <div className="session-spotlight-title">{sessionDisplayTitle(displaySharedPrimarySession)}</div>
                <div className="session-item-sub">
                  {(displaySharedPrimarySession.execution_mode ? `${executionModeLabel(displaySharedPrimarySession.execution_mode)} · ` : "")}
                  {STATUS_LABELS[displaySharedPrimarySession.status] || displaySharedPrimarySession.status} · {sessionActivityText(displaySharedPrimarySession)}
                </div>
                <div className="session-spotlight-copy">{sessionPreviewText(displaySharedPrimarySession)}</div>
              </div>
              <div className="stack-actions two-up">
                <button type="button" onClick={() => continueSession(displaySharedPrimarySession, { closeSheet: true }).catch(handleChatError)}>继续主会话</button>
                <button type="button" onClick={() => previewSession(displaySharedPrimarySession, { closeSheet: true }).catch(handleChatError)}>只看历史</button>
              </div>
            </div>
          ) : null}
          {externalRecentSessions.length ? (
            <div className="session-external-strip">
              <div className="approval-label">Mac / 外部最近活动</div>
              <div className="session-list">
                {externalRecentSessions.map((session) => (
                  <button
                    key={session.id}
                    className={`session-item${session.id === sessionId ? " active" : ""}`}
                    type="button"
                    onClick={() => previewSession(session, { closeSheet: true }).catch(handleChatError)}
                  >
                    <div className="session-item-head">
                      <span className={`session-dot ${statusClass(session.status)}`} />
                      <span className="session-item-name">{sessionDisplayTitle(session)}</span>
                    </div>
                    <div className="session-item-sub">
                      {(session.execution_mode ? `${executionModeLabel(session.execution_mode)} · ` : "")}
                      {STATUS_LABELS[session.status] || session.status} · {sessionActivityText(session)}
                    </div>
                    <div className="session-item-sub">{sessionPreviewText(session)}</div>
                  </button>
                ))}
              </div>
            </div>
          ) : null}
          <div className="session-list">
            {recentPrimarySessions.length ? recentPrimarySessions.map((session) => (
              <button
                key={session.id}
                className={`session-item${session.id === sessionId ? " active" : ""}`}
                type="button"
                onClick={() => previewSession(session, { closeSheet: true }).catch(handleChatError)}
              >
                <div className="session-item-head">
                  <span className={`session-dot ${statusClass(session.status)}`} />
                  <span className="session-item-name">{sessionDisplayTitle(session)}</span>
                </div>
                <div className="session-item-sub">
                  {(session.execution_mode ? `${executionModeLabel(session.execution_mode)} · ` : "")}
                  {STATUS_LABELS[session.status] || session.status} · {sessionActivityText(session)}
                </div>
                <div className="session-item-sub">{sessionPreviewText(session)}</div>
              </button>
            )) : <div className="chat-system"><span>没有更多需要你手动判断的最近会话了</span></div>}
          </div>
          {archivedSessions.length ? (
            <details className="session-archive">
              <summary>归档/外部会话 ({archivedSessions.length})</summary>
              <div className="session-list">
                {archivedSessions.map((session) => (
                  <button
                    key={session.id}
                    className={`session-item archived${session.id === sessionId ? " active" : ""}`}
                    type="button"
                    onClick={() => previewSession(session, { closeSheet: true }).catch(handleChatError)}
                  >
                    <div className="session-item-head">
                      <span className={`session-dot ${statusClass(session.status)}`} />
                      <span className="session-item-name">{sessionDisplayTitle(session)}</span>
                    </div>
                    <div className="session-item-sub">
                      {(session.execution_mode ? `${executionModeLabel(session.execution_mode)} · ` : "")}
                      {STATUS_LABELS[session.status] || session.status} · {sessionActivityText(session)}
                    </div>
                    <div className="session-item-sub">{sessionPreviewText(session)}</div>
                  </button>
                ))}
              </div>
            </details>
          ) : null}
          <button
            className={`ghost-btn small-btn ${displaySuggestedSession && displaySuggestedSession.id !== sessionId ? "" : "hidden"}`}
            type="button"
            onClick={() => switchToLatestSession().catch(handleChatError)}
          >
            切到推荐会话
          </button>
          <div className="stack-actions">
            <button type="button" onClick={() => renameCurrentSession().catch(handleChatError)}>重命名会话</button>
            <button type="button" onClick={() => copySessionLink().catch(handleChatError)}>复制会话链接</button>
            <button type="button" onClick={() => resendLastPrompt().catch(handleChatError)}>重发上条</button>
          </div>
        </section>

        <details className="sheet-section create-section">
          <summary>新建项目</summary>
          <div className="create-grid">
            <input
              value={createProjectForm.name}
              placeholder="项目名，例如 remote_codex"
              onChange={(event) => setCreateProjectForm((prev) => ({ ...prev, name: event.target.value }))}
            />
            <input
              value={createProjectForm.githubOwner}
              placeholder="GitHub Owner（可选）"
              onChange={(event) => setCreateProjectForm((prev) => ({ ...prev, githubOwner: event.target.value }))}
            />
            <input
              value={createProjectForm.githubRepo}
              placeholder="GitHub Repo（可选）"
              onChange={(event) => setCreateProjectForm((prev) => ({ ...prev, githubRepo: event.target.value }))}
            />
            <label className="check-row"><input type="checkbox" checked={createProjectForm.createGithub} onChange={(event) => setCreateProjectForm((prev) => ({ ...prev, createGithub: event.target.checked }))} /> 同时创建 GitHub 仓库</label>
            <label className="check-row"><input type="checkbox" checked={createProjectForm.privateRepo} onChange={(event) => setCreateProjectForm((prev) => ({ ...prev, privateRepo: event.target.checked }))} /> 使用私有仓库</label>
            <button className="primary" type="button" onClick={() => createProject().catch(handleChatError)}>创建项目</button>
            <small style={{ color: createProjectMsg.error ? "var(--danger)" : "var(--text-soft)" }}>{createProjectMsg.text}</small>
          </div>
        </details>
      </aside>

      <section className={`sheet bottom-sheet ${activeSheet === "workspace" ? "open" : "hidden"}`} aria-hidden={activeSheet !== "workspace"}>
        <div className="sheet-handle" aria-hidden="true" />
        <div className="sheet-head">
          <div>
            <div className="eyebrow">Workspace</div>
            <h2>当前工作区</h2>
          </div>
          <button className="icon-btn" type="button" aria-label="关闭" onClick={() => setActiveSheet("none")}>×</button>
        </div>

        <div className="workspace-segments" role="tablist" aria-label="工作区">
          {["changes", "files", "run"].map((tab) => (
            <button
              key={tab}
              className={`segment${workspaceTab === tab ? " active" : ""}`}
              type="button"
              onClick={() => setWorkspaceTab(tab)}
            >
              {tab === "changes" ? "改动" : tab === "files" ? "文件" : "运行"}
            </button>
          ))}
        </div>

        <div className="workspace-scroll">
          <section className={`workspace-panel${workspaceTab === "changes" ? " active" : ""}`}>
            <div className="panel surface-panel">
              <div className="section-head">
                <div>
                  <div className="section-title">改动文件</div>
                  <div className="section-copy">围绕当前任务查看工作区变更。</div>
                </div>
                <button className="ghost-btn small-btn" type="button" onClick={() => refreshChanges().catch(handleChatError)}>刷新</button>
              </div>
              <div className="list-stack">
                {changesFiles.length ? changesFiles.map((path) => (
                  <button key={path} className="item" type="button" onClick={() => openDiff(path).catch(handleChatError)}>{path}</button>
                )) : <div className="chat-system"><span>暂无未提交改动</span></div>}
              </div>
            </div>
            <div className="panel surface-panel">
              <div className="section-title">Diff</div>
              <pre className="code-block">{diffView}</pre>
            </div>
          </section>

          <section className={`workspace-panel${workspaceTab === "files" ? " active" : ""}`}>
            <div className="panel surface-panel">
              <div className="section-head">
                <div>
                  <div className="section-title">最近文件</div>
                  <div className="section-copy">快速打开最近动过的文件。</div>
                </div>
                <button className="ghost-btn small-btn" type="button" onClick={() => refreshFiles().catch(handleChatError)}>刷新</button>
              </div>
              <div className="list-stack">
                {recentFiles.map((path) => (
                  <button key={path} className="item" type="button" onClick={() => openFile(path).catch(handleChatError)}>{path}</button>
                ))}
              </div>
            </div>
            <div className="panel surface-panel">
              <div className="section-title">文件树</div>
              <div className="list-stack">
                {treeFiles.map((path) => (
                  <button key={path} className="item" type="button" onClick={() => openFile(path).catch(handleChatError)}>{path}</button>
                ))}
              </div>
            </div>
            <div className="panel surface-panel editor-panel">
              <input value={filePath} readOnly />
              <textarea rows="16" value={fileContent} placeholder="选择文件后在这里编辑" onChange={(event) => setFileContent(event.target.value)} />
              <button className="primary" type="button" onClick={() => saveFile().catch(handleChatError)}>保存文件</button>
            </div>
          </section>

          <section className={`workspace-panel${workspaceTab === "run" ? " active" : ""}`}>
            <div className="panel surface-panel">
              <div className="section-title">常用命令</div>
              <div className="section-copy">手机上最常见的只读检查与验证入口。</div>
              <div className="chip-row">
                {["git status -sb", "pytest -q", "codex --version"].map((command) => (
                  <button key={command} type="button" onClick={() => runCommand(command).catch(handleChatError)}>
                    {command === "git status -sb" ? "Git Status" : command === "pytest -q" ? "默认测试" : "Codex 版本"}
                  </button>
                ))}
              </div>
              <pre className="code-block">{runOutput}</pre>
            </div>
          </section>
        </div>
      </section>

      <div className="app-shell">
        <header className="topbar">
          <div className="topbar-main">
            <button className="glass-btn icon-btn" type="button" aria-label="打开项目与会话" onClick={() => setActiveSheet("switcher")}>≡</button>
            <div className="title-stack">
              <div className="title">{headerTitle}</div>
              <div className="meta">{buildSessionMeta(currentSession, sessionHint)}</div>
            </div>
            <button className="glass-btn icon-btn" type="button" aria-label="同步" onClick={() => syncChat().catch(handleChatError)}>⟳</button>
          </div>
        </header>

        <main className="page-stack">
          <section className={`page${activePage === "chat" ? " active" : ""}`}>
            <div className="chat-layout">
              <div className={`approval-card${approvalRequest ? "" : " hidden"}`}>
                <div className="approval-copy">
                  <div className="approval-label">需要确认</div>
                  <div>{approvalRequest?.summary || "Codex 请求批准"}</div>
                </div>
                <div className="approval-actions">
                  <button className="primary" type="button" onClick={() => approveDecision(true).catch(handleChatError)}>批准</button>
                  <button className="ghost-btn danger-btn" type="button" onClick={() => approveDecision(false).catch(handleChatError)}>拒绝</button>
                </div>
              </div>

              <SessionHub
                currentSession={currentSessionCard}
                suggestedSession={displaySuggestedSession}
                sharedSession={displaySharedPrimarySession}
                externalRecentSessions={externalRecentSessions}
                recentSessions={recentPrimarySessions}
                syncHint={sessionHubView.syncHint}
                onPreviewSession={(session) => previewSession(session, { closeSheet: false }).catch(handleChatError)}
                onContinueSession={(session) => continueSession(session, { closeSheet: false }).catch(handleChatError)}
                onResumeShared={() => resumeSharedSession(currentRepoRef.current?.id, { closeSheet: false, refreshSessions: true }).catch(handleChatError)}
                onSwitchLatest={() => switchToLatestSession().catch(handleChatError)}
                onOpenSwitcher={() => setActiveSheet("switcher")}
                onSync={() => syncChat().catch(handleChatError)}
              />

              <div className="conversation-card">
                <div ref={timelineRef} className={`chat-timeline${conversationItems.length ? "" : " empty"}`} onScroll={onTimelineScroll}>
                  {conversationItems.length ? conversationItems.map((item) => (
                    <ConversationItem
                      key={item.id}
                      item={item}
                      onContinue={() => sendQuickPrompt("基于当前上下文继续推进当前任务，直接实施下一步并汇报结果。").catch(handleChatError)}
                      onCopy={copyText}
                    />
                  )) : (
                    <EmptyConversation
                      currentRepo={currentRepo}
                      currentSession={currentSession}
                      status={status}
                      rawLogLines={rawLogLines}
                      onQuickStart={(template) => sendQuickPrompt(template).catch(handleChatError)}
                      onPause={() => pauseCurrentTask().catch(handleChatError)}
                      onStop={() => stopCurrentTask().catch(handleChatError)}
                      onToggleLog={() => setLogOpen(true)}
                    />
                  )}
                  {conversationItems.length > 0 ? (
                    <LiveAssistantCard
                      status={status}
                      conversationItems={conversationItems}
                      fallbackTaskText={currentSession?.last_prompt || ""}
                      rawLogLines={rawLogLines}
                      onPause={() => pauseCurrentTask().catch(handleChatError)}
                      onStop={() => stopCurrentTask().catch(handleChatError)}
                      onToggleLog={() => setLogOpen((prev) => !prev)}
                    />
                  ) : null}
                </div>
                <button className={`jump-bottom${showJumpBottom ? "" : " hidden"}`} type="button" onClick={() => scrollTimelineToBottom(true)}>回到最新</button>
              </div>

              <div className={`typing-strip${typingInfo && (timelineEvents.length || pendingTimelinePrompts.length) ? "" : " hidden"}`} aria-live="polite">
                {typingInfo ? (
                  <>
                    <div className="typing-avatar" aria-hidden="true">C</div>
                    <div className="typing-copy">
                      <div className="typing-title">{typingInfo.title}</div>
                      <div className="typing-sub">{typingInfo.text}</div>
                    </div>
                    <div className="typing-dots" aria-hidden="true"><span /><span /><span /></div>
                  </>
                ) : null}
              </div>

              {logOpen ? (
                <div className="detail-card floating-detail">
                  <div className="floating-detail-head">
                    <span>执行细节</span>
                    <button className="ghost-btn small-btn" type="button" onClick={() => setLogOpen(false)}>收起</button>
                  </div>
                  <small className="floating-detail-sub">实时动态 · 已过滤界面噪声</small>
                  <div ref={logRef} className="log-view">
                    {displayLogLines.length ? (
                      <div className="log-list">
                        {displayLogLines.map((line, index) =>
                          line ? (
                            <div key={`${line}:${index}`} className={`log-line${/^\[error\]/i.test(line) ? " error" : ""}`}>
                              {/^\[error\]/i.test(line) ? line.replace(/^\[error\]\s*/i, "") : line}
                            </div>
                          ) : (
                            <div key={`sep:${index}`} className="log-sep" aria-hidden="true" />
                          ),
                        )}
                      </div>
                    ) : (
                      <div className="log-empty">
                        {status === "running" ? "Codex 正在后台执行，目前还没有值得关注的关键动态。" : "暂无关键执行动态；新的关键过程会在这里出现。"}
                      </div>
                    )}
                  </div>
                </div>
              ) : null}

              {keypadOpen ? (
                <div className="detail-card floating-detail keypad-card">
                  <div className="floating-detail-head">
                    <span>终端控制键</span>
                    <button className="ghost-btn small-btn" type="button" onClick={() => setKeypadOpen(false)}>收起</button>
                  </div>
                  <div className="keypad">
                    <div className="key-row">
                      <button className="key-btn" type="button" onClick={() => sendControlKey("Escape").catch(handleChatError)}>Esc</button>
                      <button className="key-btn" type="button" onClick={() => sendControlKey("Tab").catch(handleChatError)}>Tab</button>
                      <button className="key-btn key-enter" type="button" onClick={() => sendControlKey("Enter").catch(handleChatError)}>回车</button>
                    </div>
                    <div className="key-row">
                      <button className="key-btn" type="button" onClick={() => sendControlKey("Up").catch(handleChatError)}>↑</button>
                      <button className="key-btn" type="button" onClick={() => sendControlKey("Left").catch(handleChatError)}>←</button>
                      <button className="key-btn" type="button" onClick={() => sendControlKey("Down").catch(handleChatError)}>↓</button>
                      <button className="key-btn" type="button" onClick={() => sendControlKey("Right").catch(handleChatError)}>→</button>
                    </div>
                    <div className="key-row">
                      <button className="key-btn danger-btn" type="button" onClick={() => sendControlKey("Ctrl+C").catch(handleChatError)}>Ctrl+C</button>
                      <button className="key-btn" type="button" onClick={() => sendControlKey("Backspace").catch(handleChatError)}>退格</button>
                    </div>
                  </div>
                </div>
              ) : null}
            </div>
          </section>

          <section className={`page${activePage === "workspace" ? " active" : ""}`}>
            <div className="page-card">
              <div className="section-head">
                <div>
                  <div className="section-title">工作区入口</div>
                  <div className="section-copy">改动、文件和命令统一收进一个面板，避免干扰主对话。</div>
                </div>
                <button className="primary" type="button" onClick={() => openWorkspace("changes")}>展开工作区</button>
              </div>
              <div className="workspace-preview-grid">
                <button className="preview-card" type="button" onClick={() => openWorkspace("changes")}>
                  <span className="preview-label">改动</span>
                  <strong>查看 diff 与未提交文件</strong>
                  <small>围绕当前任务快速审阅本轮代码修改</small>
                </button>
                <button className="preview-card" type="button" onClick={() => openWorkspace("files")}>
                  <span className="preview-label">文件</span>
                  <strong>打开和编辑工作区文件</strong>
                  <small>最近文件与浅层文件树都在这里</small>
                </button>
                <button className="preview-card" type="button" onClick={() => openWorkspace("run")}>
                  <span className="preview-label">运行</span>
                  <strong>执行常用检查命令</strong>
                  <small>适合手机端做轻量验证和回看结果</small>
                </button>
              </div>
            </div>
          </section>

          <section className={`page${activePage === "settings" ? " active" : ""}`}>
            <div className="settings-stack">
              <div className="page-card">
                <div className="section-title">执行模式</div>
                <div className="section-copy">恢复共享会话时，按这里的默认模式启动。</div>
                <div className="mode-grid">
                  {["inspect", "workspace", "full-auto"].map((mode) => (
                    <button
                      key={mode}
                      className={`mode-btn${preferredExecutionMode === mode ? " active" : ""}`}
                      type="button"
                      onClick={() => updateExecutionMode(mode)}
                    >
                      <strong>{executionModeLabel(mode)}</strong>
                      <span>
                        {mode === "inspect" ? "更保守，适合只读排查" : mode === "workspace" ? "工作区写入，保持审批" : "效率最高，但风险也最高"}
                      </span>
                    </button>
                  ))}
                </div>
                <small className="hint-text">当前默认：{executionModeLabel(preferredExecutionMode)}；点“恢复共享会话”即可按此模式启动或重建共享会话。</small>
              </div>

              <div className="page-card">
                <div className="section-title">快捷任务</div>
                <div className="section-copy">把高频目标预置成一键 Prompt。</div>
                <div className="quick-editor">
                  {Array.from({ length: CUSTOM_QUICK_SLOTS }, (_, index) => (
                    <React.Fragment key={index}>
                      <input
                        placeholder={`按钮 ${index + 1} 名称`}
                        value={customQuickInputs[index]?.label || ""}
                        onChange={(event) =>
                          setCustomQuickInputs((prev) =>
                            prev.map((item, itemIndex) =>
                              itemIndex === index ? { ...item, label: event.target.value } : item,
                            ),
                          )
                        }
                      />
                      <textarea
                        rows="3"
                        placeholder={`按钮 ${index + 1} 内容`}
                        value={customQuickInputs[index]?.template || ""}
                        onChange={(event) =>
                          setCustomQuickInputs((prev) =>
                            prev.map((item, itemIndex) =>
                              itemIndex === index ? { ...item, template: event.target.value } : item,
                            ),
                          )
                        }
                      />
                    </React.Fragment>
                  ))}
                  <div className="stack-actions two-up">
                    <button className="primary" type="button" onClick={saveCustomQuickEditor}>保存</button>
                    <button type="button" onClick={resetCustomQuickEditor}>恢复默认</button>
                  </div>
                </div>
              </div>

              <div className="page-card">
                <div className="section-title">显示设置</div>
                <div className="chip-row">
                  <button type="button" onClick={() => setChatCompact((prev) => !prev)}>紧凑模式: {chatCompact ? "开" : "关"}</button>
                  <button type="button" onClick={() => setChatFontSize((prev) => clamp(prev - 1, 11, 18))}>A-</button>
                  <button type="button" onClick={() => setChatFontSize((prev) => clamp(prev + 1, 11, 18))}>A+</button>
                </div>
              </div>

              <div className="page-card">
                <div className="section-title">外部入口</div>
                <div className="portal-stack">
                  <a className="portal-card" href="/term/">
                    <span className="preview-label">终端</span>
                    <strong>进入 ttyd 终端</strong>
                    <small>/term/</small>
                  </a>
                  <a className="portal-card" href="/ide/">
                    <span className="preview-label">IDE</span>
                    <strong>进入桌面 IDE</strong>
                    <small>/ide/</small>
                  </a>
                </div>
              </div>
            </div>
          </section>
        </main>

        <footer ref={composerShellRef} className={`composer-shell${activePage === "chat" ? "" : " hidden"}`}>
          <div className="composer-card">
            <div className="composer-status-row">
              <div className="composer-status">
                <span className="composer-dot" aria-hidden="true" />
                <span>{composerStatusText}</span>
              </div>
              <div className="composer-top-actions">
                <button
                  className={`tool-chip${composerToolsOpen ? " active" : ""}`}
                  type="button"
                  onClick={() => setComposerToolsOpen((prev) => !prev)}
                >
                  工具
                </button>
                <button className="tool-chip" type="button" onClick={() => syncChat().catch(handleChatError)}>
                  同步
                </button>
              </div>
            </div>
            <div className={`composer-tools-panel${composerToolsOpen ? " open" : ""}`}>
              {secondaryComposerTools.map((tool) => (
                <button
                  key={tool.label}
                  className={`tool-chip${tool.danger ? " danger" : ""}`}
                  type="button"
                  onClick={tool.onClick}
                >
                  {tool.label}
                </button>
              ))}
            </div>
            <div className="composer-main">
              <div className="composer-input-shell">
                <textarea
                  ref={promptRef}
                  className="composer-input"
                  rows="1"
                  value={promptValue}
                  placeholder="给 Codex 发消息"
                  onChange={(event) => setPromptValue(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      sendPrompt().catch(handleChatError);
                    }
                  }}
                  disabled={sendingPrompt}
                />
                <button
                  className="primary send-btn"
                  type="button"
                  onClick={() => sendPrompt().catch(handleChatError)}
                  disabled={sendingPrompt}
                  aria-label={chatPrimaryLabel}
                  title={chatPrimaryLabel}
                >
                  <span className="send-btn-icon" aria-hidden="true">{sendingPrompt ? "…" : "↑"}</span>
                  <span className="send-btn-text">{chatPrimaryLabel}</span>
                </button>
              </div>
            </div>
          </div>
        </footer>

      </div>
    </>
  );
}

function resolveComposerStatus({ sendingPrompt, currentSession, status }) {
  if (sendingPrompt) return "发送中";
  if (!currentSession) return "未连接";
  if (status === "running") return "处理中";
  if (status === "waiting_approval") return "待确认";
  if (status === "failed") return "失败";
  if (status === "completed" || status === "waiting_input") return "可继续";
  return "已连接";
}

function CollapsibleMessage({ text, className = "", maxChars = 320, maxLines = 8, mono = false }) {
  const [expanded, setExpanded] = useState(false);
  const value = String(text || "").trim();
  const collapsible = shouldCollapseMessage(value, maxChars, maxLines);

  return (
    <>
      <div className={`${className}${collapsible && !expanded ? " collapsed" : ""}`}>
        <RichText text={value} mono={mono} />
      </div>
      {collapsible ? (
        <button className="chat-expand-btn" type="button" onClick={() => setExpanded((prev) => !prev)}>
          {expanded ? "收起" : "展开全文"}
        </button>
      ) : null}
    </>
  );
}

function assistantOutcomeNote(item) {
  if (item.resultText) {
    return {
      tone: "result",
      text: "这轮已经给出最终结果，过程记录默认折叠在下方。",
    };
  }
  if (item.outcome === "aborted") {
    return {
      tone: "error",
      text: "这轮没有产出最终结果，下面只保留中断前的过程记录。",
    };
  }
  return {
    tone: "warn",
    text: "这轮尚未产出最终结果，下面展示的是最近进展，不代表最终结论。",
  };
}

function ProgressSummary({ item, compact = false }) {
  const total = Number(item?.taskProgress?.total || 0);
  const completed = Number(item?.taskProgress?.completed || 0);
  const ratio = total > 0 ? Math.max(0, Math.min(100, Math.round((completed / total) * 100))) : 0;
  const phaseLabel = item?.phaseLabel || (item?.resultText ? "已返回结果" : "正在处理中");
  const summary = total > 0 ? `共 ${total} 个任务，已完成 ${completed} 个` : "这轮暂时还没有可计数的任务。";

  return (
    <div className={`chat-progress-panel${compact ? " compact" : ""}`}>
      <div className="chat-progress-head">
        <span className="chat-progress-phase">{phaseLabel}</span>
        <span className="chat-progress-summary">{summary}</span>
      </div>
      {total > 0 ? (
        <>
          <div className="chat-progress-bar" aria-hidden="true">
            <span style={{ width: `${ratio}%` }} />
          </div>
          <div className="chat-progress-meta">
            <span>已完成 {completed}</span>
            <span>剩余 {Math.max(0, total - completed)}</span>
          </div>
        </>
      ) : null}
    </div>
  );
}

function ConversationItem({ item, onContinue, onCopy }) {
  if (item.type === "day") {
    return <div className="chat-day"><span>{item.label}</span></div>;
  }

  if (item.type === "system") {
    return <div className={`chat-system ${item.tone || "info"}`}><span>{item.text}</span></div>;
  }

  if (item.type === "user") {
    return (
      <div className="chat-row user">
        <div className={`chat-bubble user${item.pending ? " pending" : ""}`}>
          <CollapsibleMessage text={item.text} className="chat-bubble-body" maxChars={240} maxLines={6} />
          <div className="chat-bubble-foot">
            {item.pending ? <span className="chat-bubble-status">发送中</span> : null}
            <span className="chat-bubble-time">{bubbleTimeLabel(item.ts)}</span>
          </div>
        </div>
      </div>
    );
  }

  const outcomeNote = assistantOutcomeNote(item);
  const detailSummaryLabel = (item.detailSummary || "查看过程")
    .replace(/^查看过程/, item.resultText ? "展开本轮过程" : "展开过程记录");

  return (
    <div className="chat-row assistant">
      <div className="chat-avatar" aria-hidden="true">C</div>
      <div className={`chat-bubble assistant${item.error ? " error" : ""}`}>
        <div className="chat-bubble-head">
          <div className="chat-bubble-titlewrap">
            <strong className="chat-bubble-name">Codex</strong>
            <span className={`chat-turn-state ${item.outcome || "idle"}`}>{item.statusLabel || "处理中"}</span>
          </div>
          <span className="chat-bubble-time">{bubbleTimeLabel(item.lastTs || item.ts)}</span>
        </div>
        <div className={`chat-turn-note ${outcomeNote.tone}`}>{outcomeNote.text}</div>
        {!item.resultText ? <ProgressSummary item={item} /> : null}
        {item.resultText ? (
          <div className="chat-result-block">
            <div className="chat-result-label">最终结果</div>
            <CollapsibleMessage text={cleanAssistantMessageText(item.resultText || "")} className="chat-bubble-body chat-result-body" maxChars={420} maxLines={10} />
          </div>
        ) : null}
        {!item.resultText ? (
          <div className="chat-process-block">
            <div className="chat-process-label">{item.outcome === "aborted" ? "中断信息" : "当前进展"}</div>
            <CollapsibleMessage text={cleanAssistantMessageText(item.text || "")} className="chat-bubble-body chat-process-body" maxChars={260} maxLines={6} />
          </div>
        ) : null}
        {item.resultText && item.processText ? (
          <details className="chat-process-fold">
            <summary>本轮过程摘要</summary>
            <CollapsibleMessage text={cleanAssistantMessageText(item.processText || "")} className="chat-turn-text" maxChars={220} maxLines={4} />
          </details>
        ) : null}
        {item.abortedText && item.resultText ? (
          <div className="chat-turn-note error">{item.abortedText}</div>
        ) : null}
        {item.badges?.length ? (
          <div className="chat-badges">
            {item.badges.map((badge) => <span key={badge} className="chat-badge">{badge}</span>)}
          </div>
        ) : null}
        <div className="chat-inline-actions">
          <button className="chat-action" type="button" onClick={() => onCopy(item.resultText || item.text || "")}>{item.resultText ? "复制结果" : "复制"}</button>
          <button className="chat-action accent" type="button" onClick={onContinue}>继续</button>
        </div>
        {item.details?.length ? (
          <details className="chat-turn-details">
            <summary>{detailSummaryLabel}</summary>
            <div className="chat-turn-list">
              {item.details.map((row, index) => (
                <div key={`${row.label}:${index}`} className={`chat-turn-item${row.mono ? " mono" : ""}`}>
                  <div className="chat-turn-item-head">
                    <span className="chat-turn-kind">{row.label}</span>
                    <span className="chat-turn-time">{row.time}</span>
                  </div>
                  <CollapsibleMessage text={row.text} className="chat-turn-text" maxChars={220} maxLines={5} mono={row.mono} />
                  {row.expanded && row.fullText && row.fullText !== row.text ? (
                    <details className="chat-raw-fold">
                      <summary>查看原始内容</summary>
                      <CollapsibleMessage text={row.fullText} className="chat-turn-text" maxChars={800} maxLines={12} mono={row.mono} />
                    </details>
                  ) : null}
                </div>
              ))}
            </div>
          </details>
        ) : null}
      </div>
    </div>
  );
}

function EmptyConversation({ currentRepo, currentSession, status, rawLogLines, onQuickStart, onPause, onStop, onToggleLog }) {
  const sessionState = currentSession
    ? `共享会话 · ${STATUS_LABELS[currentSession.status || status] || currentSession.status || status}`
    : "未连接";
  const starters = [
    { icon: "↻", label: "继续", template: "继续推进当前任务，直接实施下一步并汇报结果。" },
    { icon: "✓", label: "修复并验证", template: "请修复当前失败点，并运行相关测试后给出结论。" },
    { icon: "≡", label: "总结", template: "请总结当前进展、阻塞点、下一步计划。" },
  ];

  return (
    <div className="chat-empty chat-empty-conversation">
      <div className="chat-day"><span>现在</span></div>
      <div className="chat-row assistant chat-empty-intro">
        <div className="chat-avatar" aria-hidden="true">C</div>
        <div className="chat-bubble assistant intro">
        <div className="chat-bubble-head">
          <strong className="chat-bubble-name">Codex</strong>
          <span className="chat-bubble-time">{sessionState}</span>
        </div>
          <div className="chat-bubble-body">{currentRepo?.name || "当前项目"}</div>
        </div>
      </div>
      <LiveAssistantCard
        status={status}
        conversationItems={[]}
        fallbackTaskText={currentSession?.last_prompt || ""}
        rawLogLines={rawLogLines}
        onPause={onPause}
        onStop={onStop}
        onToggleLog={onToggleLog}
      />
      <div className="chat-empty-suggestions">
        <div className="chat-starters compact">
          {starters.map((item) => (
            <button key={item.label} className="chat-starter" type="button" onClick={() => onQuickStart(item.template)}>
              <span className="chat-starter-icon" aria-hidden="true">{item.icon}</span>
              <span className="chat-starter-copywrap">
                <strong className="chat-starter-label">{item.label}</strong>
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function LiveAssistantCard({ status, conversationItems, fallbackTaskText = "", rawLogLines, onPause, onStop, onToggleLog }) {
  if (!["running", "waiting_approval", "failed"].includes(status)) return null;
  const latestAssistant = [...(conversationItems || [])].reverse().find((item) => item?.type === "assistant") || null;
  const latestUser = [...(conversationItems || [])].reverse().find((item) => item?.type === "user") || null;
  const lines = latestLogPreview(rawLogLines, 4);
  const progressText = cleanAssistantMessageText(
    latestAssistant?.processText ||
    (latestAssistant?.resultText ? "" : latestAssistant?.text) ||
    lines.join("\n") ||
    "",
  );
  const latestTaskText = String(latestUser?.text || fallbackTaskText || "").trim();
  const title = status === "waiting_approval" ? "待确认" : status === "failed" ? "已中断" : "处理中";
  const note =
    status === "waiting_approval"
      ? "等待你确认后才会继续执行，目前还没有最终结果。"
      : status === "failed"
        ? "这一轮已经中断，目前只显示中断前最后一段进展。"
        : "这一轮还在执行中，目前只显示最近进展，尚未返回最终结果。";
  const noteTone = status === "failed" ? "error" : "warn";
  const progressItem = latestAssistant || {
    resultText: "",
    phaseLabel: status === "waiting_approval" ? "等待确认" : status === "failed" ? "已中断" : "正在处理中",
    taskProgress: { total: 0, completed: 0 },
  };

  return (
    <div className="chat-row assistant chat-live-row live-inline">
      <div className="chat-avatar" aria-hidden="true">C</div>
      <div className="chat-bubble assistant live">
        <div className="chat-bubble-head">
          <div className="chat-bubble-titlewrap">
            <strong className="chat-bubble-name">{title}</strong>
            <span className={`chat-turn-state ${status === "failed" ? "aborted" : "update"}`}>{status === "waiting_approval" ? "等待确认" : status === "failed" ? "本轮中断" : "尚未返回结果"}</span>
          </div>
          <span className="chat-bubble-time">{STATUS_LABELS[status] || status}</span>
        </div>
        <div className={`chat-turn-note ${noteTone}`}>{note}</div>
        <ProgressSummary item={progressItem} compact />
        {latestTaskText ? (
          <div className="chat-process-block">
            <div className="chat-process-label">当前任务</div>
            <CollapsibleMessage text={latestTaskText} className="chat-bubble-body chat-process-body" maxChars={240} maxLines={4} />
          </div>
        ) : null}
        <div className="chat-process-block">
          <div className="chat-process-label">最近进展</div>
          <CollapsibleMessage text={progressText || "Codex 正在处理中，关键进展会在这里更新。"} className="chat-bubble-body chat-process-body" maxChars={220} maxLines={4} />
        </div>
        <div className="chat-inline-actions">
          {status === "running" ? (
            <>
              <button className="chat-action" type="button" onClick={onPause}>暂停</button>
              <button className="chat-action danger" type="button" onClick={onStop}>停止</button>
            </>
          ) : null}
          <button className="chat-action" type="button" onClick={onToggleLog}>更多细节</button>
        </div>
      </div>
    </div>
  );
}
