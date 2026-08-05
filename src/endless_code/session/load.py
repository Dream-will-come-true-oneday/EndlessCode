"""从 JSONL 恢复可继续使用的 Conversation 历史。"""

import json
from dataclasses import dataclass
from pathlib import Path

from endless_code.llm import Message, ToolCall, ToolResult


@dataclass(frozen=True)
class LoadedSession:
    messages: list[Message]
    last_timestamp: int | None


def load_session(session_dir: str) -> LoadedSession:
    path = Path(session_dir) / "conversation.jsonl"
    entries: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return LoadedSession([], None)
    for raw in lines:
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if entry.get("type") == "compact":
            entries = []
        elif entry.get("role") in {"user", "assistant", "tool"}:
            entries.append(entry)

    messages = [_to_message(entry) for entry in entries]
    if messages and messages[-1].role == "assistant" and messages[-1].tool_calls:
        messages.pop()
        entries.pop()
    timestamp = entries[-1].get("ts") if entries else None
    return LoadedSession(messages, timestamp if isinstance(timestamp, int) else None)


def _to_message(entry: dict) -> Message:
    tool_calls = [
        ToolCall(id=str(item["id"]), name=str(item["name"]), input=str(item["input"]))
        for item in entry.get("tool_calls", [])
        if isinstance(item, dict) and {"id", "name", "input"} <= item.keys()
    ]
    tool_results = [
        ToolResult(
            tool_call_id=str(item["tool_call_id"]),
            content=str(item["content"]),
            is_error=bool(item.get("is_error", False)),
        )
        for item in entry.get("tool_results", [])
        if isinstance(item, dict) and {"tool_call_id", "content"} <= item.keys()
    ]
    return Message(
        role=entry["role"],
        content=str(entry.get("content", "")),
        tool_calls=tool_calls,
        tool_results=tool_results,
    )
