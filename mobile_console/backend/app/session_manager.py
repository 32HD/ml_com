from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from .db import StateDB
from .event_parser import detect_status
from .github_service import GitHubError, create_repo
from .models import ProjectInitRequest
from .settings import SETTINGS
from .tmux_adapter import (
    capture_lines,
    create_session,
    list_session_names,
    normalize_repo_id,
    session_current_path,
    send_key as tmux_send_key,
    send_input,
    session_exists,
    tmux_session_name,
)


class SessionManager:
    VSCODE_SESSION_PREFIX = "vscode:"

    def __init__(self, db: StateDB) -> None:
        self.db = db

    def repo_id_for_path(self, path: str) -> str:
        rid = normalize_repo_id(path)
        stable = hashlib.sha1(Path(path).resolve().as_posix().encode("utf-8")).hexdigest()[:8]
        return f"{rid}_{stable}"

    def workspace_root(self) -> Path:
        return SETTINGS.workspace_root.expanduser().resolve()

    def is_path_in_workspace(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.workspace_root())
            return True
        except ValueError:
            return False

    def _assert_in_workspace(self, path: Path) -> None:
        if not self.is_path_in_workspace(path):
            raise ValueError(f"Path must be under workspace root: {self.workspace_root()}")

    def ensure_repo(self, path: str, name: Optional[str] = None) -> Dict[str, Any]:
        repo_path = Path(path).expanduser().resolve()
        if not repo_path.exists() or not repo_path.is_dir():
            raise ValueError(f"Repo path does not exist: {repo_path}")
        self._assert_in_workspace(repo_path)
        rid = self.repo_id_for_path(str(repo_path))
        self.db.upsert_repo(rid, name or repo_path.name, str(repo_path))
        repo = self.db.get_repo(rid)
        if not repo:
            raise RuntimeError("failed to save repo")
        return repo

    def discover_workspace_repos(self) -> list[Dict[str, Any]]:
        root = self.workspace_root()
        if not root.exists() or not root.is_dir():
            return []

        discovered: list[Dict[str, Any]] = []
        for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir():
                continue
            if child.name.startswith("."):
                continue
            repo = self.ensure_repo(str(child), name=child.name)
            discovered.append(repo)
        return discovered

    def _run_git(self, args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
        cp = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            text=True,
            capture_output=True,
            check=False,
        )
        if check and cp.returncode != 0:
            detail = (cp.stderr or cp.stdout or "").strip()
            raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
        return cp

    def _set_origin(self, repo_path: Path, remote_url: str) -> None:
        exists = self._run_git(["remote", "get-url", "origin"], cwd=repo_path, check=False)
        if exists.returncode == 0:
            self._run_git(["remote", "set-url", "origin", remote_url], cwd=repo_path, check=True)
        else:
            self._run_git(["remote", "add", "origin", remote_url], cwd=repo_path, check=True)

    def _ensure_initial_commit(self, repo_path: Path) -> None:
        has_head = self._run_git(["rev-parse", "--verify", "HEAD"], cwd=repo_path, check=False)
        if has_head.returncode == 0:
            return
        self._run_git(["add", "-A"], cwd=repo_path, check=True)
        self._run_git(["commit", "-m", "chore: bootstrap project"], cwd=repo_path, check=True)

    def init_project(self, req: ProjectInitRequest) -> Dict[str, Any]:
        raw_name = req.name.strip()
        if not raw_name:
            raise ValueError("project name is required")
        safe_name = normalize_repo_id(raw_name)
        if not safe_name:
            raise ValueError("invalid project name")
        if req.create_github_repo and not SETTINGS.github_token:
            raise ValueError("GITHUB_TOKEN is not configured in codex-bridge environment")

        root = self.workspace_root()
        root.mkdir(parents=True, exist_ok=True)
        project_dir = (root / safe_name).resolve()
        self._assert_in_workspace(project_dir)
        if project_dir.exists():
            raise ValueError(f"project already exists: {project_dir}")
        project_dir.mkdir(parents=True, exist_ok=False)

        readme = project_dir / "README.md"
        readme.write_text(f"# {safe_name}\n\nCreated by Codex Mobile.\n", encoding="utf-8")
        (project_dir / ".codex-project").write_text("", encoding="utf-8")

        self._run_git(["init", "-b", "main"], cwd=project_dir, check=True)
        self._run_git(["add", "README.md", ".codex-project"], cwd=project_dir, check=True)
        self._run_git(["commit", "-m", "chore: bootstrap project"], cwd=project_dir, check=True)

        github_owner = None
        github_repo = None
        github_url = None
        remote_url = None
        existed = None

        if req.create_github_repo:
            token = SETTINGS.github_token or ""
            repo_name = (req.github_repo or safe_name).strip() or safe_name
            owner = (req.github_owner or SETTINGS.github_owner or "").strip() or None
            private = SETTINGS.github_default_private if req.private is None else bool(req.private)
            description = (req.description or f"{safe_name} managed by Codex Mobile").strip()
            try:
                repo_info = create_repo(
                    token=token,
                    repo=repo_name,
                    owner=owner,
                    description=description,
                    private=private,
                )
            except GitHubError as exc:
                raise ValueError(str(exc)) from exc

            owner_login = str((repo_info.get("owner") or {}).get("login", "")).strip()
            github_owner = owner_login or owner
            github_repo = str(repo_info.get("name", repo_name))
            github_url = str(repo_info.get("html_url", ""))
            remote_url = str(repo_info.get("ssh_url", ""))
            existed = bool(repo_info.get("existed", False))
            if not remote_url:
                if not github_owner:
                    raise ValueError("cannot determine GitHub owner for remote URL")
                remote_url = f"git@github.com:{github_owner}/{github_repo}.git"

            self._set_origin(project_dir, remote_url)
            if req.push_initial_commit:
                self._ensure_initial_commit(project_dir)
                self._run_git(["push", "-u", "origin", "main"], cwd=project_dir, check=True)

        repo = self.ensure_repo(str(project_dir), name=safe_name)
        return {
            "repo": repo,
            "github_owner": github_owner,
            "github_repo": github_repo,
            "github_url": github_url,
            "remote_url": remote_url,
            "existed": existed,
        }

    def _new_tmux_session_name(self, repo_id: str) -> str:
        return f"{tmux_session_name(repo_id)}_{uuid.uuid4().hex[:6]}"

    def _shared_tmux_session_name(self, repo_id: str) -> str:
        return f"{tmux_session_name(repo_id)}_shared"

    def _path_under(self, path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except Exception:
            return False

    def _iter_codex_session_files(self, limit: int = 500) -> list[Path]:
        root = Path.home() / ".codex" / "sessions"
        if not root.exists():
            return []
        files = sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[:limit]

    def _extract_codex_session_meta(self, path: Path) -> dict[str, Any] | None:
        try:
            with path.open("r", encoding="utf-8") as f:
                first = f.readline().strip()
                if not first:
                    return None
                row = json.loads(first)
                if row.get("type") != "session_meta":
                    return None
                payload = row.get("payload", {})
                sid = str(payload.get("id", "")).strip()
                cwd = str(payload.get("cwd", "")).strip()
                origin = str(payload.get("originator", "")).strip()
                if not sid or not cwd:
                    return None

                last_prompt = None
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        item = json.loads(line)
                    except Exception:
                        continue
                    if item.get("type") != "event_msg":
                        continue
                    p = item.get("payload", {})
                    if p.get("type") == "user_message":
                        msg = (p.get("message") or "").strip()
                        if msg:
                            last_prompt = msg
                return {
                    "id": sid,
                    "cwd": cwd,
                    "originator": origin,
                    "last_prompt": last_prompt,
                }
        except Exception:
            return None

    def sync_vscode_recorded_sessions(self, repo_id: str) -> list[Dict[str, Any]]:
        repo = self.db.get_repo(repo_id)
        if not repo:
            return []

        repo_path = Path(repo["path"]).resolve()
        rows = self.db.list_sessions_for_repo(repo_id)
        by_tmux = {r.get("tmux_session"): r for r in rows if r.get("tmux_session")}

        for f in self._iter_codex_session_files():
            meta = self._extract_codex_session_meta(f)
            if not meta:
                continue
            if "vscode" not in (meta.get("originator") or "").lower():
                continue
            cwd = Path(meta["cwd"]).expanduser()
            if not self._path_under(cwd, repo_path):
                continue

            tmux_key = f"{self.VSCODE_SESSION_PREFIX}{meta['id']}"
            existing = by_tmux.get(tmux_key)
            if existing:
                updates: dict[str, Any] = {}
                if meta.get("last_prompt"):
                    updates["last_prompt"] = meta["last_prompt"]
                if existing.get("status") == "failed":
                    updates["status"] = "completed"
                if not existing.get("name"):
                    updates["name"] = f"VSCode {str(meta['id'])[:8]}"
                if updates:
                    self.db.update_session(existing["id"], **updates)
                continue

            created = self.db.create_session(
                repo_id,
                tmux_key,
                "completed",
                name=f"VSCode {str(meta['id'])[:8]}",
            )
            if meta.get("last_prompt"):
                self.db.update_session(created["id"], last_prompt=meta["last_prompt"])

        return self.db.list_sessions_for_repo(repo_id)

    def sync_repo_sessions(self, repo_id: str) -> list[Dict[str, Any]]:
        repo = self.db.get_repo(repo_id)
        if not repo:
            return []

        repo_path = Path(repo["path"]).resolve()
        rows = self.db.list_sessions_for_repo(repo_id)
        by_tmux = {r.get("tmux_session"): r for r in rows if r.get("tmux_session")}
        seen_tmux: set[str] = set()

        prefix = tmux_session_name(repo_id)
        for tmux_name in list_session_names():
            belongs = tmux_name == prefix or tmux_name.startswith(f"{prefix}_")
            if not belongs:
                cwd = session_current_path(tmux_name)
                if cwd:
                    belongs = self._path_under(Path(cwd), repo_path)
            if not belongs:
                continue

            seen_tmux.add(tmux_name)
            existing = by_tmux.get(tmux_name)
            if existing:
                if existing.get("status") != "running":
                    self.db.update_session(existing["id"], status="running")
            else:
                default_name = "共享会话" if tmux_name.endswith("_shared") else tmux_name
                self.db.create_session(repo_id, tmux_name, "running", name=default_name)

        # Keep history, but mark unreachable sessions as failed.
        for row in rows:
            tmux_name = row.get("tmux_session")
            if not tmux_name:
                continue
            if tmux_name not in seen_tmux and not session_exists(tmux_name):
                if row.get("status") not in {"failed", "completed"}:
                    self.db.update_session(row["id"], status="failed")

        self.sync_vscode_recorded_sessions(repo_id)
        return self.db.list_sessions_for_repo(repo_id)

    def start_new(self, repo_id: str) -> Dict[str, Any]:
        repo = self.db.get_repo(repo_id)
        if not repo:
            raise ValueError("repo not found")
        tmux_name = self._new_tmux_session_name(repo_id)
        create_session(tmux_name, repo["path"], str(SETTINGS.codex_wrapper))
        return self.db.create_session(repo_id, tmux_name, "running", name="临时会话")

    def resume(self, repo_id: str) -> Dict[str, Any]:
        repo = self.db.get_repo(repo_id)
        if not repo:
            raise ValueError("repo not found")

        self.sync_repo_sessions(repo_id)

        # Shared per-repo session keeps mobile/web/mac interactions consistent.
        shared_tmux = self._shared_tmux_session_name(repo_id)
        rows = self.db.list_sessions_for_repo(repo_id)
        shared_row = next((r for r in rows if r.get("tmux_session") == shared_tmux), None)

        if session_exists(shared_tmux):
            if shared_row:
                self.db.update_session(shared_row["id"], status="running")
                return self.db.get_session(shared_row["id"]) or shared_row
            return self.db.create_session(repo_id, shared_tmux, "running", name="共享会话")

        create_session(shared_tmux, repo["path"], str(SETTINGS.codex_wrapper))
        if shared_row:
            self.db.update_session(shared_row["id"], status="running")
            return self.db.get_session(shared_row["id"]) or shared_row
        return self.db.create_session(repo_id, shared_tmux, "running", name="共享会话")

    def resume_session_by_id(self, session_id: str) -> Dict[str, Any]:
        sess = self.db.get_session(session_id)
        if not sess:
            raise ValueError("session not found")

        tmux_name = str(sess.get("tmux_session") or "")
        if tmux_name.startswith(self.VSCODE_SESSION_PREFIX):
            external_session_id = tmux_name.split(":", 1)[1].strip()
            repo = self.db.get_repo(sess["repo_id"])
            if not repo:
                raise ValueError("repo not found")
            new_tmux = self._new_tmux_session_name(sess["repo_id"])
            create_session(
                new_tmux,
                repo["path"],
                str(SETTINGS.codex_wrapper),
                extra_args=["resume", external_session_id],
            )
            self.db.update_session(session_id, tmux_session=new_tmux, status="running")
            return self.db.get_session(session_id) or sess

        if not session_exists(sess["tmux_session"]):
            self.db.update_session(session_id, status="failed")
            raise ValueError("selected session is no longer running in tmux")
        self.db.update_session(session_id, status="running")
        return self.db.get_session(session_id) or sess

    def send_prompt(self, session_id: str, prompt: str) -> Dict[str, Any]:
        sess = self.db.get_session(session_id)
        if not sess:
            raise ValueError("session not found")
        if not session_exists(sess["tmux_session"]):
            raise RuntimeError("tmux session not found")
        send_input(sess["tmux_session"], prompt, enter=True)
        self.db.update_session(session_id, status="running", last_prompt=prompt)
        return self.db.get_session(session_id) or sess

    def send_key(self, session_id: str, key: str, repeat: int = 1) -> Dict[str, Any]:
        sess = self.db.get_session(session_id)
        if not sess:
            raise ValueError("session not found")
        if not session_exists(sess["tmux_session"]):
            raise RuntimeError("tmux session not found")
        tmux_send_key(sess["tmux_session"], key, repeat=repeat)
        self.db.update_session(session_id, status="running")
        return self.db.get_session(session_id) or sess

    def approve(self, session_id: str, approve: bool) -> Dict[str, Any]:
        sess = self.db.get_session(session_id)
        if not sess:
            raise ValueError("session not found")
        if session_exists(sess["tmux_session"]):
            send_input(sess["tmux_session"], "y" if approve else "n", enter=True)
        self.db.update_session(session_id, status="running")
        return self.db.get_session(session_id) or sess

    def session_output(self, session_id: str, lines: int = 800) -> list[str]:
        sess = self.db.get_session(session_id)
        if not sess:
            raise ValueError("session not found")
        if not session_exists(sess["tmux_session"]):
            self.db.update_session(session_id, status="failed")
            return ["[codex-mobile] tmux session not found"]
        out = capture_lines(sess["tmux_session"], lines=lines)
        status = detect_status(out)
        self.db.update_session(session_id, status=status)
        return out

    def run_repo_cmd(self, repo_id: str, cmd: str, timeout_sec: int = 120) -> Dict[str, Any]:
        repo = self.db.get_repo(repo_id)
        if not repo:
            raise ValueError("repo not found")
        cp = subprocess.run(
            ["bash", "-lc", cmd],
            cwd=repo["path"],
            text=True,
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
        return {
            "ok": cp.returncode == 0,
            "code": cp.returncode,
            "stdout": cp.stdout,
            "stderr": cp.stderr,
        }

    def rename_session(self, session_id: str, name: str) -> Dict[str, Any]:
        row = self.db.get_session(session_id)
        if not row:
            raise ValueError("session not found")
        updated = self.db.rename_session(session_id, name)
        if not updated:
            raise ValueError("session not found")
        return updated
