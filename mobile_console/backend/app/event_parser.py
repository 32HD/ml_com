from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Iterable, Sequence, Tuple

ANSI_ESCAPE_RE = re.compile(r"\u001b\[[0-9;?]*[ -/]*[@-~]|\u001b\][^\u0007]*(?:\u0007|\u001b\\)")
PROMPT_LINE_RE = re.compile(r"^[›>]\s+(.+)$")
ASSISTANT_LINE_RE = re.compile(r"^[•●]\s+(.+)$")
LIST_ITEM_RE = re.compile(r"^(?:[-*•●]|\d+[.)])\s+")
WORKING_PATTERNS = [
    re.compile(r"\bworking\b", re.IGNORECASE),
    re.compile(r"esc to interrupt", re.IGNORECASE),
]
PLACEHOLDER_PROMPTS = {
    "Write tests for @filename",
}
UI_NOISE_PATTERNS = [
    re.compile(r"^\?\s+for shortcuts", re.IGNORECASE),
    re.compile(r"^\d+%\s+context left$", re.IGNORECASE),
    re.compile(r"^tip:", re.IGNORECASE),
    re.compile(r"^openai codex", re.IGNORECASE),
    re.compile(r"^model:\s+", re.IGNORECASE),
    re.compile(r"^directory:\s+", re.IGNORECASE),
    re.compile(r"^approval policy:\s+", re.IGNORECASE),
    re.compile(r"^sandbox:\s+", re.IGNORECASE),
    re.compile(r"^reasoning effort:\s+", re.IGNORECASE),
    re.compile(r"^session id:\s+", re.IGNORECASE),
    re.compile(r"^workspace:\s+", re.IGNORECASE),
    re.compile(r"^provider:\s+", re.IGNORECASE),
    re.compile(r"^build faster with codex", re.IGNORECASE),
    re.compile(r"^.+\s+·\s+\d+%\s+left\s+·\s+.+$", re.IGNORECASE),
]
APPROVAL_PATTERNS = [
    re.compile(r"waiting[_ ]approval", re.IGNORECASE),
    re.compile(r"waiting for approval", re.IGNORECASE),
    re.compile(r"approval required", re.IGNORECASE),
    re.compile(r"request(?:ed)? approval", re.IGNORECASE),
    re.compile(r"approve.+\?", re.IGNORECASE),
    re.compile(r"permission.+\?", re.IGNORECASE),
    re.compile(r"allow.+\?", re.IGNORECASE),
    re.compile(r"是否允许", re.IGNORECASE),
    re.compile(r"请求批准", re.IGNORECASE),
    re.compile(r"等待批准", re.IGNORECASE),
    re.compile(r"\by/n\b", re.IGNORECASE),
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


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", str(text or "")).replace("\r", "")


def normalize_terminal_line(text: str) -> str:
    return strip_ansi(text).strip()


def collapse_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _is_ui_noise(text: str) -> bool:
    clean = collapse_spaces(text)
    if not clean:
        return True
    if clean.startswith("╭") or clean.startswith("╰") or clean.startswith("│"):
        return True
    return any(pattern.search(clean) for pattern in UI_NOISE_PATTERNS)


def extract_prompt_line(text: str) -> str:
    match = PROMPT_LINE_RE.match(collapse_spaces(text))
    if not match:
        return ""
    prompt = match.group(1).strip()
    if prompt in PLACEHOLDER_PROMPTS:
        return ""
    return prompt


def has_live_work(lines: Iterable[str]) -> bool:
    tail = "\n".join(collapse_spaces(line) for line in list(lines)[-40:])
    return any(pattern.search(tail) for pattern in WORKING_PATTERNS)


def has_approval_request(lines: Iterable[str]) -> bool:
    tail = "\n".join(collapse_spaces(line) for line in list(lines)[-60:])
    return any(pattern.search(tail) for pattern in APPROVAL_PATTERNS)


def detect_status(lines: Iterable[str]) -> str:
    all_lines = [normalize_terminal_line(line) for line in list(lines)]
    if not all_lines:
        return "idle"
    tail = "\n".join(all_lines[-120:])
    if has_approval_request(all_lines):
        return "waiting_approval"
    if has_live_work(all_lines):
        return "running"
    if any(pattern.search(tail) for pattern in ERROR_PATTERNS):
        return "failed"
    if any(pattern.search(tail) for pattern in DONE_PATTERNS):
        return "completed"
    if any(ASSISTANT_LINE_RE.match(collapse_spaces(line)) for line in all_lines[-80:]):
        return "completed"
    return "idle"


def prompt_pending_submission(lines: Sequence[str], prompt: str) -> bool:
    target = collapse_spaces(prompt)
    if not target:
        return False
    clean_lines = [normalize_terminal_line(line) for line in lines[-120:]]
    if has_live_work(clean_lines):
        return False
    return any(extract_prompt_line(line) == target for line in clean_lines)


def _join_prompt_parts(parts: Sequence[str]) -> str:
    return " ".join(collapse_spaces(part) for part in parts if collapse_spaces(part)).strip()


def _join_answer_parts(parts: Sequence[str]) -> str:
    merged: list[str] = []
    current: list[str] = []
    for raw in parts:
        line = collapse_spaces(raw)
        if not line:
            if current:
                merged.append(" ".join(current).strip())
                current = []
            if merged and merged[-1] != "":
                merged.append("")
            continue
        if LIST_ITEM_RE.match(line):
            if current:
                merged.append(" ".join(current).strip())
                current = []
            merged.append(line)
            continue
        current.append(line)
    if current:
        merged.append(" ".join(current).strip())

    out: list[str] = []
    for item in merged:
        if not item:
            if out and out[-1] != "":
                out.append("")
            continue
        out.append(item)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out).strip())


