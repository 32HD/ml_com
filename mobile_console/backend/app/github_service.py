from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict


API_BASE = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


def _api_request(method: str, path: str, token: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    url = f"{API_BASE}{path}"
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "codex-mobile",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        detail = text
        try:
            payload = json.loads(text)
            detail = payload.get("message") or detail
        except json.JSONDecodeError:
            pass
        raise GitHubError(f"GitHub API {method} {path} failed ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise GitHubError(f"GitHub API request failed: {exc.reason}") from exc


def github_login(token: str) -> str:
    info = _api_request("GET", "/user", token)
    login = str(info.get("login", "")).strip()
    if not login:
        raise GitHubError("Cannot resolve GitHub login from token")
    return login


def get_repo(token: str, owner: str, repo: str) -> Dict[str, Any]:
    owner_q = urllib.parse.quote(owner)
    repo_q = urllib.parse.quote(repo)
    return _api_request("GET", f"/repos/{owner_q}/{repo_q}", token)


def create_repo(
    token: str,
    repo: str,
    owner: str | None,
    description: str,
    private: bool,
) -> Dict[str, Any]:
    login = github_login(token)
    repo = repo.strip()
    if not repo:
        raise GitHubError("repo name is required")

    target_owner = owner.strip() if owner else login
    payload = {
        "name": repo,
        "description": description or "",
        "private": bool(private),
        "auto_init": False,
    }

    try:
        if target_owner == login:
            data = _api_request("POST", "/user/repos", token, payload)
        else:
            owner_q = urllib.parse.quote(target_owner)
            data = _api_request("POST", f"/orgs/{owner_q}/repos", token, payload)
        data["existed"] = False
        return data
    except GitHubError as exc:
        # Allow idempotent behavior when repo already exists.
        msg = str(exc).lower()
        if "name already exists" in msg or "already exists" in msg:
            existing = get_repo(token, target_owner, repo)
            existing["existed"] = True
            return existing
        raise
