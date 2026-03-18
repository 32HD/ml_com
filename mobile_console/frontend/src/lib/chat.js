export const EXECUTION_MODE_LABELS = {
  inspect: "巡检模式",
  workspace: "开发模式",
  "full-auto": "全自动",
  external: "外部会话",
};

export const STATUS_LABELS = {
  idle: "空闲",
  running: "处理中",
  waiting_approval: "待确认",
  completed: "已完成",
  failed: "失败",
  waiting_input: "可继续",
};

export const QUICK_PROMPT_DEFAULTS = [
  { label: "继续推进", template: "继续推进当前任务，直接实施下一步并汇报结果。" },
  { label: "总结进展", template: "请总结当前进展、阻塞点、下一步计划。" },
  { label: "修复并测试", template: "请修复当前失败点，并运行相关测试后给出结论。" },
  { label: "变更摘要", template: "请给我本轮变更摘要（文件、原因、风险、回滚点）。" },
];

const LOG_NOISE_PATTERNS = [
  /^[╭╰].*[╮╯]$/,
  /^│(?:.*)│$/,
  /^[─━═]{6,}$/,
  /^>\s+/,
  /^›\s+/,
  /^\?\s+for shortcuts(?:\s+\d+%\s+context left)?$/i,
  /^\d+%\s+context left$/i,
  /^tip:/i,
  /^openai codex/i,
  /^model:\s+/i,
  /^directory:\s+/i,
  /^approval policy:\s+/i,
  /^sandbox:\s+/i,
  /^reasoning effort:\s+/i,
  /^session id:\s+/i,
  /^workspace:\s+/i,
  /^provider:\s+/i,
  /^build faster with codex/i,
];

const LOG_DECORATION_PREFIX = /^[⠁-⣿◐◓◑◒◴◷◶◵●○◌•·▪▸▹▶▷◆◇✦✧➜➤→↳]+\s*/u;

export function normalizeInitialPage(page) {
  const raw = String(page || "").trim();
  if (["changes", "files", "run", "dashboard", "workspace"].includes(raw)) return "workspace";
  if (["more", "settings"].includes(raw)) return "settings";
  return "chat";
}

export function statusClass(status) {
  return String(status || "idle")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]/g, "_");
}

export function executionModeLabel(mode) {
  return EXECUTION_MODE_LABELS[mode] || mode || "全自动";
}

export function formatTs(ts) {
  if (!ts) return "-";
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return ts;
  return date.toLocaleString();
}

export function stripAnsi(text) {
  return String(text || "")
    .replace(/\u001b\[[0-9;?]*[ -/]*[@-~]/g, "")
    .replace(/\u001b\][^\u0007]*(?:\u0007|\u001b\\)/g, "")
    .replace(/\r/g, "");
}

export function normalizeLogLine(line) {
  return stripAnsi(line).replace(/\s+$/g, "");
}

export function collapseWhitespace(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

export function truncateText(text, max = 34) {
  const clean = collapseWhitespace(text);
  if (!clean) return "";
  if (clean.length <= max) return clean;
  return `${clean.slice(0, Math.max(1, max - 1))}…`;
}

function formatLogDisplayLine(line) {
  return collapseWhitespace(
    normalizeLogLine(line)
      .replace(LOG_DECORATION_PREFIX, "")
      .replace(/^\[[0-9:.\-\s]+\]\s*/, ""),
  );
}

function canonicalizeLogText(line) {
  return formatLogDisplayLine(line).toLowerCase();
}

function isNoiseLogLine(line) {
  const text = canonicalizeLogText(line);
  if (!text) return true;
  return LOG_NOISE_PATTERNS.some((pattern) => pattern.test(text));
}

export function buildDisplayLogLines(lines = []) {
  const out = [];
  let pendingBlank = false;
  let lastCanonical = "";
  lines.forEach((raw) => {
    const display = formatLogDisplayLine(raw);
    const canonical = display.toLowerCase();
    if (!display) {
      pendingBlank = out.length > 0;
      return;
    }
    if (isNoiseLogLine(display)) return;
    if (canonical === lastCanonical) return;
    if (pendingBlank && out.length && out[out.length - 1] !== "") {
      out.push("");
    }
    out.push(display);
    lastCanonical = canonical;
    pendingBlank = false;
  });
  return out.slice(-120);
}

export function latestLogPreview(rawLines = [], limit = 3) {
  return buildDisplayLogLines(rawLines)
    .filter((line) => {
      const text = String(line || "").trim();
      return text &&
        !/^>\s/.test(text) &&
        !/^›\s/.test(text) &&
        !/^chunk id:/i.test(text) &&
        !/^wall time:/i.test(text) &&
        !/^original token count:/i.test(text);
    })
    .slice(-limit);
}

export function timelineKindLabel(kind) {
  const labels = {
    task_started: "开始",
    user_message: "任务",
    reasoning: "思考",
    commentary: "播报",
    tool_call: "工具调用",
    tool_output: "工具结果",
    final_answer: "答复",
    task_complete: "完成",
    task_aborted: "中断",
  };
  return labels[kind] || "事件";
}

export function bubbleTimeLabel(ts) {
  if (!ts) return "";
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function timelineDateKey(ts) {
  if (!ts) return "";
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return "";
  return `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`;
}

function formatDayLabel(ts) {
  if (!ts) return "";
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  const todayKey = timelineDateKey(now.toISOString());
  const dateKey = timelineDateKey(ts);
  if (dateKey === todayKey) return "今天";
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (dateKey === timelineDateKey(yesterday.toISOString())) return "昨天";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    weekday: "short",
  }).format(date);
}

