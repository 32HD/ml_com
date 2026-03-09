from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path
from typing import List, Optional


def _run_tmux(args: List[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *args],
        check=check,
        text=True,
        capture_output=True,
    )


def normalize_repo_id(path: str) -> str:
    base = Path(path).name.lower()
    safe = re.sub(r"[^a-z0-9_]+", "_", base).strip("_")
    return safe or "repo"


def tmux_session_name(repo_id: str) -> str:
    return f"codex_{repo_id}"


def session_exists(session_name: str) -> bool:
    result = _run_tmux(["has-session", "-t", session_name], check=False)
    return result.returncode == 0


def kill_session(session_name: str) -> None:
    _run_tmux(["kill-session", "-t", session_name], check=False)


def create_session(
    session_name: str,
    repo_path: str,
    wrapper_path: str,
    extra_args: Optional[List[str]] = None,
) -> None:
    args = [shlex.quote(repo_path)]
    for item in extra_args or []:
        args.append(shlex.quote(str(item)))
    cmd = f"cd {shlex.quote(repo_path)} && {shlex.quote(wrapper_path)} {' '.join(args)}"
    _run_tmux(["new-session", "-d", "-s", session_name, "bash", "-lc", cmd])


def send_input(session_name: str, text: str, enter: bool = True) -> None:
    _run_tmux(["send-keys", "-t", session_name, text])
    if enter:
        # Codex TUI reliably treats C-m as submit; Enter can be interpreted as plain newline.
        _run_tmux(["send-keys", "-t", session_name, "C-m"])


_KEY_ALIASES = {
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "enter": "C-m",
    "return": "C-m",
    "tab": "Tab",
    "esc": "Escape",
    "escape": "Escape",
    "backspace": "BSpace",
    "space": "Space",
    "ctrl+c": "C-c",
    "ctrl+d": "C-d",
    "ctrl+z": "C-z",
}

_ALLOWED_KEYS = {
    "Up",
    "Down",
    "Left",
    "Right",
    "C-m",
    "Tab",
    "Escape",
    "BSpace",
    "Space",
    "C-c",
    "C-d",
    "C-z",
}


def _normalize_key(key: str) -> str:
    raw = (key or "").strip()
    if not raw:
        raise ValueError("key is required")
    alias = _KEY_ALIASES.get(raw.lower())
    tmux_key = alias or raw
    if tmux_key not in _ALLOWED_KEYS:
        raise ValueError(f"unsupported key: {key}")
    return tmux_key


def send_key(session_name: str, key: str, repeat: int = 1) -> None:
    tmux_key = _normalize_key(key)
    times = max(1, min(int(repeat), 20))
    for _ in range(times):
        _run_tmux(["send-keys", "-t", session_name, tmux_key])


def capture_lines(session_name: str, lines: int = 800) -> List[str]:
    result = _run_tmux(["capture-pane", "-p", "-t", session_name, "-S", f"-{lines}"])
    return result.stdout.splitlines()


def list_session_names() -> List[str]:
    result = _run_tmux(["list-sessions", "-F", "#S"], check=False)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def session_current_path(session_name: str) -> Optional[str]:
    # Prefer pane 0 path; fall back to session target.
    result = _run_tmux(["display-message", "-p", "-t", f"{session_name}:0.0", "#{pane_current_path}"], check=False)
    if result.returncode != 0:
        result = _run_tmux(["display-message", "-p", "-t", session_name, "#{pane_current_path}"], check=False)
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None
