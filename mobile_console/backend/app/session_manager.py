from __future__ import annotations

import hashlib
import json
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .codex_session_parser import parse_session_meta, read_timeline
from .db import StateDB, utc_now
from .event_parser import detect_status, prompt_pending_submission
from .github_service import GitHubError, create_repo
from .models import ProjectInitRequest
from .settings import SETTINGS
from .tmux_adapter import (
    capture_lines,
    create_session,
    kill_session,
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
    RECORDED_SESSION_PREFIX = "recorded:"
    DEFAULT_EXECUTION_MODE = "full-auto"
    ALLOWED_EXECUTION_MODES = {"inspect", "workspace", "full-auto"}

    def __init__(self, db: StateDB) -> None:
        self.db = db
        self._recorded_sync_at: dict[str, float] = {}
        self._recorded_sync_ttl = 2.5
        self._session_files_cache_ttl = 2.0
        self._session_files_cache: tuple[float, list[Path]] = (0.0, [])
        self._session_meta_cache: dict[str, tuple[int, int, dict[str, Any] | None]] = {}
        self._history_activity_cache: tuple[str, int, int, dict[str, dict[str, Any]]] | None = None

    def repo_id_for_path(self, path: str) -> str:
        canonical = self.canonical_repo_path(path)
        rid = normalize_repo_id(str(canonical))
        stable = hashlib.sha1(canonical.as_posix().encode("utf-8")).hexdigest()[:8]
        return f"{rid}_{stable}"

    def _git_root(self, path: Path) -> Path | None:
        cp = self._run_git(["rev-parse", "--show-toplevel"], cwd=path, check=False)
        if cp.returncode != 0:
            return None
        value = str(cp.stdout or "").strip()
        if not value:
            return None
        root = Path(value).expanduser().resolve()
        return root if root.exists() and root.is_dir() else None

    def canonical_repo_path(self, path: str | Path) -> Path:
        resolved = Path(path).expanduser().resolve()
        git_root = self._git_root(resolved)
        return git_root or resolved

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

    def is_manageable_repo_path(self, path: Path) -> bool:
        resolved = path.expanduser().resolve()
        return self.is_path_in_workspace(resolved) or self._git_root(resolved) is not None

    def _looks_like_project_dir(self, path: Path) -> bool:
        if (path / ".git").exists():
            return True
        markers = [
            "README.md",
            "README",
            "package.json",
            "pyproject.toml",
            "Cargo.toml",
            "go.mod",
            "Makefile",
            ".codex-project",
        ]
        return any((path / marker).exists() for marker in markers)

    def ensure_repo(self, path: str, name: Optional[str] = None) -> Dict[str, Any]:
        repo_path = self.canonical_repo_path(path)
        if not repo_path.exists() or not repo_path.is_dir():
            raise ValueError(f"Repo path does not exist: {repo_path}")
        if not self.is_manageable_repo_path(repo_path):
            raise ValueError(f"Repo path is not manageable by codex-mobile: {repo_path}")
        if not (repo_path / ".git").exists():
            preferred = self._find_git_repo_by_name(repo_path.name)
            if preferred:
                repo_path = self.canonical_repo_path(str(preferred["path"]))
        rid = self.repo_id_for_path(str(repo_path))
        self.db.upsert_repo(rid, name or repo_path.name, str(repo_path))
        repo = self.db.get_repo(rid)
        if not repo:
            repo = self.db.get_repo_by_path(str(repo_path))
        if not repo:
            raise RuntimeError("failed to save repo")
        self._merge_alias_repos(repo)
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
            if not self._looks_like_project_dir(child):
                continue
            repo = self.ensure_repo(str(child), name=child.name)
            discovered.append(repo)
        return discovered

    def discover_recent_session_repos(self, limit: int = 160) -> list[Dict[str, Any]]:
        discovered: list[Dict[str, Any]] = []
        seen: set[str] = set()
        for path in self._iter_codex_session_files(limit=limit):
            meta = self._extract_codex_session_meta(path)
            if not meta:
                continue
            cwd = Path(str(meta.get("cwd") or "")).expanduser()
            if not cwd.exists() or not cwd.is_dir():
                continue
            try:
                repo_path = self.canonical_repo_path(cwd)
            except Exception:
                continue
            if not self.is_manageable_repo_path(repo_path):
                continue
            if not self._looks_like_project_dir(repo_path):
                continue
            if not (repo_path / ".git").exists():
                preferred = self._find_git_repo_by_name(repo_path.name)
                if preferred:
                    self._merge_alias_repos(preferred)
                    continue
            key = repo_path.as_posix()
            if key in seen:
                continue
            seen.add(key)
            repo = self.ensure_repo(str(repo_path), name=repo_path.name)
            self._merge_alias_repos(repo)
            discovered.append(repo)
        return discovered

    def _find_git_repo_by_name(self, repo_name: str) -> Dict[str, Any] | None:
        target = str(repo_name or "").strip().lower()
        if not target:
            return None
        candidates: list[Dict[str, Any]] = []
        for row in self.db.list_repos():
            path = Path(str(row.get("path") or "")).expanduser().resolve()
            name = str(row.get("name") or path.name).strip().lower()
            if name != target:
                continue
            if (path / ".git").exists():
                candidates.append(row)
        if not candidates:
            return None
        candidates.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        return candidates[0]

    def _merge_alias_repos(self, target_repo: Dict[str, Any]) -> None:
        target_path = Path(str(target_repo.get("path") or "")).resolve()
        target_name = str(target_repo.get("name") or target_path.name).strip().lower()
        if not target_name:
            return
        if not (target_path / ".git").exists():
            return

        for row in self.db.list_repos():
            source_id = str(row.get("id") or "").strip()
            if not source_id or source_id == target_repo.get("id"):
                continue
            source_path = Path(str(row.get("path") or "")).expanduser().resolve()
            source_name = str(row.get("name") or source_path.name).strip().lower()
            if source_name != target_name:
                continue
            if source_path == target_path:
                self.db.merge_repo_into(source_id, str(target_repo.get("id") or ""))
                continue
            if (source_path / ".git").exists():
                continue
            self.db.merge_repo_into(source_id, str(target_repo.get("id") or ""))

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

    def _repo_accepts_cwd(self, repo_path: Path, cwd: Path) -> bool:
        if self._path_under(cwd, repo_path):
            return True
        repo_name = str(repo_path.name or "").strip().lower()
        cwd_name = str(cwd.name or "").strip().lower()
        if not repo_name or repo_name != cwd_name:
            return False
        # Fallback for misconfigured workspace roots: if the registered repo path is not a
        # real repo root, still accept the same project name so mobile can bind to the real
        # Codex session history instead of showing an empty or wrong conversation.
        return not (repo_path / ".git").exists()

    def _normalize_execution_mode(self, mode: str | None) -> str:
        value = str(mode or self.DEFAULT_EXECUTION_MODE).strip().lower()
        aliases = {
            "readonly": "inspect",
            "read-only": "inspect",
            "safe": "inspect",
            "workspace-write": "workspace",
            "write": "workspace",
            "danger": "full-auto",
            "full": "full-auto",
        }
        value = aliases.get(value, value)
        if value not in self.ALLOWED_EXECUTION_MODES:
            raise ValueError(f"unsupported execution mode: {mode}")
        return value

    def _wrapper_extra_args(self, execution_mode: str, extra_args: list[str] | None = None) -> list[str]:
        args = ["--mobile-mode", self._normalize_execution_mode(execution_mode)]
        args.extend(extra_args or [])
        return args

    def _session_rank(self, row: Dict[str, Any] | None) -> tuple[Any, ...]:
        data = dict(row or {})
        status_rank = {
            "running": 5,
            "waiting_input": 4,
            "waiting_approval": 3,
            "completed": 2,
            "failed": 1,
            "idle": 0,
        }.get(str(data.get("status") or "").strip().lower(), -1)
        tmux_name = str(data.get("tmux_session") or "")
        return (
            status_rank,
            1 if data.get("codex_session_id") else 0,
            1 if data.get("last_prompt") else 0,
            str(data.get("last_activity_at") or data.get("updated_at") or ""),
            0 if tmux_name.startswith(self.VSCODE_SESSION_PREFIX) else 1,
            str(data.get("created_at") or ""),
            str(data.get("id") or ""),
        )

    def _latest_session_event_kind(self, session_id: str) -> str:
        for event in reversed(self.db.list_session_events(session_id, limit=12)):
            kind = str(event.get("kind") or "").strip()
            if kind:
                return kind
        return ""

    def _recorded_tmux_key(self, meta: Dict[str, Any]) -> str:
        session_id = str(meta.get("id") or "").strip()
        if not session_id:
            raise ValueError("missing codex session id")
        if self._is_vscode_meta(meta):
            return f"{self.VSCODE_SESSION_PREFIX}{session_id}"
        return f"{self.RECORDED_SESSION_PREFIX}{session_id}"

    def _recorded_session_name(self, meta: Dict[str, Any]) -> str:
        session_id = str(meta.get("id") or "")[:8]
        source = str(meta.get("source") or "").strip().lower()
        if self._is_vscode_meta(meta):
            return f"VSCode {session_id}"
        if source == "exec":
            return f"执行会话 {session_id}"
        return f"终端会话 {session_id}"

    def _recorded_execution_mode(self, meta: Dict[str, Any]) -> str | None:
        if self._is_vscode_meta(meta):
            return "external"
        return None

    def _normalize_prompt_text(self, text: Any) -> str | None:
        raw = str(text or "").strip()
        if not raw:
            return None
        marker = "## My request for Codex:"
        if marker in raw:
            trimmed = raw.split(marker, 1)[1].strip()
            return trimmed or raw
        return raw

    def _is_live_status(self, status: Any) -> bool:
        value = str(status or "").strip().lower()
        return value in {"running", "waiting_input", "waiting_approval"}

    def _parse_ts(self, value: Any) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _seconds_between(self, left: datetime | None, right: datetime | None) -> float:
        if not left or not right:
            return 10_000_000.0
        return abs((left - right).total_seconds())

    def _latest_timestamp(self, *values: Any) -> str | None:
        candidates: list[datetime] = []
        for value in values:
            parsed = self._parse_ts(value)
            if parsed:
                candidates.append(parsed)
        if not candidates:
            return None
        return max(candidates).isoformat()

    def _activity_timestamp_for_meta(self, meta: Dict[str, Any], path: Path) -> str:
        ts = self._parse_ts(meta.get("last_timestamp")) or self._parse_ts(meta.get("timestamp"))
        if ts:
            return ts.isoformat()
        return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat()

    def _recorded_session_status(self, meta: Dict[str, Any]) -> str:
        last_kind = str(meta.get("last_kind") or "").strip()
        if last_kind == "task_aborted":
            return "failed"
        if last_kind in {"final_answer", "task_complete"}:
            return "completed"
        last_ts = self._parse_ts(meta.get("last_timestamp") or meta.get("timestamp"))
        if not last_ts:
            return "completed"
        now = datetime.now(last_ts.tzinfo) if last_ts.tzinfo else datetime.now()
        age_seconds = abs((now - last_ts).total_seconds())
        if age_seconds <= 900:
            return "running"
        if age_seconds <= 43200:
            return "waiting_input"
        return "completed"

    def _session_activity_dt(self, sess: Dict[str, Any] | None) -> datetime | None:
        data = dict(sess or {})
        return self._parse_ts(data.get("last_activity_at") or data.get("updated_at") or data.get("created_at"))

    def _session_activity_value(self, sess: Dict[str, Any] | None) -> str | None:
        ts = self._session_activity_dt(sess)
        return ts.isoformat() if ts else None

    def _session_origin_kind(self, sess: Dict[str, Any] | None) -> str:
        data = dict(sess or {})
        tmux_name = str(data.get("tmux_session") or "").strip()
        source = str(data.get("codex_source") or "").strip().lower()
        if tmux_name.endswith("_shared"):
            return "shared"
        if tmux_name.startswith(self.VSCODE_SESSION_PREFIX) or source == "vscode":
            return "vscode"
        if tmux_name.startswith(self.RECORDED_SESSION_PREFIX):
            return "recorded"
        if source == "cli":
            return "cli"
        if source == "exec":
            return "exec"
        return "mobile"

    def _is_shared_session(self, sess: Dict[str, Any] | None) -> bool:
        return self._session_origin_kind(sess) == "shared"

    def _is_external_session(self, sess: Dict[str, Any] | None) -> bool:
        return self._session_origin_kind(sess) in {"vscode", "recorded", "cli", "exec"}

    def _is_recent_external_session(self, sess: Dict[str, Any] | None, recent_hours: float = 18.0) -> bool:
        if not self._is_external_session(sess):
            return False
        if self._is_live_status((sess or {}).get("status")):
            return True
        activity = self._session_activity_dt(sess)
        if not activity:
            return False
        now = datetime.now(activity.tzinfo) if activity.tzinfo else datetime.now()
        return abs((now - activity).total_seconds()) <= recent_hours * 3600

    def _is_archived_session(self, sess: Dict[str, Any] | None) -> bool:
        return self._is_external_session(sess) and not self._is_recent_external_session(sess)

    def _session_sort_key(self, sess: Dict[str, Any] | None) -> tuple[Any, ...]:
        data = dict(sess or {})
        activity = self._session_activity_dt(data)
        activity_value = activity.timestamp() if activity else 0.0
        return (
            1 if self._is_live_status(data.get("status")) else 0,
            1 if self._is_recent_external_session(data, recent_hours=2.0) else 0,
            1 if self._is_shared_session(data) else 0,
            activity_value,
            1 if data.get("codex_session_id") else 0,
            self._session_rank(data),
        )

    def _select_focus_candidate(self, rows: list[Dict[str, Any]]) -> Dict[str, Any] | None:
        if not rows:
            return None
        non_archived = [row for row in rows if not self._is_archived_session(row)]
        pool = non_archived or rows
        ordered = sorted(pool, key=self._session_sort_key, reverse=True)
        return ordered[0] if ordered else None

    def _focus_reason_is_pinned(self, reason: Any) -> bool:
        value = str(reason or "").strip().lower()
        return bool(value) and value != "activity"

    def _should_replace_focus(
        self,
        focus_row: Dict[str, Any] | None,
        candidate_row: Dict[str, Any] | None,
    ) -> bool:
        if not candidate_row:
            return False
        if not focus_row:
            return True
        if str(focus_row.get("id") or "") == str(candidate_row.get("id") or ""):
            return False

        focus_activity = self._session_activity_dt(focus_row)
        candidate_activity = self._session_activity_dt(candidate_row)
        delta = 0.0
        if focus_activity and candidate_activity:
            delta = (candidate_activity - focus_activity).total_seconds()

        if self._is_archived_session(focus_row) and not self._is_archived_session(candidate_row):
            return True
        if self._is_live_status(candidate_row.get("status")) and not self._is_live_status(focus_row.get("status")):
            return True
        if self._is_external_session(candidate_row) and delta > 20:
            return True
        if delta > 45:
            return True
        if (
            self._is_shared_session(focus_row) and
            self._is_recent_external_session(candidate_row, recent_hours=2.0) and
            delta > 10
        ):
            return True
        return False

    def _refresh_repo_focus(self, repo_id: str, rows: list[Dict[str, Any]]) -> Dict[str, Any] | None:
        repo_key = str(repo_id or "").strip()
        if not repo_key:
            return None
        if not rows:
            self.db.clear_repo_focus(repo_key)
            return None

        focus = self.db.get_repo_focus(repo_key) or {}
        focus_id = str(focus.get("session_id") or "").strip()
        focus_row = next((row for row in rows if str(row.get("id") or "") == focus_id), None)
        candidate = self._select_focus_candidate(rows)
        if not candidate:
            return focus_row

        if focus_row and self._focus_reason_is_pinned(focus.get("reason")):
            self.db.set_repo_focus(
                repo_key,
                str(focus_row.get("id") or ""),
                reason=str(focus.get("reason") or "manual"),
                activity_at=self._session_activity_value(focus_row),
            )
            return focus_row

        if self._should_replace_focus(focus_row, candidate):
            self.db.set_repo_focus(
                repo_key,
                str(candidate.get("id") or ""),
                reason="activity",
                activity_at=self._session_activity_value(candidate),
            )
            return candidate

        if focus_row:
            self.db.set_repo_focus(
                repo_key,
                str(focus_row.get("id") or ""),
                reason=str(focus.get("reason") or "manual"),
                activity_at=self._session_activity_value(focus_row),
            )
            return focus_row

        self.db.set_repo_focus(
            repo_key,
            str(candidate.get("id") or ""),
            reason="activity",
            activity_at=self._session_activity_value(candidate),
        )
        return candidate

    def mark_session_focus(self, session_id: str, reason: str | None = "manual") -> Dict[str, Any]:
        sess = self.db.get_session(session_id)
        if not sess:
            raise ValueError("session not found")
        self.db.set_repo_focus(
            str(sess.get("repo_id") or ""),
            str(sess.get("id") or ""),
            reason=str(reason or "manual"),
            activity_at=self._session_activity_value(sess),
        )
        return self.db.get_session(session_id) or sess

    def _build_sync_hint(
        self,
        focus_row: Dict[str, Any] | None,
        suggested_row: Dict[str, Any] | None,
        rows: list[Dict[str, Any]],
    ) -> str | None:
        if suggested_row and focus_row and str(suggested_row.get("id") or "") != str(focus_row.get("id") or ""):
            if self._session_origin_kind(suggested_row) == "vscode":
                return "Mac / VSCode 最近有新活动，手机端可以直接接着继续。"
            if self._is_external_session(suggested_row):
                return "发现外部会话新进展，建议切到最新会话继续。"
            if self._is_shared_session(suggested_row):
                return "项目共享会话是当前最活跃入口。"

        external_recent = [row for row in rows if self._is_recent_external_session(row, recent_hours=2.0)]
        if external_recent:
            newest = sorted(external_recent, key=self._session_sort_key, reverse=True)[0]
            if self._session_origin_kind(newest) == "vscode":
                return "已同步到 VSCode 最近会话，手机端会持续自动刷新。"
            return "已发现最近的外部会话活动，手机与桌面会按活动时间自动对齐。"

        live_rows = [row for row in rows if self._is_live_status(row.get("status"))]
        if live_rows:
            return "当前项目有活跃会话，手机端会优先跟随最近活动。"
        return "当前没有活跃会话；发消息时会自动恢复项目共享会话。"

    def _stale_live_binding(self, sess: Dict[str, Any], meta: Dict[str, Any] | None) -> bool:
        if not meta:
            return False
        tmux_name = str(sess.get("tmux_session") or "")
        if tmux_name.startswith(self.VSCODE_SESSION_PREFIX) or tmux_name.startswith(self.RECORDED_SESSION_PREFIX):
            return False
        if not self._is_live_status(sess.get("status")):
            return False
        session_ts = self._parse_ts(sess.get("last_activity_at") or sess.get("updated_at") or sess.get("created_at"))
        meta_ts = self._parse_ts(meta.get("last_timestamp") or meta.get("timestamp"))
        if not session_ts or not meta_ts:
            return False
        return (session_ts - meta_ts).total_seconds() > 600

    def _clear_stale_binding(self, session_id: str, sess: Dict[str, Any], meta: Dict[str, Any] | None) -> None:
        updates: dict[str, Any] = {
            "codex_session_id": None,
            "codex_session_file": None,
            "codex_source": None,
            "codex_model": None,
        }
        stale_prompt = str(meta.get("last_prompt") or "").strip() if meta else ""
        current_prompt = str(sess.get("last_prompt") or "").strip()
        if stale_prompt and current_prompt == stale_prompt:
            updates["last_prompt"] = None
        self.db.update_session(session_id, **updates)

    def _should_accept_last_prompt(
        self,
        sess: Dict[str, Any],
        prompt: Any,
        *,
        prompt_timestamp: Any = None,
    ) -> bool:
        candidate = self._normalize_prompt_text(prompt) or ""
        if not candidate:
            return False
        current = self._normalize_prompt_text(sess.get("last_prompt")) or ""
        if not current or current == candidate:
            return True
        current_ts = self._parse_ts(sess.get("last_activity_at") or sess.get("updated_at") or sess.get("created_at"))
        candidate_ts = self._parse_ts(prompt_timestamp)
        if not current_ts or not candidate_ts:
            return False
        return candidate_ts > current_ts

    def _update_binding(self, session_id: str, sess: Dict[str, Any], **fields: Any) -> None:
        prompt_timestamp = fields.pop("last_prompt_timestamp", None)
        incoming_activity_at = fields.get("last_activity_at")
        merged_activity_at = self._latest_timestamp(sess.get("last_activity_at"), incoming_activity_at)
        if incoming_activity_at and merged_activity_at:
            fields["last_activity_at"] = merged_activity_at
        if "last_prompt" in fields and not self._should_accept_last_prompt(
            sess,
            fields.get("last_prompt"),
            prompt_timestamp=prompt_timestamp or incoming_activity_at,
        ):
            fields.pop("last_prompt", None)
        next_codex_id = fields.get("codex_session_id", sess.get("codex_session_id"))
        current_codex_id = sess.get("codex_session_id")
        if next_codex_id and current_codex_id and str(next_codex_id).strip() != str(current_codex_id).strip():
            self.db.clear_session_events(session_id)
        self.db.update_session(session_id, **fields)

    def _history_activity_for_sessions(self) -> dict[str, dict[str, Any]]:
        path = Path.home() / ".codex" / "history.jsonl"
        if not path.exists():
            return {}
        try:
            stat = path.stat()
            cache_key = (str(path), int(stat.st_mtime_ns), int(stat.st_size))
            if self._history_activity_cache and self._history_activity_cache[:3] == cache_key:
                return dict(self._history_activity_cache[3])
        except Exception:
            cache_key = None

        latest: dict[str, dict[str, Any]] = {}
        try:
            with path.open("r", encoding="utf-8") as handle:
                for raw in handle:
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    session_id = str(row.get("session_id") or "").strip()
                    if not session_id:
                        continue
                    try:
                        ts_value = float(row.get("ts") or 0)
                    except (TypeError, ValueError):
                        ts_value = 0.0
                    if ts_value <= 0:
                        continue
                    text = self._normalize_prompt_text(row.get("text")) or ""
                    timestamp = datetime.fromtimestamp(ts_value).astimezone().isoformat()
                    previous = latest.get(session_id)
                    if previous and str(previous.get("timestamp") or "") >= timestamp:
                        continue
                    latest[session_id] = {
                        "timestamp": timestamp,
                        "text": text,
                    }
        except Exception:
            return {}
        if cache_key:
            self._history_activity_cache = (cache_key[0], cache_key[1], cache_key[2], dict(latest))
        return latest

    def _iter_codex_session_files(self, limit: int = 500) -> list[Path]:
        root = Path.home() / ".codex" / "sessions"
        if not root.exists():
            return []
        now = time.monotonic()
        cached_at, cached_files = self._session_files_cache
        if cached_files and now - cached_at < self._session_files_cache_ttl and len(cached_files) >= limit:
            return cached_files[:limit]
        files = sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        self._session_files_cache = (now, files)
        return files[:limit]

    def _extract_codex_session_meta(self, path: Path) -> dict[str, Any] | None:
        cache_key = str(path)
        try:
            stat = path.stat()
            stamp = (int(stat.st_mtime_ns), int(stat.st_size))
            cached = self._session_meta_cache.get(cache_key)
            if cached and cached[:2] == stamp:
                meta = cached[2]
                return dict(meta) if isinstance(meta, dict) else None

            meta = parse_session_meta(path)
            if not meta:
                self._session_meta_cache[cache_key] = (stamp[0], stamp[1], None)
                return None
            with path.open("r", encoding="utf-8") as f:
                _ = f.readline()

                last_prompt = None
                last_timestamp = meta.get("timestamp")
                last_kind = None
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        item = json.loads(line)
                    except Exception:
                        continue
                    row_ts = item.get("timestamp")
                    if row_ts:
                        last_timestamp = row_ts
                    row_type = item.get("type")
                    if row_type == "event_msg":
                        p = item.get("payload", {})
                        payload_type = str(p.get("type") or "").strip()
                        if payload_type == "user_message":
                            msg = self._normalize_prompt_text(p.get("message")) or ""
                            if msg:
                                last_prompt = msg
                                last_kind = "user_message"
                        elif payload_type == "task_started":
                            last_kind = "task_started"
                        elif payload_type == "agent_reasoning":
                            last_kind = "reasoning"
                        elif payload_type == "agent_message":
                            phase = str(p.get("phase") or "").strip()
                            last_kind = "final_answer" if phase == "final_answer" else "commentary"
                        elif payload_type == "task_complete":
                            last_kind = "task_complete"
                        elif payload_type == "turn_aborted":
                            last_kind = "task_aborted"
                        continue
                    if row_type != "response_item":
                        continue
                    p = item.get("payload", {})
                    payload_type = str(p.get("type") or "").strip()
                    if payload_type == "reasoning":
                        last_kind = "reasoning"
                    elif payload_type == "function_call":
                        last_kind = "tool_call"
                    elif payload_type == "function_call_output":
                        last_kind = "tool_output"
                    elif payload_type == "message":
                        phase = str(p.get("phase") or "").strip()
                        last_kind = "final_answer" if phase == "final_answer" else "commentary"
                meta["last_prompt"] = last_prompt
                meta["last_timestamp"] = last_timestamp
                meta["last_kind"] = last_kind
                self._session_meta_cache[cache_key] = (stamp[0], stamp[1], dict(meta))
                return meta
        except Exception:
            return None

    def _find_codex_session_file(self, session_ref: str) -> Path | None:
        ref = str(session_ref or "").strip()
        if not ref:
            return None
        for path in self._iter_codex_session_files(limit=800):
            if ref in path.name:
                return path
        return None

    def _is_vscode_meta(self, meta: Dict[str, Any] | None) -> bool:
        if not meta:
            return False
        originator = str(meta.get("originator") or "").strip().lower()
        source = str(meta.get("source") or "").strip().lower()
        return "vscode" in originator or source == "vscode"

    def _session_accepts_meta(self, sess: Dict[str, Any], meta: Dict[str, Any] | None) -> bool:
        tmux_name = str(sess.get("tmux_session") or "")
        if tmux_name.startswith(self.VSCODE_SESSION_PREFIX):
            return True
        return not self._is_vscode_meta(meta)

    def _codex_candidate_score(
        self,
        sess: Dict[str, Any],
        repo_path: Path,
        meta: Dict[str, Any],
    ) -> tuple[int, float, float]:
        cwd = Path(str(meta.get("cwd") or "")).expanduser()
        score = 0
        if cwd.resolve() == repo_path.resolve():
            score += 8
        elif self._path_under(cwd, repo_path):
            score += 3

        source = str(meta.get("source") or "").strip().lower()
        if source == "cli":
            score += 5
        elif source == "exec":
            score += 1
        elif source == "vscode":
            score += 2

        if sess.get("codex_session_id") and meta.get("id") == sess.get("codex_session_id"):
            score += 12
        if sess.get("last_prompt") and meta.get("last_prompt") == sess.get("last_prompt"):
            score += 8

        session_ts = self._parse_ts(sess.get("last_activity_at") or sess.get("updated_at") or sess.get("created_at"))
        meta_ts = self._parse_ts(meta.get("last_timestamp") or meta.get("timestamp"))
        delta = self._seconds_between(session_ts, meta_ts)
        if delta <= 30:
            score += 5
        elif delta <= 180:
            score += 2

        freshness = meta_ts.timestamp() if meta_ts else 0.0
        return score, -delta, freshness

    def resolve_codex_session(self, session_id: str) -> dict[str, Any] | None:
        sess = self.db.get_session(session_id)
        if not sess:
            return None

        tmux_name = str(sess.get("tmux_session") or "")
        if tmux_name.startswith(self.VSCODE_SESSION_PREFIX):
            external_id = tmux_name.split(":", 1)[1].strip()
            path = self._find_codex_session_file(external_id)
            meta = self._extract_codex_session_meta(path) if path else None
            if meta and path:
                self._update_binding(
                    session_id,
                    sess,
                    codex_session_id=meta.get("id"),
                    codex_session_file=str(path),
                    codex_source=meta.get("source"),
                    codex_model=meta.get("model"),
                    last_activity_at=self._latest_timestamp(sess.get("last_activity_at"), meta.get("timestamp")),
                    last_prompt_timestamp=meta.get("last_timestamp") or meta.get("timestamp"),
                    **({"last_prompt": meta.get("last_prompt")} if meta.get("last_prompt") else {}),
                )
                meta["path"] = str(path)
            return meta

        known_path_raw = str(sess.get("codex_session_file") or "").strip()
        known_path = Path(known_path_raw).expanduser() if known_path_raw else None
        if known_path and known_path.exists():
            meta = self._extract_codex_session_meta(known_path)
            if meta and self._session_accepts_meta(sess, meta) and not self._stale_live_binding(sess, meta):
                if meta.get("last_prompt"):
                    self._update_binding(
                        session_id,
                        sess,
                        last_prompt=meta.get("last_prompt"),
                        last_prompt_timestamp=meta.get("last_timestamp") or meta.get("timestamp"),
                    )
                meta["path"] = str(known_path)
                return meta
            self._clear_stale_binding(session_id, sess, meta)
            sess = self.db.get_session(session_id) or sess

        known_thread_id = str(sess.get("codex_session_id") or "").strip()
        if known_thread_id:
            thread_path = self._find_codex_session_file(known_thread_id)
            thread_meta = self._extract_codex_session_meta(thread_path) if thread_path else None
            if thread_meta and thread_path and self._session_accepts_meta(sess, thread_meta) and not self._stale_live_binding(sess, thread_meta):
                self._update_binding(
                    session_id,
                    sess,
                    codex_session_file=str(thread_path),
                    codex_source=thread_meta.get("source"),
                    codex_model=thread_meta.get("model"),
                    last_activity_at=self._latest_timestamp(sess.get("last_activity_at"), thread_meta.get("timestamp")),
                    last_prompt_timestamp=thread_meta.get("last_timestamp") or thread_meta.get("timestamp"),
                    **({"last_prompt": thread_meta.get("last_prompt")} if thread_meta.get("last_prompt") else {}),
                )
                thread_meta["path"] = str(thread_path)
                return thread_meta
            self._clear_stale_binding(session_id, sess, thread_meta)
            sess = self.db.get_session(session_id) or sess

        repo = self.db.get_repo(sess["repo_id"])
        if not repo:
            return None

        repo_path = Path(repo["path"]).resolve()
        candidates: list[tuple[tuple[int, float, float], Path, Dict[str, Any]]] = []
        for path in self._iter_codex_session_files(limit=800):
            meta = self._extract_codex_session_meta(path)
            if not meta:
                continue
            if not self._session_accepts_meta(sess, meta):
                continue
            cwd = Path(str(meta.get("cwd") or "")).expanduser()
            if not self._repo_accepts_cwd(repo_path, cwd):
                continue
            candidates.append((self._codex_candidate_score(sess, repo_path, meta), path, meta))

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)
        _score, best_path, best_meta = candidates[0]
        self._update_binding(
            session_id,
            sess,
            codex_session_id=best_meta.get("id"),
            codex_session_file=str(best_path),
            codex_source=best_meta.get("source"),
            codex_model=best_meta.get("model"),
            last_activity_at=self._latest_timestamp(sess.get("last_activity_at"), best_meta.get("timestamp")),
            last_prompt_timestamp=best_meta.get("last_timestamp") or best_meta.get("timestamp"),
            **({"last_prompt": best_meta.get("last_prompt")} if best_meta.get("last_prompt") else {}),
        )
        best_meta["path"] = str(best_path)
        return best_meta

    def session_timeline(self, session_id: str, limit: int = 120) -> dict[str, Any]:
        meta = self.resolve_codex_session(session_id)
        if not meta:
            return {
                "events": [],
                "codex_session_id": None,
                "codex_source": None,
                "codex_model": None,
            }

        path_value = meta.get("path")
        path = Path(str(path_value)).expanduser() if path_value else None
        events = read_timeline(path, limit=limit) if path else []
        return {
            "events": events,
            "codex_session_id": meta.get("id"),
            "codex_source": meta.get("source"),
            "codex_model": meta.get("model"),
        }

    def sync_recorded_sessions(self, repo_id: str, *, force: bool = False) -> list[Dict[str, Any]]:
        last_sync = self._recorded_sync_at.get(repo_id, 0.0)
        now = time.monotonic()
        if not force and now - last_sync < self._recorded_sync_ttl:
            return self.db.list_sessions_for_repo(repo_id)

        repo = self.db.get_repo(repo_id)
        if not repo:
            return []

        repo_path = Path(repo["path"]).resolve()
        rows = self.db.list_sessions_for_repo(repo_id)
        by_tmux = {r.get("tmux_session"): r for r in rows if r.get("tmux_session")}
        by_codex: dict[str, Dict[str, Any]] = {}
        history_activity = self._history_activity_for_sessions()
        for row in rows:
            codex_id = str(row.get("codex_session_id") or "").strip()
            if not codex_id:
                continue
            existing = by_codex.get(codex_id)
            if not existing or self._session_rank(row) > self._session_rank(existing):
                by_codex[codex_id] = row

        for f in self._iter_codex_session_files():
            meta = self._extract_codex_session_meta(f)
            if not meta:
                continue
            cwd = Path(meta["cwd"]).expanduser()
            if not self._repo_accepts_cwd(repo_path, cwd):
                continue

            tmux_key = self._recorded_tmux_key(meta)
            default_name = self._recorded_session_name(meta)
            execution_mode = self._recorded_execution_mode(meta)
            activity_at = self._activity_timestamp_for_meta(meta, f)
            recorded_status = self._recorded_session_status(meta)
            history_meta = history_activity.get(str(meta.get("id") or "").strip()) or {}
            activity_at = self._latest_timestamp(activity_at, history_meta.get("timestamp")) or activity_at
            if not meta.get("last_prompt") and history_meta.get("text"):
                meta["last_prompt"] = history_meta["text"]
            existing = by_tmux.get(tmux_key)
            linked = by_codex.get(str(meta.get("id") or "").strip())
            if existing:
                updates: dict[str, Any] = {}
                if meta.get("last_prompt"):
                    updates["last_prompt"] = meta["last_prompt"]
                if str(existing.get("status") or "").strip() != recorded_status:
                    updates["status"] = recorded_status
                if not existing.get("name"):
                    updates["name"] = default_name
                updates["last_activity_at"] = activity_at
                updates["codex_session_id"] = meta.get("id")
                updates["codex_session_file"] = str(f)
                updates["codex_source"] = meta.get("source")
                updates["codex_model"] = meta.get("model")
                if execution_mode:
                    updates["execution_mode"] = execution_mode
                if updates:
                    if meta.get("last_prompt"):
                        updates["last_prompt_timestamp"] = meta.get("last_timestamp") or activity_at
                    self._update_binding(existing["id"], existing, **updates)
                continue

            if linked:
                updates = {
                    "last_activity_at": activity_at,
                    "last_prompt": meta.get("last_prompt"),
                    "codex_session_id": meta.get("id"),
                    "codex_session_file": str(f),
                    "codex_source": meta.get("source"),
                    "codex_model": meta.get("model"),
                }
                linked_tmux = str(linked.get("tmux_session") or "")
                if linked_tmux.startswith(self.VSCODE_SESSION_PREFIX) or linked_tmux.startswith(self.RECORDED_SESSION_PREFIX):
                    updates["tmux_session"] = tmux_key
                    updates["status"] = recorded_status
                    if execution_mode:
                        updates["execution_mode"] = execution_mode
                    if not linked.get("name"):
                        updates["name"] = default_name
                if meta.get("last_prompt"):
                    updates["last_prompt_timestamp"] = meta.get("last_timestamp") or activity_at
                self._update_binding(
                    linked["id"],
                    linked,
                    **{k: v for k, v in updates.items() if v not in (None, "")},
                )
                continue

            created = self.db.create_session(
                repo_id,
                tmux_key,
                recorded_status,
                name=default_name,
                execution_mode=execution_mode,
            )
            self._update_binding(
                created["id"],
                created,
                last_activity_at=activity_at,
                last_prompt_timestamp=meta.get("last_timestamp") or activity_at,
                last_prompt=meta.get("last_prompt"),
                codex_session_id=meta.get("id"),
                codex_session_file=str(f),
                codex_source=meta.get("source"),
                codex_model=meta.get("model"),
                **({"execution_mode": execution_mode} if execution_mode else {}),
            )

        for codex_id in list(by_codex.keys()):
            row = self.db.find_session_by_codex_session_id(repo_id, codex_id) or by_codex[codex_id]
            history_meta = history_activity.get(codex_id)
            if not history_meta:
                continue
            updates: dict[str, Any] = {
                "last_activity_at": self._latest_timestamp(row.get("last_activity_at"), history_meta.get("timestamp")),
            }
            if history_meta.get("text") and not row.get("last_prompt"):
                updates["last_prompt"] = history_meta["text"]
            self.db.update_session(
                row["id"],
                **{key: value for key, value in updates.items() if value not in (None, "")},
            )

        self._recorded_sync_at[repo_id] = now
        return self.db.list_sessions_for_repo(repo_id)

    def sync_repo_sessions(self, repo_id: str, *, force: bool = False) -> list[Dict[str, Any]]:
        repo = self.db.get_repo(repo_id)
        if not repo:
            return []

        self.db.dedupe_sessions(repo_id)
        repo_path = Path(repo["path"]).resolve()
        if (repo_path / ".git").exists():
            self._merge_alias_repos(repo)
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
            default_name = "共享会话" if tmux_name == prefix or tmux_name.endswith("_shared") else tmux_name
            existing = by_tmux.get(tmux_name)
            if existing:
                updates: dict[str, Any] = {}
                if existing.get("status") != "running":
                    updates["status"] = "running"
                    updates["last_activity_at"] = self._latest_timestamp(existing.get("last_activity_at"), utc_now())
                if not str(existing.get("name") or "").strip():
                    updates["name"] = default_name
                if updates:
                    self.db.update_session(existing["id"], **updates)
            else:
                self.db.create_session(repo_id, tmux_name, "running", name=default_name)

        # Keep history, but mark unreachable sessions as failed.
        for row in rows:
            tmux_name = row.get("tmux_session")
            if not tmux_name:
                continue
            if tmux_name not in seen_tmux and not session_exists(tmux_name):
                if row.get("status") not in {"failed", "completed"}:
                    self.db.update_session(row["id"], status="failed")

        self.sync_recorded_sessions(repo_id, force=force)
        refreshed = self.db.list_sessions_for_repo(repo_id)
        live_candidates = [
            row for row in refreshed
            if str(row.get("status") or "").strip().lower() in {"running", "waiting_input", "waiting_approval"}
        ][:8]
        for row in live_candidates:
            self.resolve_codex_session(str(row.get("id") or ""))
        final_rows = self.db.list_sessions_for_repo(repo_id)
        self._refresh_repo_focus(repo_id, final_rows)
        return final_rows

    def build_session_hub(self, repo_id: str, *, force: bool = False) -> Dict[str, Any]:
        rows = self.sync_repo_sessions(repo_id, force=force)
        ordered = sorted(rows, key=self._session_sort_key, reverse=True)
        focus_row = self._refresh_repo_focus(repo_id, ordered)
        focus = self.db.get_repo_focus(repo_id) or {}
        suggested_row = self._select_focus_candidate(ordered)
        shared_row = next((row for row in ordered if self._is_shared_session(row)), None)

        excluded_ids = {
            str(item.get("id") or "")
            for item in (focus_row, shared_row)
            if item and item.get("id")
        }
        live_ids = [
            str(row.get("id") or "")
            for row in ordered
            if row.get("id") and self._is_live_status(row.get("status"))
        ][:6]
        external_recent_ids = [
            str(row.get("id") or "")
            for row in ordered
            if row.get("id") and self._is_recent_external_session(row, recent_hours=2.0)
        ][:4]
        archived_ids = [
            str(row.get("id") or "")
            for row in ordered
            if row.get("id") and self._is_archived_session(row)
        ][:10]
        recent_ids = [
            str(row.get("id") or "")
            for row in ordered
            if row.get("id") and str(row.get("id") or "") not in excluded_ids and str(row.get("id") or "") not in archived_ids
        ][:8]

        return {
            "repo_id": repo_id,
            "generated_at": utc_now(),
            "focus_session_id": str(focus.get("session_id") or "") or None,
            "focus_reason": str(focus.get("reason") or "") or None,
            "focus_updated_at": str(focus.get("updated_at") or "") or None,
            "current_session_id": str(focus_row.get("id") or "") if focus_row else None,
            "suggested_session_id": str(suggested_row.get("id") or "") if suggested_row else None,
            "shared_session_id": str(shared_row.get("id") or "") if shared_row else None,
            "live_session_ids": live_ids,
            "recent_session_ids": recent_ids,
            "archived_session_ids": archived_ids,
            "external_recent_session_ids": external_recent_ids,
            "sync_hint": self._build_sync_hint(focus_row, suggested_row, ordered),
            "sessions": ordered,
        }

    def start_new(self, repo_id: str, execution_mode: str | None = None) -> Dict[str, Any]:
        repo = self.db.get_repo(repo_id)
        if not repo:
            raise ValueError("repo not found")
        mode = self._normalize_execution_mode(execution_mode)
        tmux_name = self._new_tmux_session_name(repo_id)
        create_session(
            tmux_name,
            repo["path"],
            str(SETTINGS.codex_wrapper),
            extra_args=self._wrapper_extra_args(mode),
        )
        row = self.db.create_session(repo_id, tmux_name, "running", name="临时会话", execution_mode=mode)
        self.mark_session_focus(str(row.get("id") or ""), reason="new")
        return self.db.get_session(str(row.get("id") or "")) or row

    def resume(self, repo_id: str, execution_mode: str | None = None) -> Dict[str, Any]:
        repo = self.db.get_repo(repo_id)
        if not repo:
            raise ValueError("repo not found")
        mode = self._normalize_execution_mode(execution_mode)

        self.sync_repo_sessions(repo_id)

        # Shared per-repo session keeps mobile/web/mac interactions consistent.
        shared_tmux = self._shared_tmux_session_name(repo_id)
        rows = self.db.list_sessions_for_repo(repo_id)
        shared_row = next((r for r in rows if r.get("tmux_session") == shared_tmux), None)

        if session_exists(shared_tmux):
            raw_existing_mode = str((shared_row or {}).get("execution_mode") or "").strip()
            existing_mode = self._normalize_execution_mode(raw_existing_mode or self.DEFAULT_EXECUTION_MODE)
            should_recreate = not raw_existing_mode or existing_mode != mode
            if should_recreate:
                kill_session(shared_tmux)
                create_session(
                    shared_tmux,
                    repo["path"],
                    str(SETTINGS.codex_wrapper),
                    extra_args=self._wrapper_extra_args(mode),
                )
                if shared_row:
                    self.db.update_session(shared_row["id"], status="running", execution_mode=mode, last_activity_at=utc_now())
                    focused = self.mark_session_focus(shared_row["id"], reason="shared_resume")
                    return self.db.get_session(shared_row["id"]) or focused
                created = self.db.create_session(repo_id, shared_tmux, "running", name="共享会话", execution_mode=mode)
                self.mark_session_focus(str(created.get("id") or ""), reason="shared_resume")
                return self.db.get_session(str(created.get("id") or "")) or created
            if shared_row:
                self.db.update_session(shared_row["id"], status="running", execution_mode=existing_mode, last_activity_at=utc_now())
                focused = self.mark_session_focus(shared_row["id"], reason="shared_resume")
                return self.db.get_session(shared_row["id"]) or focused
            created = self.db.create_session(repo_id, shared_tmux, "running", name="共享会话", execution_mode=mode)
            self.mark_session_focus(str(created.get("id") or ""), reason="shared_resume")
            return self.db.get_session(str(created.get("id") or "")) or created

        create_session(
            shared_tmux,
            repo["path"],
            str(SETTINGS.codex_wrapper),
            extra_args=self._wrapper_extra_args(mode),
        )
        if shared_row:
            self.db.update_session(shared_row["id"], status="running", execution_mode=mode, last_activity_at=utc_now())
            focused = self.mark_session_focus(shared_row["id"], reason="shared_resume")
            return self.db.get_session(shared_row["id"]) or focused
        created = self.db.create_session(repo_id, shared_tmux, "running", name="共享会话", execution_mode=mode)
        self.mark_session_focus(str(created.get("id") or ""), reason="shared_resume")
        return self.db.get_session(str(created.get("id") or "")) or created

    def resume_session_by_id(self, session_id: str) -> Dict[str, Any]:
        sess = self.db.get_session(session_id)
        if not sess:
            raise ValueError("session not found")

        tmux_name = str(sess.get("tmux_session") or "")
        if tmux_name.startswith(self.VSCODE_SESSION_PREFIX) or tmux_name.startswith(self.RECORDED_SESSION_PREFIX):
            external_session_id = str(sess.get("codex_session_id") or tmux_name.split(":", 1)[1].strip()).strip()
            repo = self.db.get_repo(sess["repo_id"])
            if not repo:
                raise ValueError("repo not found")
            resume_mode = str(sess.get("execution_mode") or self.DEFAULT_EXECUTION_MODE).strip()
            if resume_mode == "external":
                resume_mode = self.DEFAULT_EXECUTION_MODE
            new_tmux = self._new_tmux_session_name(sess["repo_id"])
            create_session(
                new_tmux,
                repo["path"],
                str(SETTINGS.codex_wrapper),
                extra_args=self._wrapper_extra_args(
                    resume_mode,
                    ["resume", external_session_id],
                ),
            )
            self.db.update_session(
                session_id,
                tmux_session=new_tmux,
                status="running",
                execution_mode=self._normalize_execution_mode(resume_mode),
                last_activity_at=utc_now(),
            )
            focused = self.mark_session_focus(session_id, reason="resume")
            return self.db.get_session(session_id) or focused

        if not session_exists(sess["tmux_session"]):
            resume_target = str(sess.get("codex_session_id") or "").strip()
            if resume_target:
                repo = self.db.get_repo(sess["repo_id"])
                if not repo:
                    raise ValueError("repo not found")
                resume_mode = str(sess.get("execution_mode") or self.DEFAULT_EXECUTION_MODE).strip()
                if resume_mode == "external":
                    resume_mode = self.DEFAULT_EXECUTION_MODE
                new_tmux = self._new_tmux_session_name(sess["repo_id"])
                create_session(
                    new_tmux,
                    repo["path"],
                    str(SETTINGS.codex_wrapper),
                    extra_args=self._wrapper_extra_args(
                        resume_mode,
                        ["resume", resume_target],
                    ),
                )
                self.db.update_session(
                    session_id,
                    tmux_session=new_tmux,
                    status="running",
                    execution_mode=self._normalize_execution_mode(resume_mode),
                    last_activity_at=utc_now(),
                )
                focused = self.mark_session_focus(session_id, reason="resume")
                return self.db.get_session(session_id) or focused
            self.db.update_session(session_id, status="failed")
            raise ValueError("selected session is no longer running in tmux")
        self.db.update_session(session_id, status="running", last_activity_at=utc_now())
        focused = self.mark_session_focus(session_id, reason="resume")
        return self.db.get_session(session_id) or focused

    def send_prompt(self, session_id: str, prompt: str) -> Dict[str, Any]:
        sess = self.db.get_session(session_id)
        if not sess:
            raise ValueError("session not found")
        if not session_exists(sess["tmux_session"]):
            raise RuntimeError("tmux session not found")
        tmux_name = sess["tmux_session"]
        send_input(tmux_name, prompt, enter=True)
        time.sleep(0.18)
        try:
            pane_lines = capture_lines(tmux_name, lines=120)
        except Exception:
            pane_lines = []
        if prompt_pending_submission(pane_lines, prompt):
            tmux_send_key(tmux_name, "Enter", repeat=1)
            time.sleep(0.12)
        self.db.update_session(session_id, status="running", last_prompt=prompt, last_activity_at=utc_now())
        focused = self.mark_session_focus(session_id, reason="prompt")
        return self.db.get_session(session_id) or focused

    def send_key(self, session_id: str, key: str, repeat: int = 1) -> Dict[str, Any]:
        sess = self.db.get_session(session_id)
        if not sess:
            raise ValueError("session not found")
        if not session_exists(sess["tmux_session"]):
            raise RuntimeError("tmux session not found")
        tmux_send_key(sess["tmux_session"], key, repeat=repeat)
        self.db.update_session(session_id, status="running", last_activity_at=utc_now())
        focused = self.mark_session_focus(session_id, reason="key")
        return self.db.get_session(session_id) or focused

    def approve(self, session_id: str, approve: bool) -> Dict[str, Any]:
        sess = self.db.get_session(session_id)
        if not sess:
            raise ValueError("session not found")
        if session_exists(sess["tmux_session"]):
            send_input(sess["tmux_session"], "y" if approve else "n", enter=True)
        self.db.update_session(session_id, status="running", last_activity_at=utc_now())
        focused = self.mark_session_focus(session_id, reason="approval")
        return self.db.get_session(session_id) or focused

    def session_output(self, session_id: str, lines: int = 800) -> list[str]:
        sess = self.db.get_session(session_id)
        if not sess:
            raise ValueError("session not found")
        if not session_exists(sess["tmux_session"]):
            tmux_name = str(sess.get("tmux_session") or "")
            archived = tmux_name.startswith(self.VSCODE_SESSION_PREFIX) or tmux_name.startswith(self.RECORDED_SESSION_PREFIX)
            terminal_status = str(sess.get("status") or "").strip().lower()
            if archived or terminal_status in {"completed", "failed", "idle"}:
                return []
            self.db.update_session(session_id, status="failed")
            return ["[codex-mobile] tmux session not found"]
        out = capture_lines(sess["tmux_session"], lines=lines)
        status = detect_status(out)
        current_status = str(sess.get("status") or "").strip().lower()
        latest_kind = self._latest_session_event_kind(session_id)
        if status in {"completed", "idle"} and current_status in {"running", "waiting_input", "waiting_approval"} and latest_kind == "user_message":
            status = current_status
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
