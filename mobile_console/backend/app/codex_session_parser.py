from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


EXIT_CODE_RE = re.compile(r"Process exited with code (\d+)")


def _normalize_prompt_text(text: Any) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    marker = "## My request for Codex:"
    if marker in raw:
        trimmed = raw.split(marker, 1)[1].strip()
        return trimmed or raw
    return raw


def parse_session_meta(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            first = handle.readline().strip()
        if not first:
            return None
        row = json.loads(first)
        if row.get("type") != "session_meta":
            return None
        payload = row.get("payload") or {}
        session_id = str(payload.get("id") or "").strip()
        cwd = str(payload.get("cwd") or "").strip()
        if not session_id or not cwd:
            return None
        return {
            "id": session_id,
            "timestamp": payload.get("timestamp") or row.get("timestamp"),
            "cwd": cwd,
            "originator": str(payload.get("originator") or "").strip(),
            "source": str(payload.get("source") or "").strip(),
            "model": str(payload.get("model") or "").strip() or None,
            "path": str(path),
        }
    except Exception:
        return None


def _event(event_id: str, timestamp: str | None, kind: str, title: str, text: str = "", **extra: Any) -> dict[str, Any]:
    payload = {
        "id": event_id,
        "timestamp": timestamp,
        "kind": kind,
        "title": title,
        "text": text,
    }
    payload.update(extra)
    return payload


def _extract_reasoning_text(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or []
    parts: list[str] = []
    if isinstance(summary, list):
        for item in summary:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if text:
                parts.append(text)
    return "\n\n".join(parts).strip()


def _short_text(text: str, limit: int = 1200) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit].rstrip()}\n…"


def _parse_exit_code(text: str) -> int | None:
    match = EXIT_CODE_RE.search(text or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def read_timeline(path: Path, limit: int = 120) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    events: list[dict[str, Any]] = []
    call_map: dict[str, dict[str, Any]] = {}

    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_no, raw in enumerate(handle, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue

                timestamp = row.get("timestamp")
                event_id = f"{path.name}:{line_no}"
                row_type = row.get("type")

                if row_type == "event_msg":
                    payload = row.get("payload") or {}
                    kind = payload.get("type")
                    if kind == "task_started":
                        events.append(
                            _event(
                                event_id,
                                timestamp,
                                "task_started",
                                "开始任务",
                                "Codex 开始处理这一轮任务。",
                                turn_id=payload.get("turn_id"),
                            )
                        )
                    elif kind == "user_message":
                        message = _normalize_prompt_text(payload.get("message"))
                        if message:
                            events.append(_event(event_id, timestamp, "user_message", "你的任务", message))
                    elif kind == "agent_reasoning":
                        text = str(payload.get("text") or "").strip()
                        if text:
                            events.append(_event(event_id, timestamp, "reasoning", "推理摘要", _short_text(text)))
                    elif kind == "agent_message":
                        message = str(payload.get("message") or "").strip()
                        if not message:
                            continue
                        phase = str(payload.get("phase") or "commentary").strip()
                        if phase == "final_answer":
                            events.append(_event(event_id, timestamp, "final_answer", "最终答复", _short_text(message), phase=phase))
                        else:
                            events.append(_event(event_id, timestamp, "commentary", "过程播报", _short_text(message), phase=phase))
                    elif kind == "task_complete":
                        text = str(payload.get("last_agent_message") or "任务已完成").strip()
                        events.append(
                            _event(
                                event_id,
                                timestamp,
                                "task_complete",
                                "任务完成",
                                _short_text(text),
                                turn_id=payload.get("turn_id"),
                            )
                        )
                    elif kind == "turn_aborted":
                        events.append(_event(event_id, timestamp, "task_aborted", "任务中断", "本轮任务被中断。"))

                if row_type != "response_item":
                    continue

                payload = row.get("payload") or {}
                item_type = payload.get("type")
                if item_type == "function_call":
                    name = str(payload.get("name") or "tool").strip() or "tool"
                    call_id = str(payload.get("call_id") or "").strip()
                    arguments = payload.get("arguments") or "{}"
                    try:
                        parsed_args = json.loads(arguments) if isinstance(arguments, str) else arguments
                    except Exception:
                        parsed_args = {}
                    if not isinstance(parsed_args, dict):
                        parsed_args = {}

                    call_map[call_id] = {
                        "name": name,
                        "arguments": parsed_args,
                    }

                    if name == "exec_command":
                        command = str(parsed_args.get("cmd") or "").strip()
                        workdir = str(parsed_args.get("workdir") or "").strip()
                        body = command
                        if workdir:
                            body = f"{body}\n目录：{workdir}" if body else f"目录：{workdir}"
                        events.append(
                            _event(
                                event_id,
                                timestamp,
                                "tool_call",
                                "执行命令",
                                _short_text(body or arguments),
                                tool_name=name,
                                call_id=call_id,
                                command=command or None,
                                workdir=workdir or None,
                            )
                        )
                    elif name == "update_plan":
                        steps = parsed_args.get("plan") or []
                        summary = " / ".join(
                            str(step.get("step") or "").strip()
                            for step in steps
                            if isinstance(step, dict) and step.get("step")
                        )
                        events.append(
                            _event(
                                event_id,
                                timestamp,
                                "tool_call",
                                "更新计划",
                                _short_text(summary or "计划已更新。"),
                                tool_name=name,
                                call_id=call_id,
                            )
                        )
                    else:
                        body = arguments if isinstance(arguments, str) else json.dumps(parsed_args, ensure_ascii=False)
                        events.append(
                            _event(
                                event_id,
                                timestamp,
                                "tool_call",
                                f"调用工具：{name}",
                                _short_text(body),
                                tool_name=name,
                                call_id=call_id,
                            )
                        )

                elif item_type == "function_call_output":
                    call_id = str(payload.get("call_id") or "").strip()
                    tool = call_map.get(call_id) or {}
                    name = str(tool.get("name") or "tool").strip() or "tool"
                    output = str(payload.get("output") or "").strip()
                    exit_code = _parse_exit_code(output)
                    if name == "update_plan":
                        title = "计划已更新"
                    elif name == "exec_command":
                        title = "命令结果"
                    else:
                        title = "工具输出"
                    status = None
                    if exit_code is not None:
                        status = "ok" if exit_code == 0 else "error"
                    events.append(
                        _event(
                            event_id,
                            timestamp,
                            "tool_output",
                            title,
                            _short_text(output or "完成"),
                            tool_name=name,
                            call_id=call_id,
                            exit_code=exit_code,
                            status=status,
                        )
                    )

    except Exception:
        return []

    return events[-limit:]
