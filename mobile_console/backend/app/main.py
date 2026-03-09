from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from .db import StateDB
from .event_parser import detect_status, has_approval_request, split_incremental
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


def sse(event: str, payload: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def ensure_default_repo() -> Dict[str, Any]:
    try:
        return mgr.ensure_repo(str(SETTINGS.default_repo))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def visible_repo_rows() -> list[Dict[str, Any]]:
    rows = db.list_repos()
    visible: list[Dict[str, Any]] = []
    for row in rows:
        path = Path(row["path"])
        if not path.exists() or not path.is_dir():
            continue
        if not mgr.is_path_in_workspace(path):
            continue
        visible.append(row)
    return visible


def repo_detail(row: Dict[str, Any]) -> RepoInfo:
    status = git_status(Path(row["path"]))
    try:
        mgr.sync_repo_sessions(row["id"])
    except Exception:
        # tmux discovery failures should not block repo listing.
        pass
    sess = db.latest_session_for_repo(row["id"])
    return RepoInfo(
        id=row["id"],
        name=row["name"],
        path=row["path"],
        branch=status.get("branch", "(unknown)"),
        dirty_files=len(status.get("dirty_files", [])),
        session_status=sess["status"] if sess else "idle",
        updated_at=row.get("updated_at"),
    )


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
def list_repo_sessions(repo_id: str) -> List[SessionInfo]:
    try:
        rows = mgr.sync_repo_sessions(repo_id)
    except Exception:
        rows = db.list_sessions_for_repo(repo_id)
    return [SessionInfo(**r) for r in rows]


@app.post("/api/repos/{repo_id}/sessions/new")
def new_session(repo_id: str) -> SessionInfo:
    try:
        row = mgr.start_new(repo_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SessionInfo(**row)


@app.post("/api/repos/{repo_id}/sessions/resume")
def resume_session(repo_id: str) -> SessionInfo:
    try:
        row = mgr.resume(repo_id)
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


@app.get("/api/sessions/{session_id}/snapshot")
def session_snapshot(session_id: str, lines: int = Query(220, ge=20, le=2000)) -> Dict[str, Any]:
    sess = db.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="session not found")

    try:
        out = mgr.session_output(session_id, lines=lines)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    status = detect_status(out)
    return {
        "session_id": session_id,
        "tmux_session": sess["tmux_session"],
        "status": status,
        "updated_at": sess.get("updated_at"),
        "lines": out[-lines:],
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
        last_status = sess["status"]
        last_block: str | None = None
        try:
            old_lines = await asyncio.to_thread(mgr.session_output, session_id, 400)
        except Exception:
            old_lines = []

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

            status = detect_status(lines)
            if status != last_status:
                last_status = status
                yield sse("status", {"status": status, "session_id": session_id})

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
