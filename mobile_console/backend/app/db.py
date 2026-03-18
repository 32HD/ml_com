from __future__ import annotations

import json
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

    @staticmethod
    def _status_rank(status: Any) -> int:
        ranks = {
            "running": 5,
            "waiting_input": 4,
            "waiting_approval": 3,
            "completed": 2,
            "failed": 1,
            "idle": 0,
        }
        return ranks.get(str(status or "").strip().lower(), -1)

    def _session_priority(self, row: sqlite3.Row | Dict[str, Any]) -> tuple[Any, ...]:
        data = dict(row)
        tmux_name = str(data.get("tmux_session") or "")
        execution_mode = str(data.get("execution_mode") or "")
        return (
            self._status_rank(data.get("status")),
            1 if data.get("codex_session_id") else 0,
            1 if data.get("last_prompt") else 0,
            1 if execution_mode and execution_mode != "external" else 0,
            str(data.get("last_activity_at") or data.get("updated_at") or ""),
            0 if tmux_name.startswith("vscode:") else 1,
            str(data.get("created_at") or ""),
            str(data.get("id") or ""),
        )

    def _dedupe_sessions_locked(self, conn: sqlite3.Connection, repo_id: str | None = None) -> int:
        query = "SELECT * FROM sessions"
        params: tuple[Any, ...] = ()
        if repo_id:
            query += " WHERE repo_id = ?"
            params = (repo_id,)
        rows = conn.execute(query, params).fetchall()

        groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
        for row in rows:
            key = (str(row["repo_id"]), str(row["tmux_session"]))
            groups.setdefault(key, []).append(row)

        merged = 0
        for group in groups.values():
            if len(group) < 2:
                continue

            ordered = sorted(group, key=self._session_priority, reverse=True)
            keeper = ordered[0]
            keeper_id = str(keeper["id"])
            merged_rows = [dict(keeper)]

            for duplicate in ordered[1:]:
                duplicate_id = str(duplicate["id"])
                merged_rows.append(dict(duplicate))
                conn.execute(
                    """
                    INSERT INTO session_events(
                        session_id, event_id, timestamp, kind, title, text, payload_json, created_at, updated_at
                    )
                    SELECT ?, event_id, timestamp, kind, title, text, payload_json, created_at, updated_at
                    FROM session_events
                    WHERE session_id = ?
                    ON CONFLICT(session_id, event_id) DO UPDATE SET
                      timestamp = excluded.timestamp,
                      kind = excluded.kind,
                      title = excluded.title,
                      text = excluded.text,
                      payload_json = excluded.payload_json,
                      updated_at = excluded.updated_at
                    """,
                    (keeper_id, duplicate_id),
                )
                conn.execute("DELETE FROM session_events WHERE session_id = ?", (duplicate_id,))
                conn.execute("DELETE FROM sessions WHERE id = ?", (duplicate_id,))
                merged += 1

            status = max(merged_rows, key=lambda row: self._status_rank(row.get("status"))).get("status")
            last_activity_at = max(
                str(row.get("last_activity_at") or row.get("updated_at") or row.get("created_at") or "")
                for row in merged_rows
            )

            def pick(field: str) -> Any:
                for row in ordered:
                    value = row[field]
                    if value is None:
                        continue
                    if isinstance(value, str) and not value.strip():
                        continue
                    return value
                return None

            created_at = min(str(row.get("created_at") or "") for row in merged_rows)
            updated_at = max(str(row.get("updated_at") or "") for row in merged_rows)
            conn.execute(
                """
                UPDATE sessions
                SET name = ?,
                    status = ?,
                    last_activity_at = ?,
                    last_prompt = ?,
                    execution_mode = ?,
                    codex_session_id = ?,
                    codex_session_file = ?,
                    codex_source = ?,
                    codex_model = ?,
                    created_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    pick("name"),
                    status,
                    last_activity_at,
                    pick("last_prompt"),
                    pick("execution_mode"),
                    pick("codex_session_id"),
                    pick("codex_session_file"),
                    pick("codex_source"),
                    pick("codex_model"),
                    created_at,
                    updated_at,
                    keeper_id,
                ),
            )

        return merged

    def dedupe_sessions(self, repo_id: str | None = None) -> int:
        with self._lock, self._connect() as conn:
            return self._dedupe_sessions_locked(conn, repo_id=repo_id)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _session_order_sql() -> str:
        return """
            CASE status
                WHEN 'running' THEN 5
                WHEN 'waiting_input' THEN 4
                WHEN 'waiting_approval' THEN 3
                WHEN 'completed' THEN 2
                WHEN 'failed' THEN 1
                ELSE 0
            END DESC,
            COALESCE(last_activity_at, updated_at, created_at) DESC,
            updated_at DESC
        """

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
                    last_activity_at TEXT,
                    last_prompt TEXT,
                    execution_mode TEXT,
                    codex_session_id TEXT,
                    codex_session_file TEXT,
                    codex_source TEXT,
                    codex_model TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(repo_id) REFERENCES repos(id)
                );

                CREATE TABLE IF NOT EXISTS session_events (
                    session_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    timestamp TEXT,
                    kind TEXT,
                    title TEXT,
                    text TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(session_id, event_id),
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                );

                CREATE TABLE IF NOT EXISTS repo_focus (
                    repo_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    reason TEXT,
                    activity_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(repo_id) REFERENCES repos(id),
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                );

                CREATE INDEX IF NOT EXISTS idx_session_events_session_time
                ON session_events(session_id, timestamp, created_at);

                CREATE INDEX IF NOT EXISTS idx_repo_focus_updated
                ON repo_focus(updated_at DESC);
                """
            )
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
            if "name" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN name TEXT")
            if "last_activity_at" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN last_activity_at TEXT")
                conn.execute("UPDATE sessions SET last_activity_at = updated_at WHERE last_activity_at IS NULL")
            if "execution_mode" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN execution_mode TEXT")
            if "codex_session_id" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN codex_session_id TEXT")
            if "codex_session_file" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN codex_session_file TEXT")
            if "codex_source" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN codex_source TEXT")
            if "codex_model" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN codex_model TEXT")
            self._dedupe_sessions_locked(conn)
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_repo_tmux_unique
                ON sessions(repo_id, tmux_session)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_repo_updated
                ON sessions(repo_id, updated_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_repo_activity
                ON sessions(repo_id, last_activity_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_repo_codex
                ON sessions(repo_id, codex_session_id)
                """
            )

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

    def get_repo_by_path(self, path: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM repos WHERE path = ?", (path,)).fetchone()
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
        execution_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = utc_now()
        session_id = str(uuid.uuid4())
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions(id, repo_id, tmux_session, name, status, last_activity_at, execution_mode, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_id, tmux_session) DO UPDATE SET
                  name = CASE
                    WHEN sessions.name IS NULL OR TRIM(sessions.name) = '' THEN excluded.name
                    ELSE sessions.name
                  END,
                  status = excluded.status,
                  last_activity_at = COALESCE(excluded.last_activity_at, sessions.last_activity_at),
                  execution_mode = COALESCE(excluded.execution_mode, sessions.execution_mode),
                  updated_at = excluded.updated_at
                """,
                (session_id, repo_id, tmux_session, name, status, now, execution_mode, now, now),
            )
            row = conn.execute(
                "SELECT * FROM sessions WHERE repo_id = ? AND tmux_session = ?",
                (repo_id, tmux_session),
            ).fetchone()
        return dict(row) if row else {}

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

    def find_session_by_tmux(self, repo_id: str, tmux_session: str) -> Optional[Dict[str, Any]]:
        self.dedupe_sessions(repo_id)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM sessions
                WHERE repo_id = ? AND tmux_session = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (repo_id, tmux_session),
            ).fetchone()
        return dict(row) if row else None

    def find_session_by_codex_session_id(
        self,
        repo_id: str,
        codex_session_id: str,
    ) -> Optional[Dict[str, Any]]:
        ref = str(codex_session_id or "").strip()
        if not ref:
            return None
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM sessions
                WHERE repo_id = ? AND codex_session_id = ?
                ORDER BY updated_at DESC
                """,
                (repo_id, ref),
            ).fetchall()
        if not rows:
            return None
        return dict(sorted(rows, key=self._session_priority, reverse=True)[0])

    def latest_session_for_repo(self, repo_id: str) -> Optional[Dict[str, Any]]:
        self.dedupe_sessions(repo_id)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM sessions WHERE repo_id = ? ORDER BY {self._session_order_sql()} LIMIT 1",
                (repo_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_sessions_for_repo(self, repo_id: str) -> List[Dict[str, Any]]:
        self.dedupe_sessions(repo_id)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM sessions WHERE repo_id = ? ORDER BY {self._session_order_sql()}",
                (repo_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def rename_session(self, session_id: str, name: str) -> Optional[Dict[str, Any]]:
        cleaned = (name or "").strip()
        if not cleaned:
            raise ValueError("session name cannot be empty")
        self.update_session(session_id, name=cleaned)
        return self.get_session(session_id)

    def get_repo_focus(self, repo_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM repo_focus WHERE repo_id = ?",
                (repo_id,),
            ).fetchone()
        return dict(row) if row else None

    def set_repo_focus(
        self,
        repo_id: str,
        session_id: str,
        *,
        reason: Optional[str] = None,
        activity_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        repo_key = str(repo_id or "").strip()
        session_key = str(session_id or "").strip()
        if not repo_key or not session_key:
            return None
        now = utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO repo_focus(repo_id, session_id, reason, activity_at, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_id) DO UPDATE SET
                  session_id = excluded.session_id,
                  reason = COALESCE(excluded.reason, repo_focus.reason),
                  activity_at = COALESCE(excluded.activity_at, repo_focus.activity_at),
                  updated_at = excluded.updated_at
                """,
                (repo_key, session_key, reason, activity_at, now, now),
            )
            row = conn.execute(
                "SELECT * FROM repo_focus WHERE repo_id = ?",
                (repo_key,),
            ).fetchone()
        return dict(row) if row else None

    def clear_repo_focus(self, repo_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM repo_focus WHERE repo_id = ?", (repo_id,))

    def merge_repo_into(self, source_repo_id: str, target_repo_id: str) -> None:
        source = str(source_repo_id or "").strip()
        target = str(target_repo_id or "").strip()
        if not source or not target or source == target:
            return

        with self._lock, self._connect() as conn:
            source_sessions = conn.execute(
                "SELECT * FROM sessions WHERE repo_id = ? ORDER BY updated_at DESC",
                (source,),
            ).fetchall()

            for row in source_sessions:
                duplicate = conn.execute(
                    """
                    SELECT *
                    FROM sessions
                    WHERE repo_id = ? AND tmux_session = ?
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (target, row["tmux_session"]),
                ).fetchone()

                if duplicate:
                    duplicate_id = str(duplicate["id"])
                    source_session_id = str(row["id"])
                    conn.execute(
                        """
                        INSERT INTO session_events(
                            session_id, event_id, timestamp, kind, title, text, payload_json, created_at, updated_at
                        )
                        SELECT ?, event_id, timestamp, kind, title, text, payload_json, created_at, updated_at
                        FROM session_events
                        WHERE session_id = ?
                        ON CONFLICT(session_id, event_id) DO UPDATE SET
                          timestamp = excluded.timestamp,
                          kind = excluded.kind,
                          title = excluded.title,
                          text = excluded.text,
                          payload_json = excluded.payload_json,
                          updated_at = excluded.updated_at
                        """,
                        (duplicate_id, source_session_id),
                    )
                    conn.execute("DELETE FROM session_events WHERE session_id = ?", (source_session_id,))
                    conn.execute("DELETE FROM sessions WHERE id = ?", (source_session_id,))
                    continue

                conn.execute(
                    "UPDATE sessions SET repo_id = ? WHERE id = ?",
                    (target, row["id"]),
                )

            source_focus = conn.execute(
                "SELECT * FROM repo_focus WHERE repo_id = ?",
                (source,),
            ).fetchone()
            target_focus = conn.execute(
                "SELECT * FROM repo_focus WHERE repo_id = ?",
                (target,),
            ).fetchone()
            if source_focus and (
                not target_focus or
                str(source_focus["updated_at"] or "") > str(target_focus["updated_at"] or "")
            ):
                now = utc_now()
                conn.execute(
                    """
                    INSERT INTO repo_focus(repo_id, session_id, reason, activity_at, created_at, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?)
                    ON CONFLICT(repo_id) DO UPDATE SET
                      session_id = excluded.session_id,
                      reason = excluded.reason,
                      activity_at = excluded.activity_at,
                      updated_at = excluded.updated_at
                    """,
                    (
                        target,
                        source_focus["session_id"],
                        source_focus["reason"],
                        source_focus["activity_at"],
                        str(target_focus["created_at"] if target_focus else now),
                        now,
                    ),
                )
            conn.execute("DELETE FROM repo_focus WHERE repo_id = ?", (source,))
            conn.execute("DELETE FROM repos WHERE id = ?", (source,))
            self._dedupe_sessions_locked(conn, repo_id=target)


    def upsert_session_events(self, session_id: str, events: List[Dict[str, Any]]) -> None:
        rows: list[tuple[Any, ...]] = []
        now = utc_now()
        for event in events:
            event_id = str((event or {}).get("id") or "").strip()
            if not event_id:
                continue
            payload = json.dumps(event, ensure_ascii=False)
            rows.append(
                (
                    session_id,
                    event_id,
                    event.get("timestamp"),
                    event.get("kind"),
                    event.get("title"),
                    event.get("text"),
                    payload,
                    now,
                    now,
                )
            )
        if not rows:
            return
        with self._lock, self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO session_events(
                    session_id, event_id, timestamp, kind, title, text, payload_json, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, event_id) DO UPDATE SET
                  timestamp = excluded.timestamp,
                  kind = excluded.kind,
                  title = excluded.title,
                  text = excluded.text,
                  payload_json = excluded.payload_json,
                  updated_at = excluded.updated_at
                """,
                rows,
            )

    def add_session_event(
        self,
        session_id: str,
        *,
        kind: str,
        title: str,
        text: str = "",
        timestamp: Optional[str] = None,
        event_id: Optional[str] = None,
        **extra: Any,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "id": event_id or f"local:{uuid.uuid4().hex}",
            "timestamp": timestamp or utc_now(),
            "kind": kind,
            "title": title,
            "text": text,
        }
        payload.update(extra)
        self.upsert_session_events(session_id, [payload])
        return payload

    def clear_session_events(self, session_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM session_events WHERE session_id = ?", (session_id,))

    def list_session_events(self, session_id: str, limit: int = 240) -> List[Dict[str, Any]]:
        fetch_limit = max(1, int(limit))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM session_events
                WHERE session_id = ?
                ORDER BY COALESCE(timestamp, created_at) DESC, created_at DESC
                LIMIT ?
                """,
                (session_id, fetch_limit),
            ).fetchall()
        events: List[Dict[str, Any]] = []
        for row in reversed(rows):
            try:
                events.append(json.loads(row["payload_json"]))
            except Exception:
                continue
        return events
