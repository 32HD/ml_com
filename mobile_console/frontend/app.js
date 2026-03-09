const state = {
  repos: [],
  currentRepo: null,
  currentSession: null,
  sessions: [],
  stream: null,
  sessionPollTimer: null,
  activePage: "chat",
  chatCompact: false,
  chatFontSize: 13,
  snapshotLines: 240,
  navInit: (() => {
    const q = new URLSearchParams(window.location.search);
    return {
      repoId: q.get("repo") || "",
      sessionId: q.get("session") || "",
      page: q.get("page") || "",
    };
  })(),
};

function $(id) {
  return document.getElementById(id);
}

function currentUrlBase() {
  return `${window.location.origin}${window.location.pathname}`;
}

function updateUrlState() {
  const q = new URLSearchParams();
  if (state.currentRepo && state.currentRepo.id) q.set("repo", state.currentRepo.id);
  if (state.currentSession && state.currentSession.id) q.set("session", state.currentSession.id);
  if (state.activePage && state.activePage !== "dashboard") q.set("page", state.activePage);
  const suffix = q.toString() ? `?${q.toString()}` : "";
  const next = `${window.location.pathname}${suffix}`;
  const cur = `${window.location.pathname}${window.location.search}`;
  if (next !== cur) {
    history.replaceState(null, "", next);
  }
}

function statusClass(status) {
  return String(status || "idle")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]/g, "_");
}

