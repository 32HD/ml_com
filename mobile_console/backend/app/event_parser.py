from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Iterable, Tuple

APPROVAL_PATTERNS = [
    re.compile(r"approve", re.IGNORECASE),
    re.compile(r"permission", re.IGNORECASE),
    re.compile(r"allow .*\?", re.IGNORECASE),
    re.compile(r"批准", re.IGNORECASE),
    re.compile(r"确认", re.IGNORECASE),
]

ERROR_PATTERNS = [
    re.compile(r"\berror\b", re.IGNORECASE),
    re.compile(r"\bfailed\b", re.IGNORECASE),
    re.compile(r"traceback", re.IGNORECASE),
]

DONE_PATTERNS = [
    re.compile(r"\bdone\b", re.IGNORECASE),
    re.compile(r"completed", re.IGNORECASE),
    re.compile(r"finished", re.IGNORECASE),
]


def has_approval_request(lines: Iterable[str]) -> bool:
    tail = "\n".join(list(lines)[-60:])
    return any(p.search(tail) for p in APPROVAL_PATTERNS)


def detect_status(lines: Iterable[str]) -> str:
    all_lines = list(lines)
    if not all_lines:
        return "idle"
    tail = "\n".join(all_lines[-120:])
    if has_approval_request(all_lines):
        return "waiting_approval"
    if any(p.search(tail) for p in ERROR_PATTERNS):
        return "failed"
    if any(p.search(tail) for p in DONE_PATTERNS):
        return "completed"
    return "running"


def split_incremental(old_lines: list[str], new_lines: list[str]) -> Tuple[list[str], list[str]]:
    if not old_lines:
        return new_lines, new_lines
    if new_lines == old_lines:
        return [], new_lines

    # Fast path: simple append.
    if len(new_lines) >= len(old_lines) and new_lines[: len(old_lines)] == old_lines:
        return new_lines[len(old_lines) :], new_lines

    # When tmux capture window slides, find max overlap:
    # old tail == new head, then emit the truly new suffix.
    max_k = min(len(old_lines), len(new_lines))
    overlap = 0
    for k in range(max_k, 0, -1):
        if old_lines[-k:] == new_lines[:k]:
            overlap = k
            break
    if overlap:
        return new_lines[overlap:], new_lines

    # Fallback for "screen repaint" style updates in tmux where many lines shift.
    # Emit only changed fragments from the tail to avoid full-screen spam/flicker.
    old_tail = old_lines[-160:]
    new_tail = new_lines[-160:]
    matcher = SequenceMatcher(a=old_tail, b=new_tail, autojunk=False)
    delta: list[str] = []
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in {"insert", "replace"}:
            delta.extend(new_tail[j1:j2])

    if not delta and new_tail != old_tail:
        seen = set(old_tail[-24:])
        delta = [line for line in new_tail[-12:] if line and line not in seen]

    return delta[-80:], new_lines
