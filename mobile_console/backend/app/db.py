from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateDB:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS repos (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    tmux_session TEXT NOT NULL,
                    name TEXT,
                    status TEXT NOT NULL,
                    last_prompt TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(repo_id) REFERENCES repos(id)
                );
                """
            )
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
            if "name" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN name TEXT")

    def upsert_repo(self, repo_id: str, name: str, path: str) -> None:
        now = utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO repos(id, name, path, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                  name = excluded.name,
                  updated_at = excluded.updated_at
                """,
                (repo_id, name, path, now, now),
            )

    def get_repo(self, repo_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,)).fetchone()
        return dict(row) if row else None

    def list_repos(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM repos ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]

    def create_session(
        self,
        repo_id: str,
        tmux_session: str,
        status: str,
        name: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = utc_now()
        session_id = str(uuid.uuid4())
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions(id, repo_id, tmux_session, name, status, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, repo_id, tmux_session, name, status, now, now),
            )
        return self.get_session(session_id) or {}

    def update_session(self, session_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = utc_now()
        keys = list(fields.keys())
        sets = ", ".join([f"{k} = ?" for k in keys])
        values = [fields[k] for k in keys] + [session_id]
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE sessions SET {sets} WHERE id = ?", values)

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None

    def latest_session_for_repo(self, repo_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE repo_id = ? ORDER BY updated_at DESC LIMIT 1",
                (repo_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_sessions_for_repo(self, repo_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE repo_id = ? ORDER BY updated_at DESC",
                (repo_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def rename_session(self, session_id: str, name: str) -> Optional[Dict[str, Any]]:
        cleaned = (name or "").strip()
        if not cleaned:
            raise ValueError("session name cannot be empty")
        self.update_session(session_id, name=cleaned)
        return self.get_session(session_id)