export function typingStatusMessage(status) {
  if (status === "running") {
    return {
      title: "Codex 正在继续处理",
      text: "当前只展示最近进展，冗长命令输出默认折叠；最终结论会单独显示。",
    };
  }
  if (status === "waiting_approval") {
    return {
      title: "Codex 等待你的确认",
      text: "点批准后，我会继续执行刚才的下一步。",
    };
  }
  return null;
}

export function cleanAssistantMessageText(text) {
  const lines = String(text || "")
    .replace(/\r/g, "")
    .split("\n")
    .map((line) => line.replace(/\s+$/g, ""));
  const kept = [];
  lines.forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed) {
      if (kept.length && kept[kept.length - 1] !== "") kept.push("");
      return;
    }
    if (
      trimmed === "› Write tests for @filename" ||
      /^gpt-[^·]+·\s*\d+% left\s*·/i.test(trimmed) ||
      /^›\s+Write tests for @filename$/i.test(trimmed)
    ) {
      return;
    }
    kept.push(line);
  });
  while (kept.length && kept[0] === "") kept.shift();
  while (kept.length && kept[kept.length - 1] === "") kept.pop();
  return kept.join("\n").trim();
}

export function cleanPromptText(text) {
  const raw = String(text || "").trim();
  if (!raw) return "";
  const marker = "## My request for Codex:";
  if (raw.includes(marker)) {
    const trimmed = raw.split(marker, 1)[1]?.trim();
    return trimmed || raw;
  }
  return raw;
}

function isSharedTmuxSession(tmuxSession) {
  const value = String(tmuxSession || "").trim();
  return !!value && value.endsWith("_shared");
}

function isExternalTmuxSession(tmuxSession) {
  const value = String(tmuxSession || "").trim();
  return value.startsWith("vscode:") || value.startsWith("recorded:");
}