function esc(text) {
  return String(text || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function openDrawer() {
  const overlay = $("drawer-overlay");
  if (overlay) overlay.classList.add("open");
}

function closeDrawer() {
  const overlay = $("drawer-overlay");
  if (overlay) overlay.classList.remove("open");
}

function syncBottomNav() {
  document.querySelectorAll(".bottom-nav button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.page === state.activePage);
  });
}

function updateHeaderTitle() {
  const el = $("header-title");
  if (!el) return;
  const repo = state.currentRepo ? state.currentRepo.name : "未选择项目";
  const sessionName = state.currentSession && (state.currentSession.name || "").trim();
  const pageMap = {
    dashboard: "项目",
    chat: "会话",
    changes: "变更",
    files: "文件",
    run: "运行",
    more: "更多",
  };
  const page = pageMap[state.activePage] || "会话";
  if (state.activePage === "chat" && sessionName) {
    el.textContent = `${repo} · ${sessionName}`;
    return;
  }
  el.textContent = `${repo} · ${page}`;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  if (!res.ok) {
    const txt = await res.text();
    throw new Error(`${res.status}: ${txt}`);
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res.text();
}

function setStatus(s) {
  const status = String(s || "idle");
  const pill = $("status-pill");
  if (!pill) return;
  pill.textContent = status;
  pill.className = `pill ${statusClass(status)}`;
}

function promptDraftKey() {
  const sessionId = state.currentSession && state.currentSession.id ? state.currentSession.id : "";
  const repoId = state.currentRepo && state.currentRepo.id ? state.currentRepo.id : "";
  return `codex_prompt_draft_${sessionId || repoId || "default"}`;
}

function lastPromptKey() {
  const repoId = state.currentRepo && state.currentRepo.id ? state.currentRepo.id : "";
  return `codex_last_prompt_${repoId || "default"}`;
}

function pendingPromptKey(sessionId) {
  const currentSessionId = state.currentSession && state.currentSession.id ? state.currentSession.id : "";
  return `codex_pending_prompt_${sessionId || currentSessionId || "none"}`;
}

function savePromptDraft() {
  try {
    localStorage.setItem(promptDraftKey(), $("prompt").value || "");
  } catch (_err) {
    // ignore storage failures
  }
}

function loadPromptDraft() {
  try {
    $("prompt").value = localStorage.getItem(promptDraftKey()) || "";
  } catch (_err) {
    $("prompt").value = "";
  }
}

function saveLastPrompt(text) {
  try {
    localStorage.setItem(lastPromptKey(), text || "");
  } catch (_err) {
    // ignore storage failures
  }
}

function getLastPrompt() {
  try {
    return (localStorage.getItem(lastPromptKey()) || "").trim();
  } catch (_err) {
    return "";
  }
}

function savePendingPrompt(sessionId, prompt) {
  if (!sessionId) return;
  try {
    localStorage.setItem(
      pendingPromptKey(sessionId),
      JSON.stringify({ prompt, savedAt: new Date().toISOString() }),
    );
  } catch (_err) {
    // ignore storage failures
  }
}

function clearPendingPrompt(sessionId) {
  if (!sessionId) return;
  try {
    localStorage.removeItem(pendingPromptKey(sessionId));
  } catch (_err) {
    // ignore storage failures
  }
}

function loadPendingPrompt(sessionId) {
  if (!sessionId) return "";
  try {
    const raw = localStorage.getItem(pendingPromptKey(sessionId));
    if (!raw) return "";
    const data = JSON.parse(raw);
    return ((data && data.prompt) || "").trim();
  } catch (_err) {
    return "";
  }
}

function setSessionMeta(session, extra = "") {
  const el = $("session-meta");
  if (!el) return;
  if (!session) {
    el.textContent = "未连接会话";
    return;
  }
  const sid = (session.id || "").slice(0, 8);
  const name = (session.name || "").trim();
  const tmux = session.tmux_session || "-";
  const mode = tmux.startsWith("vscode:") ? "vscode" : (tmux.endsWith("_shared") ? "shared" : "temp");
  const ts = formatTs(session.updated_at);
  el.textContent = `${name || `会话 #${sid}`} · ${mode} · ${session.status || "-"} · ${ts}${extra ? ` · ${extra}` : ""}`;
}

function logLine(line) {
  if (!line) return;
  const box = $("chat-log");
  const nearBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 24;
  const next = box.textContent + (box.textContent ? "\n" : "") + line;
  // Keep UI responsive on mobile by limiting in-memory text size.
  box.textContent = next.length > 120000 ? next.slice(-100000) : next;
  if (nearBottom) {
    box.scrollTop = box.scrollHeight;
  }
}

function chatErr(err) {
  const msg = (err && err.message) || String(err);
  logLine(`[error] ${msg}`);
}

function formatTs(ts) {
  if (!ts) return "-";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString();
}

function clamp(n, min, max) {
  return Math.min(max, Math.max(min, n));
}

function applyChatLayout() {
  document.body.classList.toggle("chat-compact", state.chatCompact);
  document.documentElement.style.setProperty("--chat-font-size", `${state.chatFontSize}px`);
  const btn = $("chat-compact-toggle");
  if (btn) btn.textContent = `紧凑模式: ${state.chatCompact ? "开" : "关"}`;
}

function saveChatLayout() {
  try {
    localStorage.setItem("codex_chat_compact", state.chatCompact ? "1" : "0");
    localStorage.setItem("codex_chat_font_size", String(state.chatFontSize));
  } catch (_err) {
    // ignore storage failures
  }
}

function loadChatLayout() {
  try {
    state.chatCompact = localStorage.getItem("codex_chat_compact") === "1";
    const raw = Number(localStorage.getItem("codex_chat_font_size") || "13");
    if (Number.isFinite(raw)) state.chatFontSize = clamp(raw, 11, 18);
  } catch (_err) {
    state.chatCompact = false;
    state.chatFontSize = 13;
  }
  applyChatLayout();
}

function showPage(name) {
  for (const el of document.querySelectorAll(".page")) {
    el.classList.remove("active");
  }
  const target = document.getElementById(`page-${name}`);
  if (target) target.classList.add("active");
  state.activePage = name || "chat";
  syncBottomNav();
  updateHeaderTitle();
  updateUrlState();
}

function resetSessionView() {
  state.currentSession = null;
  if (state.stream) {
    state.stream.close();
    state.stream = null;
  }
  $("chat-log").textContent = "";
  setSessionMeta(null);
}

function switchRepo(repo, { focusChat = false } = {}) {
  if (!repo) return;
  state.currentRepo = repo;
  const sel = $("repo-select");
  if (sel) sel.value = repo.id;
  renderRepos();
  resetSessionView();
  setStatus(repo.session_status || "idle");
  loadPromptDraft();
  updateHeaderTitle();
  updateUrlState();
  if (focusChat) showPage("chat");
  loadSessions().catch((e) => setProjectMsg(`加载会话失败: ${e.message}`, true));
}

function renderRepos() {
  const sel = $("repo-select");
  sel.innerHTML = "";
  state.repos.forEach((r) => {
    const opt = document.createElement("option");
    opt.value = r.id;
    opt.textContent = `${r.name} (${r.branch})`;
    sel.appendChild(opt);
  });
  if (!state.currentRepo && state.repos.length) state.currentRepo = state.repos[0];
  if (state.currentRepo) sel.value = state.currentRepo.id;

  const containers = [$("repo-cards"), $("dashboard-repos")].filter(Boolean);
  containers.forEach((cards) => {
    cards.innerHTML = "";
    state.repos.forEach((r) => {
      const div = document.createElement("button");
      const active = state.currentRepo && state.currentRepo.id === r.id;
      div.className = `card repo-card${active ? " active" : ""}`;
      div.innerHTML = `<strong>${esc(r.name)}</strong><br/><small>${esc(r.branch)} · dirty ${esc(r.dirty_files)} · ${esc(r.session_status || "idle")}</small><br/><small>${esc(r.path)}</small>`;
      div.onclick = () => {
        switchRepo(r, { focusChat: true });
        closeDrawer();
      };
      cards.appendChild(div);
    });
  });

  updateHeaderTitle();
}

async function loadRepos() {
  const prevRepoId = state.currentRepo && state.currentRepo.id ? state.currentRepo.id : null;
  state.repos = await api("/api/repos");
  if (prevRepoId) {
    state.currentRepo = state.repos.find((r) => r.id === prevRepoId) || null;
  }
  if (state.repos.length && !state.currentRepo) state.currentRepo = state.repos[0];
  renderRepos();
}

function renderSessions() {
  const box = $("session-list");
  if (!box) return;
  box.innerHTML = "";
  if (!state.sessions.length) {
    box.innerHTML = '<div class="item">当前项目暂无历史会话</div>';
    return;
  }
  state.sessions.forEach((s, idx) => {
    const btn = document.createElement("button");
    const isActive = state.currentSession && state.currentSession.id === s.id;
    btn.className = `session-item${isActive ? " active" : ""}`;
    const sid = (s.id || "").slice(0, 8);
    const displayName = (s.name || "").trim() || `#${idx + 1} ${sid}`;
    const tmux = s.tmux_session || "";
    const mode = tmux.startsWith("vscode:") ? "vscode" : (tmux.endsWith("_shared") ? "shared" : "temp");
    const stClass = statusClass(s.status);
    const time = formatTs(s.updated_at);
    const hint = s.last_prompt ? s.last_prompt.slice(0, 34) : "无最近提示";
    btn.innerHTML = [
      '<div class="session-item-head">',
      `<span class="session-dot ${stClass}"></span>`,
      `<span class="session-item-name">${esc(displayName)}</span>`,
      "</div>",
      `<div class="session-item-sub">${esc(mode)} · ${esc(s.status)} · ${esc(time)}</div>`,
      `<div class="session-item-sub">${esc(hint)}</div>`,
    ].join("");
    btn.onclick = async () => {
      await activateSession(s.id);
      closeDrawer();
      showPage("chat");
    };
    box.appendChild(btn);
  });
}

async function loadSessions() {
  if (!state.currentRepo) return;
  state.sessions = await api(`/api/repos/${state.currentRepo.id}/sessions`);
  renderSessions();

  if (state.currentSession) {
    const fresh = state.sessions.find((s) => s.id === state.currentSession.id);
    if (fresh) {
      state.currentSession = fresh;
      setStatus(fresh.status);
      setSessionMeta(fresh);
      updateHeaderTitle();
    }
  }

  const latest = state.sessions[0] || null;
  const switchBtn = $("switch-latest");
  if (switchBtn) {
    if (latest && state.currentSession && latest.id !== state.currentSession.id) {
      switchBtn.classList.remove("hidden");
      switchBtn.textContent = `切到最新 #${latest.id.slice(0, 8)}`;
    } else {
      switchBtn.classList.add("hidden");
    }
  }
  updateHeaderTitle();
}

async function loadSnapshot(lines = state.snapshotLines) {
  if (!state.currentSession) return;
  const data = await api(`/api/sessions/${state.currentSession.id}/snapshot?lines=${lines}`);
  const text = (data.lines || []).join("\n");
  $("chat-log").textContent = text.length > 120000 ? text.slice(-100000) : text;
  $("chat-log").scrollTop = $("chat-log").scrollHeight;
  setStatus(data.status || state.currentSession.status || "running");
  state.currentSession = {
    ...state.currentSession,
    status: data.status || state.currentSession.status,
    tmux_session: data.tmux_session || state.currentSession.tmux_session,
  };
  setSessionMeta(state.currentSession, "已同步");
}

async function activateSession(sessionId) {
  if (!sessionId) return;
  try {
    const s = await api(`/api/sessions/${sessionId}/resume`, { method: "POST" });
    state.currentSession = s;
    setStatus(s.status);
    setSessionMeta(s);
    updateUrlState();
    loadPromptDraft();
    const pending = loadPendingPrompt(s.id);
    if (pending && !$("prompt").value.trim()) {
      $("prompt").value = pending;
      setSessionMeta(s, "检测到未发送消息");
    }
    await connectStream();
    updateHeaderTitle();
  } catch (err) {
    setProjectMsg(`恢复会话失败: ${err.message}`, true);
    await loadSessions();
  }
}

function setProjectMsg(text, isErr = false) {
  const el = $("create-project-msg");
  if (!el) return;
  el.textContent = text;
  el.style.color = isErr ? "var(--danger)" : "var(--muted)";
}

async function createProject() {
  const name = $("create-project-name").value.trim();
  const githubOwner = $("create-github-owner").value.trim();
  const githubRepo = $("create-github-repo").value.trim();
  const createGithub = $("create-github-enable").checked;
  const isPrivate = $("create-github-private").checked;
  if (!name) {
    setProjectMsg("请输入项目名", true);
    return;
  }
  const btn = $("create-project-btn");
  btn.disabled = true;
  setProjectMsg("正在创建项目...");
  try {
    const res = await api("/api/projects/init", {
      method: "POST",
      body: JSON.stringify({
        name,
        github_owner: githubOwner || null,
        github_repo: githubRepo || null,
        create_github_repo: createGithub,
        private: isPrivate,
        push_initial_commit: true,
      }),
    });
    await loadRepos();
    state.currentRepo = state.repos.find((r) => r.id === res.repo.id) || state.currentRepo;
    renderRepos();
    setStatus((state.currentRepo && state.currentRepo.session_status) || "idle");
    loadPromptDraft();
    await loadSessions();
    setProjectMsg(`创建成功: ${res.repo.name}${res.github_url ? ` | ${res.github_url}` : ""}`, false);
    $("create-project-name").value = "";
    $("create-github-repo").value = "";
    closeDrawer();
    showPage("chat");
    updateHeaderTitle();
  } catch (err) {
    setProjectMsg(`创建失败: ${err.message}`, true);
  } finally {
    btn.disabled = false;
  }
}

async function resumeSession() {
  if (!state.currentRepo) return;
  const s = await api(`/api/repos/${state.currentRepo.id}/sessions/resume`, { method: "POST" });
  state.currentSession = s;
  setStatus(s.status);
  setSessionMeta(s);
  updateUrlState();
  loadPromptDraft();
  const pending = loadPendingPrompt(s.id);
  if (pending && !$("prompt").value.trim()) {
    $("prompt").value = pending;
    setSessionMeta(s, "检测到未发送消息");
  }
  await connectStream();
  await loadSessions();
  closeDrawer();
  showPage("chat");
  updateHeaderTitle();
}

async function newSession() {
  if (!state.currentRepo) return;
  const s = await api(`/api/repos/${state.currentRepo.id}/sessions/new`, { method: "POST" });
  state.currentSession = s;
  $("chat-log").textContent = "";
  setStatus(s.status);
  setSessionMeta(s, "新会话");
  updateUrlState();
  loadPromptDraft();
  await connectStream();
  await loadSessions();
  closeDrawer();
  showPage("chat");
  updateHeaderTitle();
}

async function connectStream() {
  if (!state.currentSession) return;
  const sessionId = state.currentSession.id;

  await loadSnapshot();
  if (!state.currentSession || state.currentSession.id !== sessionId) return;

  if (state.stream) state.stream.close();
  const es = new EventSource(`/api/sessions/${sessionId}/stream?seed_lines=0`);
  state.stream = es;

  es.addEventListener("status", (ev) => {
    const data = JSON.parse(ev.data);
    setStatus(data.status);
    if (state.currentSession && data.session_id === state.currentSession.id) {
      state.currentSession = { ...state.currentSession, status: data.status };
      setSessionMeta(state.currentSession);
    }
  });

  es.addEventListener("message", (ev) => {
    const data = JSON.parse(ev.data);
    logLine(data.line || "");
  });

  es.addEventListener("approval_request", (ev) => {
    const data = JSON.parse(ev.data);
    $("approval-card").classList.remove("hidden");
    $("approval-text").textContent = data.summary || "Codex 请求批准";
    setSessionMeta(state.currentSession, "等待批准");
  });

  es.addEventListener("error", () => {
    // leave reconnect to browser eventsource
  });
}

async function sendPrompt() {
  if (!state.currentSession) {
    await resumeSession();
  }
  if (!state.currentSession) return;
  const prompt = $("prompt").value.trim();
  if (!prompt) return;
  const sid = state.currentSession.id;
  savePendingPrompt(sid, prompt);
  await api(`/api/sessions/${state.currentSession.id}/prompt`, {
    method: "POST",
    body: JSON.stringify({ prompt }),
  });
  logLine(`> ${prompt}`);
  clearPendingPrompt(sid);
  saveLastPrompt(prompt);
  $("prompt").value = "";
  savePromptDraft();
  setSessionMeta(state.currentSession, "已发送任务");
}

async function renameCurrentSession() {
  if (!state.currentSession) {
    chatErr(new Error("请先选择会话"));
    return;
  }
  const currentName = (state.currentSession.name || "").trim();
  const next = window.prompt("输入会话名称", currentName || "");
  if (next == null) return;
  const name = next.trim();
  if (!name) {
    chatErr(new Error("会话名称不能为空"));
    return;
  }
  const updated = await api(`/api/sessions/${state.currentSession.id}/rename`, {
    method: "POST",
    body: JSON.stringify({ name }),
  });
  state.currentSession = updated;
  setSessionMeta(updated, "已重命名");
  await loadSessions();
}

async function copySessionLink() {
  if (!state.currentRepo || !state.currentSession) {
    chatErr(new Error("请先进入一个会话"));
    return;
  }
  const q = new URLSearchParams();
  q.set("repo", state.currentRepo.id);
  q.set("session", state.currentSession.id);
  q.set("page", "chat");
  const url = `${currentUrlBase()}?${q.toString()}`;
  try {
    await navigator.clipboard.writeText(url);
    setSessionMeta(state.currentSession, "会话链接已复制");
  } catch (_err) {
    window.prompt("复制会话链接", url);
  }
}

async function resendLastPrompt() {
  const last = getLastPrompt();
  if (!last) {
    chatErr(new Error("当前项目没有可重发的上一条任务"));
    return;
  }
  $("prompt").value = last;
  await sendPrompt();
}

async function sendQuickPrompt(text) {
  if (!text) return;
  $("prompt").value = text;
  savePromptDraft();
  await sendPrompt();
}

async function sendControlKey(key, repeat = 1) {
  if (!state.currentSession) {
    await resumeSession();
  }
  if (!state.currentSession) return;
  await api(`/api/sessions/${state.currentSession.id}/key`, {
    method: "POST",
    body: JSON.stringify({ key, repeat }),
  });
}

async function approve(yes) {
  if (!state.currentSession) return;
  await api(`/api/sessions/${state.currentSession.id}/${yes ? "approve" : "reject"}`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  $("approval-card").classList.add("hidden");
}

async function syncChat() {
  await loadSessions();
  if (!state.currentSession) {
    if (state.sessions.length) {
      await activateSession(state.sessions[0].id);
    }
    return;
  }
  await loadSnapshot();
}

async function switchToLatestSession() {
  if (!state.sessions.length) return;
  await activateSession(state.sessions[0].id);
  await loadSessions();
  closeDrawer();
  showPage("chat");
}

async function refreshChanges() {
  if (!state.currentRepo) return;
  const st = await api(`/api/repos/${state.currentRepo.id}/git/status`);
  const files = st.dirty_files || [];
  const box = $("changes-files");
  box.innerHTML = "";
  if (!files.length) {
    box.innerHTML = '<div class="item">暂无未提交改动</div>';
  } else {
    for (const f of files) {
      const b = document.createElement("button");
      b.className = "item";
      b.textContent = f;
      b.onclick = async () => {
        const d = await api(`/api/repos/${state.currentRepo.id}/git/diff/${encodeURIComponent(f)}`);
        $("diff-view").textContent = d.diff || "";
      };
      box.appendChild(b);
    }
  }
  const all = await api(`/api/repos/${state.currentRepo.id}/git/diff`);
  $("diff-view").textContent = (all.diff || "").slice(0, 150000);
}

async function refreshFiles() {
  if (!state.currentRepo) return;
  const recent = await api(`/api/repos/${state.currentRepo.id}/files/recent`);
  const recentBox = $("recent-files");
  recentBox.innerHTML = "";
  (recent.files || []).slice(0, 20).forEach((f) => {
    const btn = document.createElement("button");
    btn.className = "item";
    btn.textContent = f;
    btn.onclick = () => openFile(f);
    recentBox.appendChild(btn);
  });

  const tree = await api(`/api/repos/${state.currentRepo.id}/files/tree?path=.&depth=2`);
  const treeBox = $("tree-files");
  treeBox.innerHTML = "";
  const flat = [];
  function walk(items, prefix = "") {
    for (const it of items || []) {
      const p = prefix ? `${prefix}/${it.name}` : it.name;
      if (it.type === "file") flat.push(it.path || p);
      if (it.children) walk(it.children, p);
    }
  }
  walk(tree.items || []);
  flat.slice(0, 120).forEach((p) => {
    const btn = document.createElement("button");
    btn.className = "item";
    btn.textContent = p;
    btn.onclick = () => openFile(p);
    treeBox.appendChild(btn);
  });
}

async function openFile(path) {
  if (!state.currentRepo) return;
  const data = await api(`/api/repos/${state.currentRepo.id}/file?path=${encodeURIComponent(path)}`);
  $("file-path").value = data.path;
  $("file-content").value = data.content;
}

async function saveFile() {
  if (!state.currentRepo) return;
  const path = $("file-path").value;
  if (!path) return;
  await api(`/api/repos/${state.currentRepo.id}/file`, {
    method: "PUT",
    body: JSON.stringify({ path, content: $("file-content").value }),
  });
  alert("保存成功");
}

async function runCmd(cmd) {
  if (!state.currentRepo) return;
  const data = await api(`/api/repos/${state.currentRepo.id}/run/cmd`, {
    method: "POST",
    body: JSON.stringify({ cmd }),
  });
  $("run-output").textContent = `exit=${data.code}\n\n${data.stdout || ""}\n${data.stderr || ""}`;
}

function startSessionPolling() {
  if (state.sessionPollTimer) {
    clearInterval(state.sessionPollTimer);
  }
  state.sessionPollTimer = setInterval(() => {
    if (!state.currentRepo) return;
    loadSessions().catch(() => {
      // ignore transient polling errors
    });
  }, 12000);
}

function bind() {
  const menuBtn = $("menu-btn");
  const drawerClose = $("drawer-close");
  const drawerOverlay = $("drawer-overlay");
  if (menuBtn) menuBtn.addEventListener("click", openDrawer);
  if (drawerClose) drawerClose.addEventListener("click", closeDrawer);
  if (drawerOverlay) {
    drawerOverlay.addEventListener("click", (ev) => {
      if (ev.target === drawerOverlay) closeDrawer();
    });
  }

  document.querySelectorAll(".bottom-nav button").forEach((btn) => {
    btn.addEventListener("click", () => showPage(btn.dataset.page));
  });
  document.querySelectorAll("[data-page-jump]").forEach((btn) => {
    btn.addEventListener("click", () => showPage(btn.dataset.pageJump));
  });

  $("repo-select").addEventListener("change", () => {
    const repo = state.repos.find((r) => r.id === $("repo-select").value) || null;
    switchRepo(repo, { focusChat: false });
  });

  $("resume-btn").addEventListener("click", resumeSession);
  $("new-btn").addEventListener("click", newSession);
  $("send-btn").addEventListener("click", () => sendPrompt().catch(chatErr));
  $("sync-chat").addEventListener("click", () => syncChat().catch(chatErr));
  $("repeat-last").addEventListener("click", () => resendLastPrompt().catch(chatErr));
  $("rename-session").addEventListener("click", () => renameCurrentSession().catch(chatErr));
  $("copy-session-link").addEventListener("click", () => copySessionLink().catch(chatErr));
  $("switch-latest").addEventListener("click", () => switchToLatestSession().catch(chatErr));
  $("prompt").addEventListener("input", savePromptDraft);
  $("prompt").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      sendPrompt().catch(chatErr);
    }
  });
  document.querySelectorAll(".quick-btn[data-template]").forEach((btn) => {
    btn.addEventListener("click", () => {
      sendQuickPrompt(btn.dataset.template || "").catch(chatErr);
    });
  });
  document.querySelectorAll(".key-btn[data-key]").forEach((btn) => {
    btn.addEventListener("click", () => {
      sendControlKey(btn.dataset.key, 1).catch(chatErr);
    });
  });
  document.addEventListener("keydown", (ev) => {
    const overlay = $("drawer-overlay");
    if (overlay && overlay.classList.contains("open") && ev.key === "Escape") {
      ev.preventDefault();
      closeDrawer();
      return;
    }
    const chatPage = $("page-chat");
    if (!chatPage || !chatPage.classList.contains("active")) return;
    const target = ev.target;
    const tag = ((target && target.tagName) || "").toLowerCase();
    const inInput = tag === "textarea" || tag === "input" || (target && target.isContentEditable);
    if (inInput) return;
    const map = {
      ArrowUp: "Up",
      ArrowDown: "Down",
      ArrowLeft: "Left",
      ArrowRight: "Right",
      Enter: "Enter",
      Tab: "Tab",
      Escape: "Escape",
      Backspace: "Backspace",
    };
    let key = map[ev.key];
    if (!key && ev.ctrlKey && (ev.key === "c" || ev.key === "C")) key = "Ctrl+C";
    if (!key) return;
    ev.preventDefault();
    sendControlKey(key, 1).catch(chatErr);
  });
  $("approve-btn").addEventListener("click", () => approve(true));
  $("reject-btn").addEventListener("click", () => approve(false));
  $("refresh-changes").addEventListener("click", refreshChanges);
  $("refresh-files").addEventListener("click", refreshFiles);
  $("refresh-sessions").addEventListener("click", () => loadSessions().catch(chatErr));
  $("save-file").addEventListener("click", saveFile);
  $("create-project-btn").addEventListener("click", createProject);
  $("chat-compact-toggle").addEventListener("click", () => {
    state.chatCompact = !state.chatCompact;
    applyChatLayout();
    saveChatLayout();
  });
  $("chat-font-dec").addEventListener("click", () => {
    state.chatFontSize = clamp(state.chatFontSize - 1, 11, 18);
    applyChatLayout();
    saveChatLayout();
  });
  $("chat-font-inc").addEventListener("click", () => {
    state.chatFontSize = clamp(state.chatFontSize + 1, 11, 18);
    applyChatLayout();
    saveChatLayout();
  });

  document.querySelectorAll('#page-run [data-cmd]').forEach((btn) => {
    btn.addEventListener("click", () => runCmd(btn.dataset.cmd));
  });
}

(async function init() {
  bind();
  loadChatLayout();
  showPage(state.navInit.page || "chat");
  await loadRepos();
  if (state.navInit.repoId) {
    const targetRepo = state.repos.find((r) => r.id === state.navInit.repoId);
    if (targetRepo) {
      state.currentRepo = targetRepo;
      renderRepos();
    }
  }
  startSessionPolling();
  if (state.currentRepo) {
    loadPromptDraft();
    setStatus(state.currentRepo.session_status || "idle");
    updateHeaderTitle();
    await loadSessions();
    if (state.navInit.sessionId) {
      const found = state.sessions.find((s) => s.id === state.navInit.sessionId);
      if (found) {
        await activateSession(found.id);
      } else {
        await resumeSession();
      }
    } else {
      await resumeSession();
    }
    await refreshChanges();
    await refreshFiles();
  }
})();