def _prompt_match_score(prompt: str, prompt_candidates: Sequence[str]) -> float:
    target = collapse_spaces(prompt)
    if not target:
        return 0.0
    best = 0.0
    for candidate in prompt_candidates:
        probe = collapse_spaces(candidate)
        if not probe:
            continue
        if target == probe:
            return 1.0
        short, long = (target, probe) if len(target) <= len(probe) else (probe, target)
        if short and short in long:
            best = max(best, len(short) / max(len(long), 1))
            continue
        best = max(best, SequenceMatcher(a=target, b=probe, autojunk=False).ratio())
    return best


def extract_terminal_turns(lines: Sequence[str], max_turns: int = 8) -> list[dict[str, str]]:
    clean_lines = [normalize_terminal_line(line) for line in lines or []]
    turns: list[dict[str, str]] = []
    prompt_parts: list[str] = []
    answer_parts: list[str] = []
    saw_blank_after_prompt = False
    capturing_answer = False

    def flush() -> None:
        nonlocal prompt_parts, answer_parts, saw_blank_after_prompt, capturing_answer
        prompt_text = _join_prompt_parts(prompt_parts)
        answer_text = _join_answer_parts(answer_parts)
        if prompt_text:
            turns.append({"prompt": prompt_text, "answer": answer_text})
        prompt_parts = []
        answer_parts = []
        saw_blank_after_prompt = False
        capturing_answer = False

    for raw in clean_lines:
        prompt = extract_prompt_line(raw)
        line = collapse_spaces(raw)

        if prompt:
            flush()
            prompt_parts = [prompt]
            continue

        if not prompt_parts:
            continue

        if not line:
            if capturing_answer and answer_parts and answer_parts[-1] != "":
                answer_parts.append("")
            else:
                saw_blank_after_prompt = True
            continue

        if _is_ui_noise(line):
            continue
        if has_live_work([line]) or has_approval_request([line]):
            continue

        assistant_match = ASSISTANT_LINE_RE.match(line)
        if assistant_match:
            answer_parts.append(assistant_match.group(1).strip())
            capturing_answer = True
            continue

        if capturing_answer or saw_blank_after_prompt:
            answer_parts.append(line)
            capturing_answer = True
            continue

        prompt_parts.append(line)

    flush()
    if max_turns <= 0:
        return turns
    return turns[-max_turns:]


def extract_terminal_latest_prompt(lines: Sequence[str]) -> str:
    turns = extract_terminal_turns(lines, max_turns=4)
    if turns:
        return str(turns[-1].get("prompt") or "").strip()
    for raw in reversed(lines or []):
        prompt = extract_prompt_line(raw)
        if prompt:
            return prompt
    return ""


def extract_terminal_latest_answer(lines: Sequence[str], prompts: Sequence[str] | None = None) -> dict[str, str] | None:
    turns = extract_terminal_turns(lines, max_turns=max(8, len(prompts or []) * 2 or 8))
    if not turns:
        return None

    prompt_candidates = [collapse_spaces(item) for item in (prompts or []) if collapse_spaces(item)]
    if prompt_candidates:
        for turn in reversed(turns):
            answer = str(turn.get("answer") or "").strip()
            if not answer:
                continue
            if _prompt_match_score(str(turn.get("prompt") or ""), prompt_candidates) >= 0.55:
                return {"prompt": str(turn.get("prompt") or "").strip(), "answer": answer}

    for turn in reversed(turns):
        answer = str(turn.get("answer") or "").strip()
        if answer:
            return {"prompt": str(turn.get("prompt") or "").strip(), "answer": answer}
    return None


def split_incremental(old_lines: list[str], new_lines: list[str]) -> Tuple[list[str], list[str]]:
    if not old_lines:
        return new_lines, new_lines
    if new_lines == old_lines:
        return [], new_lines

    if len(new_lines) >= len(old_lines) and new_lines[: len(old_lines)] == old_lines:
        return new_lines[len(old_lines) :], new_lines

    max_k = min(len(old_lines), len(new_lines))
    overlap = 0
    for k in range(max_k, 0, -1):
        if old_lines[-k:] == new_lines[:k]:
            overlap = k
            break
    if overlap:
        return new_lines[overlap:], new_lines

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
