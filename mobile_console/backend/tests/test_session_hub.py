from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.db import StateDB
from app.session_manager import SessionManager


class SessionHubTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tempdir.name) / "state.db"
        self.db = StateDB(db_path)
        self.mgr = SessionManager(self.db)
        self.repo_id = "demo_repo"
        self.db.upsert_repo(self.repo_id, "demo", self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _list_rows(self) -> list[dict]:
        return self.db.list_sessions_for_repo(self.repo_id)

    def _build_hub(self) -> dict:
        with patch.object(self.mgr, "sync_repo_sessions", return_value=self._list_rows()):
            return self.mgr.build_session_hub(self.repo_id)

    def test_mark_session_focus_persists_focus(self) -> None:
        row = self.db.create_session(self.repo_id, "codex_demo_shared", "running", name="共享会话", execution_mode="full-auto")
        focused = self.mgr.mark_session_focus(row["id"], reason="manual")

        self.assertEqual(focused["id"], row["id"])
        stored = self.db.get_repo_focus(self.repo_id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored["session_id"], row["id"])
        self.assertEqual(stored["reason"], "manual")

    def test_build_session_hub_prefers_recent_external_activity(self) -> None:
        shared = self.db.create_session(self.repo_id, "codex_demo_shared", "waiting_input", name="共享会话", execution_mode="full-auto")
        external = self.db.create_session(self.repo_id, "vscode:abc123", "waiting_input", name="VSCode abc123", execution_mode="external")
        self.db.update_session(shared["id"], last_activity_at="2026-03-18T09:00:00+00:00", codex_source="cli")
        self.db.update_session(external["id"], last_activity_at="2026-03-18T09:05:00+00:00", codex_source="vscode")

        hub = self._build_hub()

        self.assertEqual(hub["current_session_id"], external["id"])
        self.assertEqual(hub["suggested_session_id"], external["id"])
        self.assertIn(external["id"], hub["external_recent_session_ids"])
        self.assertIn("VSCode", hub["sync_hint"])

    def test_build_session_hub_archives_old_external_sessions(self) -> None:
        shared = self.db.create_session(self.repo_id, "codex_demo_shared", "running", name="共享会话", execution_mode="full-auto")
        archived = self.db.create_session(self.repo_id, "recorded:old-session", "completed", name="终端会话 old", execution_mode=None)
        self.db.update_session(shared["id"], last_activity_at="2026-03-18T09:06:00+00:00", codex_source="cli")
        self.db.update_session(archived["id"], last_activity_at="2026-03-16T01:00:00+00:00", codex_source="exec")

        hub = self._build_hub()

        self.assertEqual(hub["shared_session_id"], shared["id"])
        self.assertIn(archived["id"], hub["archived_session_ids"])
        self.assertNotIn(archived["id"], hub["recent_session_ids"])

    def test_build_session_hub_keeps_manual_focus(self) -> None:
        mobile = self.db.create_session(self.repo_id, "codex_demo_mobile", "completed", name="手机端当前会话", execution_mode="full-auto")
        vscode = self.db.create_session(self.repo_id, "vscode:recent-sync", "completed", name="VSCode recent", execution_mode="external")
        self.db.update_session(mobile["id"], last_activity_at="2026-03-18T09:24:00+00:00", codex_source="cli")
        self.db.update_session(vscode["id"], last_activity_at="2026-03-18T09:25:00+00:00", codex_source="vscode")
        self.db.set_repo_focus(self.repo_id, mobile["id"], reason="manual", activity_at="2026-03-18T09:24:00+00:00")

        hub = self._build_hub()

        self.assertEqual(hub["current_session_id"], mobile["id"])
        self.assertEqual(hub["focus_reason"], "manual")
        self.assertEqual(hub["suggested_session_id"], vscode["id"])

    def test_send_prompt_updates_focus_and_status(self) -> None:
        session = self.db.create_session(self.repo_id, "codex_demo_shared", "waiting_input", name="共享会话", execution_mode="workspace")

        with patch("app.session_manager.session_exists", return_value=True), \
             patch("app.session_manager.send_input") as send_input_mock, \
             patch("app.session_manager.capture_lines", return_value=[]), \
             patch("app.session_manager.tmux_send_key") as send_key_mock:
            updated = self.mgr.send_prompt(session["id"], "继续推进并汇报测试结果。")

        self.assertEqual(updated["status"], "running")
        self.assertEqual(updated["last_prompt"], "继续推进并汇报测试结果。")
        send_input_mock.assert_called_once()
        send_key_mock.assert_not_called()
        focus = self.db.get_repo_focus(self.repo_id)
        self.assertIsNotNone(focus)
        self.assertEqual(focus["session_id"], session["id"])

    def test_resume_session_by_id_recreates_missing_tmux_from_codex_session(self) -> None:
        session = self.db.create_session(
            self.repo_id,
            "codex_demo_mobile",
            "completed",
            name="手机端当前会话",
            execution_mode="full-auto",
        )
        self.db.update_session(
            session["id"],
            codex_session_id="019cf3f2-a93c-7103-ae3f-244c260a40c2",
        )

        with patch("app.session_manager.session_exists", return_value=False), \
             patch("app.session_manager.create_session") as create_session_mock:
            resumed = self.mgr.resume_session_by_id(session["id"])

        self.assertEqual(resumed["status"], "running")
        self.assertNotEqual(resumed["tmux_session"], "codex_demo_mobile")
        create_session_mock.assert_called_once()
        _, kwargs = create_session_mock.call_args
        self.assertEqual(
            kwargs["extra_args"][-2:],
            ["resume", "019cf3f2-a93c-7103-ae3f-244c260a40c2"],
        )
        focus = self.db.get_repo_focus(self.repo_id)
        self.assertIsNotNone(focus)
        self.assertEqual(focus["session_id"], session["id"])

    def test_session_output_keeps_running_after_recent_user_message(self) -> None:
        session = self.db.create_session(
            self.repo_id,
            "codex_demo_rehydrated",
            "running",
            name="历史恢复会话",
            execution_mode="full-auto",
        )
        self.db.add_session_event(
            session["id"],
            event_id="mobile:user:check",
            kind="user_message",
            title="你的任务",
            text="请只回复 mobile-sync-check。",
        )

        with patch("app.session_manager.session_exists", return_value=True), \
             patch("app.session_manager.capture_lines", return_value=["completed"]):
            lines = self.mgr.session_output(session["id"], lines=20)

        self.assertEqual(lines, ["completed"])
        refreshed = self.db.get_session(session["id"])
        self.assertIsNotNone(refreshed)
        self.assertEqual(refreshed["status"], "running")

    def test_update_binding_does_not_overwrite_newer_last_prompt(self) -> None:
        session = self.db.create_session(
            self.repo_id,
            "codex_demo_bound",
            "running",
            name="绑定会话",
            execution_mode="full-auto",
        )
        self.db.update_session(
            session["id"],
            codex_session_id="sess-bound",
            codex_session_file=str(Path(self.tempdir.name) / "rollout-test.jsonl"),
            codex_source="cli",
            last_prompt="新的手机 prompt",
            last_activity_at="2026-03-18T10:00:00+00:00",
        )
        updated = self.db.get_session(session["id"])
        assert updated is not None

        self.mgr._update_binding(
            session["id"],
            updated,
            last_prompt="旧的历史 prompt",
            last_prompt_timestamp="2026-03-18T09:00:00+00:00",
            last_activity_at="2026-03-18T09:00:00+00:00",
        )

        refreshed = self.db.get_session(session["id"])
        self.assertIsNotNone(refreshed)
        self.assertEqual(refreshed["last_prompt"], "新的手机 prompt")
        self.assertEqual(refreshed["last_activity_at"], "2026-03-18T10:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