function isMeaningfulPrompt(text) {
  const value = collapseWhitespace(cleanPromptText(text));
  if (!value) return false;
  if (value.length < 6) return false;
  if (/^# context from my ide setup:/i.test(value)) return false;
  if (/^(exit|1|y|n)$/i.test(value)) return false;
  return true;
}

function normalizedPromptKey(text) {
  const cleaned = cleanPromptText(text);
  if (!isMeaningfulPrompt(cleaned)) return "";
  return collapseWhitespace(cleaned).toLowerCase().slice(0, 140);
}

function isGenericSessionName(name) {
  const value = collapseWhitespace(name);
  if (!value) return true;
  return (
    /^vscode\s+[0-9a-f-]+$/i.test(value) ||
    /^终端会话\s+[0-9a-f-]+$/i.test(value) ||
    /^临时会话$/i.test(value) ||
    /^codex_[a-z0-9_]+(?:_[a-z0-9]+)?$/i.test(value)
  );
}

export function isLiveSession(session) {
  const status = String(session?.status || "").trim().toLowerCase();
  return ["running", "waiting_input", "waiting_approval"].includes(status);
}

export function isSharedSession(session) {
  return isSharedTmuxSession(session?.tmux_session) || /共享会话/.test(String(session?.name || ""));
}

function isRecentExternalSession(session, recentHours = 18) {
  if (!isExternalTmuxSession(session?.tmux_session)) return false;
  if (isLiveSession(session)) return true;
  const raw = sessionActivityAt(session);
  if (!raw) return false;
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return false;
  return Date.now() - date.getTime() <= recentHours * 60 * 60 * 1000;
}

export function isArchivedSession(session) {
  return isExternalTmuxSession(session?.tmux_session) && !isRecentExternalSession(session);
}

export function sessionActivityAt(session) {
  return String(session?.last_activity_at || session?.updated_at || session?.created_at || "").trim();
}

export function sessionDisplayTitle(session) {
  if (!session) return "未命名会话";
  const prompt = collapseWhitespace(cleanPromptText(session.last_prompt));
  if (isSharedSession(session)) {
    if (isMeaningfulPrompt(prompt)) return truncateText(prompt, 42);
    if (isSharedTmuxSession(session?.tmux_session)) return "当前项目主会话";
    return `共享会话 ${String(session.id || "").slice(0, 4) || "-"}`;
  }
  if (isMeaningfulPrompt(prompt)) return truncateText(prompt, 42);
  const name = collapseWhitespace(session.name);
  if (name && !isGenericSessionName(name)) return name;
  return `会话 ${String(session.id || "").slice(0, 8) || "-"}`;
}

export function sessionPreviewText(session) {
  if (!session) return "";
  const prompt = collapseWhitespace(cleanPromptText(session.last_prompt));
  if (isMeaningfulPrompt(prompt)) return truncateText(prompt, 72);
  if (isArchivedSession(session)) return "外部或录制历史，仅在需要时展开查看。";
  if (isExternalTmuxSession(session?.tmux_session)) return "来自 VSCode / 外部客户端的最近会话。";
  return "没有可用的任务摘要。";
}

export function sessionOriginLabel(session) {
  if (!session) return "未连接";
  if (isSharedSession(session)) return "共享";
  const tmux = String(session?.tmux_session || "").trim();
  const source = String(session?.codex_source || "").trim().toLowerCase();
  if (tmux.startsWith("vscode:") || source === "vscode") return "VSCode";
  if (tmux.startsWith("recorded:")) return source === "exec" ? "桌面录制" : "外部历史";
  if (source === "cli") return "CLI";
  if (source === "exec") return "桌面";
  return "手机";
}

export function sessionSyncSummary(session) {
  if (!session) return "还没有会话上下文。";
  const activity = sessionActivityAt(session);
  const when = activity ? `最近活动 ${formatTs(activity)}` : "等待同步";
  const origin = sessionOriginLabel(session);
  if (isArchivedSession(session)) return `${origin} 历史会话 · ${when}`;
  if (isExternalTmuxSession(session?.tmux_session) || ["vscode", "cli", "exec"].includes(String(session?.codex_source || "").trim().toLowerCase())) {
    return `${origin} 最近已同步 · ${when}`;
  }
  if (isSharedSession(session)) return `项目共享主线 · ${when}`;
  return `${origin} 当前入口 · ${when}`;
}

export function buildSessionHubView(sessionHub = null, sessions = []) {
  const byId = new Map((sessions || []).map((session) => [String(session?.id || ""), session]));
  const getByIds = (ids = [], limit = 8, excluded = new Set()) =>
    ids
      .map((id) => byId.get(String(id || "")))
      .filter((session) => session && !excluded.has(String(session.id || "")))
      .slice(0, limit);

  const focus = byId.get(String(sessionHub?.current_session_id || "")) || null;
  const suggested = byId.get(String(sessionHub?.suggested_session_id || "")) || null;
  const shared = byId.get(String(sessionHub?.shared_session_id || "")) || null;
  const excluded = new Set(
    [focus, suggested, shared]
      .filter(Boolean)
      .map((session) => String(session.id || "")),
  );

  const externalRecent = getByIds(sessionHub?.external_recent_session_ids, 4, excluded);
  externalRecent.forEach((session) => excluded.add(String(session.id || "")));
  const recent = getByIds(sessionHub?.recent_session_ids, 8, excluded);
  const archived = getByIds(sessionHub?.archived_session_ids, 10);
  const live = getByIds(sessionHub?.live_session_ids, 6);

  return {
    focus,
    suggested,
    shared,
    externalRecent,
    recent,
    archived,
    live,
    syncHint: String(sessionHub?.sync_hint || "").trim(),
    focusReason: String(sessionHub?.focus_reason || "").trim(),
    focusUpdatedAt: String(sessionHub?.focus_updated_at || "").trim(),
  };
}

function compareSessionActivity(left, right) {
  return sessionActivityAt(right).localeCompare(sessionActivityAt(left));
}

export function buildSessionBuckets(sessions = []) {
  const dedupedArchived = [];
  const archivedSeen = new Set();
  const primary = [];
  const primarySeen = new Set();

  sessions.forEach((session) => {
    if (!session) return;
    if (isArchivedSession(session)) {
      const promptKey = normalizedPromptKey(session.last_prompt);
      const groupKey = promptKey || `archived:${String(session.codex_session_id || "").trim() || String(session.name || "").trim() || "misc"}`;
      if (archivedSeen.has(groupKey)) return;
      archivedSeen.add(groupKey);
      dedupedArchived.push(session);
      return;
    }
    const codexKey = String(session.codex_session_id || "").trim();
    const tmuxKey = String(session.tmux_session || "").trim();
    const promptKey = normalizedPromptKey(session.last_prompt);
    const primaryKey = isSharedSession(session)
      ? `primary:shared:${codexKey || tmuxKey || promptKey || String(session.id || "").trim()}`
      : codexKey
        ? `primary:codex:${codexKey}`
        : promptKey || `primary:${String(session.id || "").trim()}`;
    if (primarySeen.has(primaryKey)) return;
    primarySeen.add(primaryKey);
    primary.push(session);
  });

  const orderedPrimary = [...primary].sort((left, right) => {
    const leftLive = isLiveSession(left) ? 1 : 0;
    const rightLive = isLiveSession(right) ? 1 : 0;
    if (leftLive !== rightLive) return rightLive - leftLive;
    const activityCompare = compareSessionActivity(left, right);
    if (activityCompare) return activityCompare;
    const leftShared = isSharedSession(left) ? 1 : 0;
    const rightShared = isSharedSession(right) ? 1 : 0;
    if (leftShared !== rightShared) return rightShared - leftShared;
    return String(right.id || "").localeCompare(String(left.id || ""));
  });
  const orderedArchived = [...dedupedArchived].sort(compareSessionActivity);

  const suggested =
    orderedPrimary[0] ||
    orderedArchived[0] ||
    null;

  return {
    suggested,
    primary: orderedPrimary.slice(0, 4),
    archived: orderedArchived.slice(0, 10),
    archivedHiddenCount: Math.max(0, sessions.length - orderedPrimary.slice(0, 4).length - orderedArchived.slice(0, 10).length),
  };
}

function summarizeMultilineText(text, { maxLines = 4, maxChars = 240 } = {}) {
  const lines = String(text || "")
    .split(/\n+/)
    .map((line) => normalizeLogLine(line))
    .filter(Boolean);
  if (!lines.length) return "";
  const clippedLines = lines.slice(0, maxLines);
  let summary = clippedLines.join("\n");
  if (summary.length > maxChars) {
    summary = `${summary.slice(0, Math.max(1, maxChars - 1))}…`;
  } else if (lines.length > maxLines) {
    summary = `${summary}\n…`;
  }
  return summary.trim();
}

function summarizeToolCall(text) {
  const firstLine = String(text || "").split(/\n/)[0] || "";
  return truncateText(firstLine, 88);
}

function summarizeToolOutput(text) {
  const lines = String(text || "")
    .split(/\n+/)
    .map((line) => normalizeLogLine(line))
    .filter(Boolean);
  if (!lines.length) return "";

  const exitLine = lines.find((line) => /^Process exited with code /i.test(line)) || "";
  const outputIndex = lines.findIndex((line) => line === "Output:");
  const outputLines = (outputIndex >= 0 ? lines.slice(outputIndex + 1) : lines)
    .filter((line) => !/^Chunk ID:/i.test(line) && !/^Wall time:/i.test(line) && !/^Original token count:/i.test(line));
  const previewLines = outputLines
    .filter((line) => line && !/^Output:$/i.test(line))
    .slice(0, 2)
    .map((line) => truncateText(line, 92));
  const outputSummary = previewLines.join("\n");
  const hiddenCount = Math.max(0, outputLines.length - previewLines.length);

  if (outputSummary && exitLine && hiddenCount > 0) {
    return `${exitLine}\n${outputSummary}\n另有 ${hiddenCount} 行输出，默认已折叠。`;
  }
  if (outputSummary && exitLine) {
    return `${exitLine}\n${outputSummary}`;
  }
  if (outputSummary) return outputSummary;
  if (exitLine) return exitLine;
  return summarizeMultilineText(lines.join("\n"), { maxLines: 3, maxChars: 180 });
}

function summarizeEventText(event) {
  const text = cleanAssistantMessageText(String(event?.text || "").trim());
  if (!text) return "";
  if (event.kind === "tool_call") return summarizeToolCall(text);
  if (event.kind === "tool_output") return summarizeToolOutput(text);
  return summarizeMultilineText(text, {
    maxLines: event.kind === "final_answer" ? 6 : 4,
    maxChars: event.kind === "final_answer" ? 320 : 220,
  });
}

function isBackgroundToolCall(event) {
  if (String(event?.kind || "") !== "tool_call") return false;
  if (String(event?.tool_name || "").trim() !== "write_stdin") return false;
  const text = String(event?.text || "");
  return /"chars"\s*:\s*""/.test(text);
}

function buildTaskProgress(turn) {
  const trackedCalls = turn.events.filter((event) => String(event?.kind || "") === "tool_call" && !isBackgroundToolCall(event));
  const trackedCallIds = new Set(
    trackedCalls.map((event) => String(event?.call_id || "").trim()).filter(Boolean),
  );
  const total = trackedCallIds.size || trackedCalls.length;
  const completedIds = new Set(
    turn.events
      .filter((event) => String(event?.kind || "") === "tool_output")
      .map((event) => String(event?.call_id || "").trim())
      .filter((id) => !trackedCallIds.size || trackedCallIds.has(id)),
  );
  const completed = Math.min(total, completedIds.size || 0);
  return {
    total,
    completed,
    pending: Math.max(0, total - completed),
  };
}

function buildPhaseLabel({ outcome, reasoningCount, commentaryCount, taskProgress, latestKind }) {
  if (outcome === "result") return "已返回结果";
  if (outcome === "aborted") return "已中断";
  if (latestKind === "reasoning") return "正在思考";
  if (taskProgress.total > 0 && taskProgress.completed < taskProgress.total) return "正在执行";
  if (commentaryCount > 0) return "正在整理";
  if (reasoningCount > 0) return "正在思考";
  return "正在处理中";
}

function buildAssistantTurn(turn) {
  const commentary = [];
  const finalAnswer = [];
  const reasoning = [];
  const toolRows = [];
  const completeRows = [];
  const abortedRows = [];

  turn.events.forEach((event) => {
    const text = cleanAssistantMessageText(String(event.text || "").trim());
    if (event.kind === "commentary" && text) commentary.push(text);
    if (event.kind === "final_answer" && text) finalAnswer.push(text);
    if (event.kind === "reasoning" && text) reasoning.push(text);
    if ((event.kind === "tool_call" || event.kind === "tool_output") && text) toolRows.push(text);
    if (event.kind === "task_complete" && text) completeRows.push(text);
    if (event.kind === "task_aborted" && text) abortedRows.push(text);
  });

  const resultText = finalAnswer[finalAnswer.length - 1] || "";
  const rawProcessText =
    commentary[commentary.length - 1] ||
    reasoning[reasoning.length - 1] ||
    toolRows[toolRows.length - 1] ||
    completeRows[completeRows.length - 1] ||
    "";
  const processText = rawProcessText && rawProcessText !== resultText ? rawProcessText : "";
  const abortedText = abortedRows[abortedRows.length - 1] || "";
  const hasResult = !!resultText;
  const outcome = hasResult ? "result" : abortedText ? "aborted" : processText ? "update" : "idle";
  const latestMeaningfulEvent = [...turn.events]
    .reverse()
    .find((event) => String(event?.text || event?.title || "").trim()) || null;
  const taskProgress = buildTaskProgress(turn);
  const phaseLabel = buildPhaseLabel({
    outcome,
    reasoningCount: reasoning.length,
    commentaryCount: commentary.length,
    taskProgress,
    latestKind: latestMeaningfulEvent?.kind || "",
  });
  const statusLabel =
    outcome === "result" ? "已返回结果" :
    outcome === "aborted" ? "本轮中断" :
    outcome === "update" ? "过程更新" :
    "处理中";

  const toolCallIds = new Set(
    turn.events.map((event) => String(event.call_id || "").trim()).filter(Boolean),
  );
  const toolCount = toolCallIds.size || turn.events.filter((event) => event.kind === "tool_call").length;
  const badges = [];
  badges.push(statusLabel);
  if (reasoning.length) badges.push(`思考 ${reasoning.length}`);
  if (taskProgress.total) badges.push(`任务 ${taskProgress.completed}/${taskProgress.total}`);
  if (toolCount) badges.push(`工具 ${toolCount}`);
  if (completeRows.length && !hasResult) badges.push("已结束");
  if (abortedRows.length) badges.push("需关注");

  const detailRows = turn.events
    .filter((event) => String(event.text || event.title || "").trim())
    .filter((event) => {
      if (event.kind === "final_answer" && hasResult) return false;
      if (event.kind === "task_complete" && hasResult && cleanAssistantMessageText(String(event.text || "").trim()) === resultText) return false;
      return true;
    })
    .map((event) => ({
      kind: event.kind,
      label: timelineKindLabel(event.kind),
      text: summarizeEventText(event) || String(event.text || event.title || "").trim(),
      fullText: cleanAssistantMessageText(String(event.text || event.title || "").trim()),
      time: formatTs(event.timestamp),
      mono: event.kind === "tool_call" || event.kind === "tool_output",
      expanded: event.kind === "tool_output" && String(event.text || "").length > 240,
    }));
  const maxDetails = hasResult ? 18 : 24;
  const details = detailRows.length > maxDetails ? detailRows.slice(-maxDetails) : detailRows;
  const hiddenDetailCount = Math.max(0, detailRows.length - details.length);

  const detailSummary = ["查看过程"];
  if (reasoning.length) detailSummary.push(`思考 ${reasoning.length}`);
  if (toolCount) detailSummary.push(`工具 ${toolCount}`);
  if (hiddenDetailCount > 0) {
    detailSummary.push(`最近 ${details.length}/${detailRows.length} 条`);
  } else if (details.length > 1) {
    detailSummary.push(`${details.length} 条`);
  }

  return {
    type: "assistant",
    id: turn.id,
    ts: turn.ts,
    lastTs: turn.lastTs,
    text: resultText || processText || abortedText || "Codex 已收到任务，正在继续处理。",
    resultText,
    processText: hasResult ? processText : "",
    abortedText,
    outcome,
    statusLabel,
    phaseLabel,
    taskProgress,
    hasResult,
    badges,
    details,
    detailSummary: detailSummary.join(" · "),
    error: abortedRows.length > 0,
  };
}

export function buildConversationItems(events = []) {
  const items = [];
  let activeDay = "";
  let assistantTurn = null;

  function flushAssistant() {
    if (!assistantTurn) return;
    items.push(buildAssistantTurn(assistantTurn));
    assistantTurn = null;
  }

  events.forEach((event) => {
    if (!event) return;
    const dateKey = timelineDateKey(event.timestamp);
    if (dateKey && dateKey !== activeDay) {
      flushAssistant();
      items.push({ type: "day", id: `day:${dateKey}`, label: formatDayLabel(event.timestamp) });
      activeDay = dateKey;
    }

    if (event.kind === "user_message") {
      flushAssistant();
      items.push({
        type: "user",
        id: event.id,
        text: cleanPromptText(event.text),
        ts: event.timestamp,
        pending: !!event.pending,
      });
      return;
    }

    if (event.kind === "task_started") {
      flushAssistant();
      items.push({
        type: "system",
        id: event.id,
        text: String(event.text || event.title || "开始处理").trim(),
        ts: event.timestamp,
        tone: "info",
      });
      return;
    }

    if (event.kind === "task_aborted" && !assistantTurn) {
      items.push({
        type: "system",
        id: event.id,
        text: String(event.text || event.title || "任务中断").trim(),
        ts: event.timestamp,
        tone: "error",
      });
      return;
    }

    if (!assistantTurn) {
      assistantTurn = {
        id: `assistant:${event.id}`,
        ts: event.timestamp,
        lastTs: event.timestamp,
        events: [],
      };
    }
    assistantTurn.events.push(event);
    assistantTurn.lastTs = event.timestamp || assistantTurn.lastTs;
  });

  flushAssistant();
  return items;
}
