"""会话列表的纯文件系统扫描逻辑。"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from endless_code.compact import parse_session_time


@dataclass(frozen=True)
class SessionInfo:
    id: str
    title: str
    modified_at: datetime
    model: str
    size: int
    dir: str


def list_sessions(sessions_dir: str) -> list[SessionInfo]:
    root = Path(sessions_dir)
    if not root.is_dir():
        return []
    sessions: list[SessionInfo] = []
    for directory in root.iterdir():
        if not directory.is_dir() or parse_session_time(directory.name) is None:
            continue
        path = directory / "conversation.jsonl"
        if not path.is_file():
            continue
        title, model = _read_summary(path)
        stat = path.stat()
        sessions.append(
            SessionInfo(
                id=directory.name,
                title=title,
                modified_at=datetime.fromtimestamp(stat.st_mtime).astimezone(),
                model=model or "未知模型",
                size=stat.st_size,
                dir=str(directory),
            )
        )
    return sorted(sessions, key=lambda item: item.modified_at, reverse=True)


def _read_summary(path: Path) -> tuple[str, str]:
    model = ""
    try:
        with path.open(encoding="utf-8") as file:
            for raw in file:
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not model and isinstance(entry.get("model"), str):
                    model = entry["model"]
                if entry.get("role") == "user" and isinstance(
                    entry.get("content"), str
                ):
                    text = entry["content"].strip().replace("\n", " ")
                    return _truncate(text) or "（空消息）", model
    except OSError:
        return "（无法读取）", model
    return "（无标题会话）", model


def _truncate(text: str, limit: int = 50) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"
