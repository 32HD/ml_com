from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict


def _run_git(repo_path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_path,
        text=True,
        capture_output=True,
        check=False,
    )


def git_status(repo_path: Path) -> Dict[str, Any]:
    branch_cp = _run_git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    branch = branch_cp.stdout.strip() if branch_cp.returncode == 0 else "(no-git)"
    cp = _run_git(repo_path, ["status", "--porcelain"])
    files = []
    if cp.returncode == 0:
        for line in cp.stdout.splitlines():
            if len(line) >= 4:
                files.append(line[3:])
    return {
        "branch": branch,
        "dirty_files": files,
        "ok": cp.returncode == 0,
        "stderr": cp.stderr,
    }


def git_diff(repo_path: Path, file_path: str | None = None) -> Dict[str, Any]:
    args = ["diff", "--no-color"]
    if file_path:
        args.append("--")
        args.append(file_path)
    cp = _run_git(repo_path, args)
    return {
        "ok": cp.returncode == 0,
        "diff": cp.stdout,
        "stderr": cp.stderr,
    }
