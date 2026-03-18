import React from "react";

import {
  STATUS_LABELS,
  executionModeLabel,
  formatTs,
  sessionActivityAt,
  sessionDisplayTitle,
  sessionOriginLabel,
  sessionPreviewText,
  sessionSyncSummary,
  statusClass,
} from "../lib/chat";

function SessionMeta({ session }) {
  if (!session) return null;
  const parts = [sessionOriginLabel(session)];
  if (session.execution_mode) parts.push(executionModeLabel(session.execution_mode));
  parts.push(STATUS_LABELS[session.status] || session.status || "空闲");
  return <div className="session-hub-card-meta">{parts.filter(Boolean).join(" · ")}</div>;
}

function SessionCard({ session, tone = "default", marker = "", active = false, onContinue, onPreview }) {
  if (!session) return null;
  const activity = sessionActivityAt(session);
  return (
    <article className={`session-hub-card ${tone}${active ? " active" : ""}`}>
      <div className="session-hub-card-top">
        <span className={`session-dot ${statusClass(session.status)}`} />
        <span className="session-hub-card-origin">{sessionOriginLabel(session)}</span>
        {marker ? <span className="session-hub-card-marker">{marker}</span> : null}
      </div>
      <div className="session-hub-card-title">{sessionDisplayTitle(session)}</div>
      <SessionMeta session={session} />
      <div className="session-hub-card-copy">{sessionPreviewText(session)}</div>
      <div className="session-hub-card-sub">{sessionSyncSummary(session)}</div>
      <div className="session-hub-card-sub">{activity ? `更新时间 ${formatTs(activity)}` : "等待同步"}</div>
      <div className="session-hub-card-actions">
        <button className="ghost-btn small-btn" type="button" onClick={onPreview}>历史</button>
        <button className="primary small-btn" type="button" onClick={onContinue}>继续</button>
      </div>
    </article>
  );
}

export function SessionHub({
  currentSession,
  suggestedSession,
  sharedSession,
  externalRecentSessions = [],
  recentSessions = [],
  syncHint = "",
  onPreviewSession,
  onContinueSession,
  onResumeShared,
  onSwitchLatest,
  onOpenSwitcher,
  onSync,
}) {
  const hasSuggestedSwitch = suggestedSession && currentSession && suggestedSession.id !== currentSession.id;
  const railSessions = [
    ...externalRecentSessions,
    ...recentSessions.filter((session) => !currentSession || session.id !== currentSession.id),
  ].slice(0, 6);

  return (
    <section className="session-hub">
      <div className="session-hub-head">
        <div>
          <div className="eyebrow">Sessions</div>
          <div className="session-hub-title">当前项目会话</div>
        </div>
        <div className="session-hub-head-actions">
          <button className="tool-chip" type="button" onClick={onSync}>同步</button>
          <button className="tool-chip" type="button" onClick={onOpenSwitcher}>全部会话</button>
        </div>
      </div>

      {syncHint ? <div className="session-sync-banner">{syncHint}</div> : null}

      <div className="session-hub-featured">
        <div className="session-hub-primary">
          {currentSession ? (
            <SessionCard
              session={currentSession}
              tone="current"
              marker="当前查看"
              active
              onPreview={() => onPreviewSession(currentSession)}
              onContinue={() => onContinueSession(currentSession)}
            />
          ) : (
            <article className="session-hub-empty">
              <div className="session-hub-card-title">还没有选中会话</div>
              <div className="session-hub-card-copy">发消息时会自动恢复项目共享会话，也可以先从最近会话里挑一条历史继续。</div>
              <div className="session-hub-card-actions">
                <button className="primary small-btn" type="button" onClick={onResumeShared}>恢复主会话</button>
                <button className="ghost-btn small-btn" type="button" onClick={onOpenSwitcher}>查看列表</button>
              </div>
            </article>
          )}
        </div>

        <div className="session-hub-side">
          {hasSuggestedSwitch ? (
            <SessionCard
              session={suggestedSession}
              tone="suggested"
              marker="推荐切换"
              onPreview={() => onPreviewSession(suggestedSession)}
              onContinue={() => onSwitchLatest()}
            />
          ) : null}
          {sharedSession && (!currentSession || sharedSession.id !== currentSession.id) ? (
            <SessionCard
              session={sharedSession}
              tone="shared"
              marker="项目主线"
              onPreview={() => onPreviewSession(sharedSession)}
              onContinue={onResumeShared}
            />
          ) : null}
        </div>
      </div>

      {railSessions.length ? (
        <div className="session-rail">
          {railSessions.map((session) => (
            <SessionCard
              key={session.id}
              session={session}
              tone="mini"
              onPreview={() => onPreviewSession(session)}
              onContinue={() => onContinueSession(session)}
            />
          ))}
        </div>
      ) : null}
    </section>
  );
}
