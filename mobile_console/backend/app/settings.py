from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    workspace_root: Path
    state_dir: Path
    state_db: Path
    default_repo: Path
    codex_wrapper: Path
    github_owner: str | None
    github_token: str | None
    github_default_private: bool
    repo_scan_depth: int
    max_diff_chars: int = 400_000


def load_settings() -> Settings:
    home = Path.home()
    raw_workspace_root = Path(os.environ.get("CODEX_WORKSPACE_ROOT", "/codex")).expanduser()
    if raw_workspace_root == Path("/codex") and not raw_workspace_root.exists():
        workspace_root = home / "codex"
    else:
        workspace_root = raw_workspace_root

    workspace_root.mkdir(parents=True, exist_ok=True)
    default_repo = Path(os.environ.get("CODEX_DEFAULT_REPO", str(workspace_root))).expanduser()
    state_dir = Path(os.environ.get("CODEX_MOBILE_STATE_DIR", str(home / ".local/share/codex-mobile"))).expanduser()
    codex_wrapper = Path(
        os.environ.get(
            "CODEX_MOBILE_WRAPPER",
            str(home / "ml_com/mobile_console/scripts/codex-mobile"),
        )
    ).expanduser()
    github_owner = os.environ.get("GITHUB_OWNER", "").strip() or None
    github_token = os.environ.get("GITHUB_TOKEN", "").strip() or None
    github_default_private = os.environ.get("GITHUB_DEFAULT_PRIVATE", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }
    try:
        repo_scan_depth = max(0, min(4, int(os.environ.get("CODEX_REPO_SCAN_DEPTH", "2"))))
    except ValueError:
        repo_scan_depth = 2

    state_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        workspace_root=workspace_root,
        state_dir=state_dir,
        state_db=state_dir / "state.db",
        default_repo=default_repo,
        codex_wrapper=codex_wrapper,
        github_owner=github_owner,
        github_token=github_token,
        github_default_private=github_default_private,
        repo_scan_depth=repo_scan_depth,
    )


SETTINGS = load_settings()
