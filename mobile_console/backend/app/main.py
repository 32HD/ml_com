from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from .db import StateDB, utc_now
from .event_parser import (
    detect_status,
    extract_terminal_latest_answer,
    extract_terminal_latest_prompt,
    extract_terminal_turns,
    has_approval_request,
    split_incremental,
)
from .file_service import FileService
from .git_service import git_diff, git_status
from .models import (
    DecisionRequest,
    FileWriteRequest,
    KeyRequest,
    ProjectInitRequest,
    ProjectInitResponse,
    PromptRequest,
    RenameSessionRequest,
    RepoInfo,
    RepoOpenRequest,
    RunCmdRequest,
    RunTestRequest,
    SessionFocusRequest,
    SessionHubResponse,
    SessionLaunchRequest,
    SessionInfo,
)
from .session_manager import SessionManager
from .settings import SETTINGS


app = FastAPI(title="codex-bridge", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

db = StateDB(SETTINGS.state_db)
mgr = SessionManager(db)


def _stable_terminal_event_id(prefix: str, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}:{digest}"


def extract_terminal_prompt(lines: List[str]) -> str:
    return extract_terminal_latest_prompt(lines)


def terminal_timeline_events(session_id: str, lines: List[str]) -> List[Dict[str, Any]]:
    stored_events = db.list_session_events(session_id, limit=240)
    stored_by_id = {str(event.get("id") or ""): event for event in stored_events if event.get("id")}

    fresh_events: List[Dict[str, Any]] = []
    turns = extract_terminal_turns(lines, max_turns=8)
    if turns:
        for turn in turns:
            prompt = str(turn.get("prompt") or "").strip()
            answer = str(turn.get("answer") or "").strip()
            if not prompt:
                continue

            pair_key = f"{prompt}\n{answer}" if answer else prompt
            user_event_id = _stable_terminal_event_id("terminal:user", pair_key)
            existing_user = stored_by_id.get(user_event_id) or {}
            fresh_events.append(
                {
                    "id": user_event_id,
                    "timestamp": existing_user.get("timestamp") or utc_now(),
                    "kind": "user_message",
                    "title": "你",
                    "text": prompt,
                    "source": "terminal",
                }
            )

            if not answer:
                continue

            assistant_event_id = _stable_terminal_event_id("terminal:assistant", pair_key)
            existing_assistant = stored_by_id.get(assistant_event_id) or {}
            fresh_events.append(
                {
                    "id": assistant_event_id,
                    "timestamp": existing_assistant.get("timestamp") or utc_now(),
                    "kind": "final_answer",
                    "title": "Codex",
                    "text": answer,
                    "prompt": prompt,
                    "source": "terminal",
                }
            )
        return fresh_events

    fallback_prompt = extract_terminal_latest_prompt(lines)
    if fallback_prompt:
        event_id = _stable_terminal_event_id("bootstrap:user", fallback_prompt)
        existing = stored_by_id.get(event_id) or {}
        fresh_events.append(
            {
                "id": event_id,
                "timestamp": existing.get("timestamp") or utc_now(),
                "kind": "user_message",
                "title": "最近任务",
                "text": fallback_prompt,
                "source": "terminal",
            }
        )

    reply = extract_terminal_latest_answer(lines)
    if reply and reply.get("answer"):
        key = f"{reply.get('prompt', '')}\n{reply.get('answer', '')}"
        event_id = _stable_terminal_event_id("terminal:assistant", key)
        existing = stored_by_id.get(event_id) or {}
        fresh_events.append(
            {
                "id": event_id,
                "timestamp": existing.get("timestamp") or utc_now(),
                "kind": "final_answer",
                "title": "Codex",
                "text": str(reply.get("answer") or "").strip(),
                "prompt": str(reply.get("prompt") or "").strip(),
                "source": "terminal",
            }
        )

    return fresh_events


def merged_session_timeline(
    session_id: str,
    live_events: List[Dict[str, Any]],
    terminal_lines: List[str],
    *,
    include_terminal_events: bool = True,
    limit: int = 180,
) -> List[Dict[str, Any]]:
    if live_events:
        db.upsert_session_events(session_id, live_events)
    terminal_events = terminal_timeline_events(session_id, terminal_lines) if include_terminal_events else []
    if terminal_events:
        db.upsert_session_events(session_id, terminal_events)
    return db.list_session_events(session_id, limit=limit)


def sse(event: str, payload: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def ensure_default_repo() -> Dict[str, Any]:
    try:
        return mgr.ensure_repo(str(SETTINGS.default_repo))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def visible_repo_rows() -> list[Dict[str, Any]]:
    rows = db.list_repos()
    grouped: dict[str, list[Dict[str, Any]]] = {}
    for row in rows:
        path = Path(row["path"])
        if not path.exists() or not path.is_dir():
            continue
        if not mgr.is_manageable_repo_path(path):
            continue
        if not mgr._looks_like_project_dir(path):
            continue
        grouped.setdefault(path.name.lower(), []).append(row)

    visible: list[Dict[str, Any]] = []
    for same_name_rows in grouped.values():
        if len(same_name_rows) == 1:
            visible.append(same_name_rows[0])
            continue

        def sort_key(row: Dict[str, Any]) -> tuple[Any, ...]:
            path = Path(row["path"])
            session = db.latest_session_for_repo(row["id"]) or {}
            return (
                1 if (path / ".git").exists() else 0,
                1 if session else 0,
                str(session.get("last_activity_at") or session.get("updated_at") or ""),
                str(row.get("updated_at") or ""),
                str(row.get("id") or ""),
            )

        visible.append(sorted(same_name_rows, key=sort_key, reverse=True)[0])
    return visible


def repo_detail(row: Dict[str, Any]) -> RepoInfo:
    status = git_status(Path(row["path"]))
    sess = db.latest_session_for_repo(row["id"])
    try:
        default_repo = mgr.canonical_repo_path(SETTINGS.default_repo)
    except Exception:
        default_repo = SETTINGS.default_repo.resolve()
    try:
        row_path = mgr.canonical_repo_path(Path(row["path"]))
    except Exception:
        row_path = Path(row["path"]).resolve()
    return RepoInfo(
        id=row["id"],
        name=row["name"],
        path=row["path"],
        branch=status.get("branch", "(unknown)"),
        dirty_files=len(status.get("dirty_files", [])),
        session_status=sess["status"] if sess else "idle",
        updated_at=row.get("updated_at"),
        is_default=row_path == default_repo,
    )


def _timeline_has_terminal_outcome(events: List[Dict[str, Any]]) -> bool:
    for event in reversed(events or []):
        kind = str(event.get("kind") or "").strip()
        if not kind:
            continue
        return kind in {"final_answer", "task_complete", "task_aborted"}
    return False


def _resolve_session_status(sess: Dict[str, Any], lines: List[str], timeline_events: List[Dict[str, Any]]) -> str:
    persisted = str(sess.get("status") or "idle").strip() or "idle"
    derived = detect_status(lines) if lines else persisted
    if derived in {"waiting_approval", "failed", "running"}:
        return derived
    if persisted in {"running", "waiting_input", "waiting_approval"} and not _timeline_has_terminal_outcome(timeline_events):
        return persisted
    return derived or persisted or "idle"


@app.get("/healthz")
def healthz() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/healthz")
def api_healthz() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/repos")
def list_repos() -> List[RepoInfo]:
    try:
        mgr.discover_workspace_repos()
    except Exception:
        # Discovery errors should not make the whole API unavailable.
        pass
    try:
        mgr.discover_recent_session_repos()
    except Exception:
        # Session-based discovery is best effort.
        pass

    repos = visible_repo_rows()
    if not repos and SETTINGS.default_repo.exists() and mgr.is_path_in_workspace(SETTINGS.default_repo):
        ensure_default_repo()
        repos = visible_repo_rows()
    return [repo_detail(r) for r in repos]


@app.post("/api/repos/open")
def open_repo(req: RepoOpenRequest) -> RepoInfo:
    try:
        row = mgr.ensure_repo(req.path, req.name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return repo_detail(row)


@app.post("/api/projects/init")
def init_project(req: ProjectInitRequest) -> ProjectInitResponse:
    try:
        data = mgr.init_project(req)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    repo = repo_detail(data["repo"])
    return ProjectInitResponse(
        repo=repo,
        github_owner=data.get("github_owner"),
        github_repo=data.get("github_repo"),
        github_url=data.get("github_url"),
        remote_url=data.get("remote_url"),
        existed=data.get("existed"),
    )


@app.get("/api/repos/{repo_id}/sessions")
def list_repo_sessions(repo_id: str, force: bool = Query(False)) -> List[SessionInfo]:
    try:
        rows = mgr.sync_repo_sessions(repo_id, force=force)
    except Exception:
        rows = db.list_sessions_for_repo(repo_id)
    return [SessionInfo(**r) for r in rows]


@app.get("/api/repos/{repo_id}/session-hub")
def repo_session_hub(repo_id: str, force: bool = Query(False)) -> SessionHubResponse:
    try:
        data = mgr.build_session_hub(repo_id, force=force)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SessionHubResponse(
        repo_id=data["repo_id"],
        generated_at=data["generated_at"],
        focus_session_id=data.get("focus_session_id"),
        focus_reason=data.get("focus_reason"),
        focus_updated_at=data.get("focus_updated_at"),
        current_session_id=data.get("current_session_id"),
        suggested_session_id=data.get("suggested_session_id"),
        shared_session_id=data.get("shared_session_id"),
        live_session_ids=data.get("live_session_ids", []),
        recent_session_ids=data.get("recent_session_ids", []),
        archived_session_ids=data.get("archived_session_ids", []),
        external_recent_session_ids=data.get("external_recent_session_ids", []),
        sync_hint=data.get("sync_hint"),
        sessions=[SessionInfo(**row) for row in data.get("sessions", [])],
    )


@app.post("/api/repos/{repo_id}/sessions/new")
def new_session(repo_id: str, req: SessionLaunchRequest | None = None) -> SessionInfo:
    try:
        row = mgr.start_new(repo_id, execution_mode=(req.execution_mode if req else None))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SessionInfo(**row)


@app.post("/api/repos/{repo_id}/sessions/resume")
def resume_session(repo_id: str, req: SessionLaunchRequest | None = None) -> SessionInfo:
    try:
        row = mgr.resume(repo_id, execution_mode=(req.execution_mode if req else None))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SessionInfo(**row)


@app.post("/api/sessions/{session_id}/resume")
def resume_session_by_id(session_id: str) -> SessionInfo:
    try:
        row = mgr.resume_session_by_id(session_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SessionInfo(**row)


@app.post("/api/sessions/{session_id}/prompt")
def send_prompt(session_id: str, req: PromptRequest) -> SessionInfo:
    try:
        row = mgr.send_prompt(session_id, req.prompt)
        db.add_session_event(
            session_id,
            event_id=f"mobile:user:{uuid.uuid4().hex}",
            kind="user_message",
            title="你的任务",
            text=req.prompt,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SessionInfo(**row)


@app.post("/api/sessions/{session_id}/key")
def send_key(session_id: str, req: KeyRequest) -> SessionInfo:
    try:
        row = mgr.send_key(session_id, req.key, repeat=req.repeat)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SessionInfo(**row)


@app.post("/api/sessions/{session_id}/rename")
def rename_session(session_id: str, req: RenameSessionRequest) -> SessionInfo:
    try:
        row = mgr.rename_session(session_id, req.name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SessionInfo(**row)


@app.post("/api/sessions/{session_id}/focus")
def focus_session(session_id: str, req: SessionFocusRequest | None = None) -> SessionInfo:
    try:
        row = mgr.mark_session_focus(session_id, reason=(req.reason if req else None) or "manual")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SessionInfo(**row)


@app.get("/api/sessions/{session_id}/snapshot")
def session_snapshot(
    session_id: str,
    lines: int = Query(160, ge=20, le=1200),
    timeline_limit: int = Query(320, ge=40, le=800),
) -> Dict[str, Any]:
    sess = db.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="session not found")

    try:
        out = mgr.session_output(session_id, lines=lines)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    effective_timeline_limit = max(40, timeline_limit)
    timeline = mgr.session_timeline(session_id, limit=effective_timeline_limit)
    include_terminal_events = not timeline.get("codex_session_id")
    merged_timeline = merged_session_timeline(
        session_id,
        timeline["events"],
        out,
        include_terminal_events=include_terminal_events,
        limit=effective_timeline_limit,
    )
    if not merged_timeline:
        fallback_prompt = extract_terminal_prompt(out)
        if fallback_prompt:
            db.add_session_event(
                session_id,
                event_id="bootstrap:terminal_prompt",
                kind="user_message",
                title="最近任务",
                text=fallback_prompt,
            )
            merged_timeline = db.list_session_events(session_id, limit=effective_timeline_limit)

    status = _resolve_session_status(sess, out, merged_timeline)
    return {
        "session_id": session_id,
        "tmux_session": sess["tmux_session"],
        "status": status,
        "updated_at": sess.get("updated_at"),
        "last_activity_at": sess.get("last_activity_at"),
        "lines": out[-lines:],
        "timeline": merged_timeline,
        "codex_session_id": timeline.get("codex_session_id"),
        "codex_source": timeline.get("codex_source"),
        "codex_model": timeline.get("codex_model"),
    }


@app.get("/api/sessions/{session_id}/stream")
async def stream(
    session_id: str,
    request: Request,
    seed_lines: int = Query(12, ge=0, le=200),
) -> StreamingResponse:
    sess = db.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="session not found")

    async def gen():
        old_lines: list[str] = []
        approval_emitted = False
        last_status = str(sess.get("status") or "idle")
        last_block: str | None = None
        seen_timeline_ids: set[str] = set()
        last_timeline_meta: tuple[str | None, str | None, str | None] | None = None
        try:
            old_lines = await asyncio.to_thread(mgr.session_output, session_id, 400)
        except Exception:
            old_lines = []

        try:
            stored_events = await asyncio.to_thread(db.list_session_events, session_id, 400)
            seen_timeline_ids = {str(event.get("id") or "") for event in stored_events if event.get("id")}
        except Exception:
            stored_events = []
            seen_timeline_ids = set()

        last_status = _resolve_session_status(sess, old_lines, stored_events)

        # Give a tiny tail for context, but avoid replaying huge history on reconnect.
        if seed_lines > 0 and old_lines:
            seed = "\n".join(old_lines[-seed_lines:])
            if seed.strip():
                yield sse("message", {"line": seed})

        yield sse("status", {"status": last_status, "session_id": session_id})
        while True:
            if await request.is_disconnected():
                break
            try:
                lines = await asyncio.to_thread(mgr.session_output, session_id, 400)
            except Exception as exc:  # noqa: BLE001
                yield sse("error", {"message": str(exc)})
                await asyncio.sleep(1.0)
                continue

            delta, old_lines = split_incremental(old_lines, lines)
            if delta:
                block = "\n".join(delta[-80:])
                if len(block) > 12000:
                    block = block[-12000:]
                if block.strip() and block != last_block:
                    last_block = block
                    yield sse("message", {"line": block})

            timeline = await asyncio.to_thread(mgr.session_timeline, session_id, 120)
            timeline_meta = (
                timeline.get("codex_session_id"),
                timeline.get("codex_source"),
                timeline.get("codex_model"),
            )
            if timeline_meta != last_timeline_meta:
                last_timeline_meta = timeline_meta
                yield sse(
                    "session_meta",
                    {
                        "session_id": session_id,
                        "codex_session_id": timeline.get("codex_session_id"),
                        "codex_source": timeline.get("codex_source"),
                        "codex_model": timeline.get("codex_model"),
                    },
                )
            live_events = timeline.get("events", [])
            terminal_events = []
            if not timeline.get("codex_session_id"):
                terminal_events = await asyncio.to_thread(terminal_timeline_events, session_id, lines)
            status = _resolve_session_status(sess, lines, [*live_events, *terminal_events] or stored_events)
            if status != last_status:
                last_status = status
                yield sse("status", {"status": status, "session_id": session_id})
            if live_events:
                await asyncio.to_thread(db.upsert_session_events, session_id, live_events)
            if terminal_events:
                await asyncio.to_thread(db.upsert_session_events, session_id, terminal_events)
            for event in [*live_events, *terminal_events]:
                event_id = str(event.get("id") or "")
                if not event_id or event_id in seen_timeline_ids:
                    continue
                seen_timeline_ids.add(event_id)
                yield sse("timeline_event", event)

            if has_approval_request(lines) and not approval_emitted:
                approval_emitted = True
                yield sse(
                    "approval_request",
                    {
                        "session_id": session_id,
                        "summary": "Codex requested confirmation in terminal output",
                        "risk": "unknown",
                    },
                )
            if status != "waiting_approval":
                approval_emitted = False

            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/sessions/{session_id}/approve")
def approve(session_id: str, _req: DecisionRequest) -> SessionInfo:
    try:
        row = mgr.approve(session_id, approve=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SessionInfo(**row)


@app.post("/api/sessions/{session_id}/reject")
def reject(session_id: str, _req: DecisionRequest) -> SessionInfo:
    try:
        row = mgr.approve(session_id, approve=False)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SessionInfo(**row)


@app.get("/api/repos/{repo_id}/git/status")
def api_git_status(repo_id: str) -> Dict[str, Any]:
    repo = db.get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="repo not found")
    return git_status(Path(repo["path"]))


@app.get("/api/repos/{repo_id}/git/diff")
def api_git_diff(repo_id: str) -> Dict[str, Any]:
    repo = db.get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="repo not found")
    data = git_diff(Path(repo["path"]))
    data["diff"] = data["diff"][: SETTINGS.max_diff_chars]
    return data


@app.get("/api/repos/{repo_id}/git/diff/{file_path:path}")
def api_git_diff_file(repo_id: str, file_path: str) -> Dict[str, Any]:
    repo = db.get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="repo not found")
    data = git_diff(Path(repo["path"]), file_path=file_path)
    data["diff"] = data["diff"][: SETTINGS.max_diff_chars]
    return data


@app.get("/api/repos/{repo_id}/files/recent")
def recent_files(repo_id: str) -> Dict[str, Any]:
    repo = db.get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="repo not found")
    st = git_status(Path(repo["path"]))
    files = st.get("dirty_files", [])
    return {"files": files[:100]}


@app.get("/api/repos/{repo_id}/files/tree")
def files_tree(repo_id: str, path: str = Query("."), depth: int = Query(2, ge=0, le=5)) -> Dict[str, Any]:
    repo = db.get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="repo not found")
    try:
        data = FileService.tree(Path(repo["path"]), rel=path, depth=depth)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": data}


@app.get("/api/repos/{repo_id}/file")
def read_file(repo_id: str, path: str = Query(...)) -> Dict[str, Any]:
    repo = db.get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="repo not found")
    try:
        return FileService.read_file(Path(repo["path"]), path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/repos/{repo_id}/file")
def write_file(repo_id: str, req: FileWriteRequest) -> Dict[str, Any]:
    repo = db.get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="repo not found")
    try:
        return FileService.write_file(Path(repo["path"]), req.path, req.content)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/repos/{repo_id}/run/cmd")
def run_cmd(repo_id: str, req: RunCmdRequest) -> Dict[str, Any]:
    try:
        return mgr.run_repo_cmd(repo_id, req.cmd)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/repos/{repo_id}/run/test")
def run_test(repo_id: str, req: RunTestRequest) -> Dict[str, Any]:
    target = (req.target or "pytest -q").strip()
    cmd = target if target else "pytest -q"
    try:
        return mgr.run_repo_cmd(repo_id, cmd)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": str(exc)})
