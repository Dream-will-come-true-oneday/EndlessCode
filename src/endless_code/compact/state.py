"""上下文压缩的会话级状态。"""

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from endless_code.compact.const import MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES


@dataclass(frozen=True)
class SessionContext:
    session_id: str
    spill_dir: str


def new_session_context(workspace: str) -> SessionContext:
    """创建当前进程唯一的工具结果落盘目录。"""
    session_id = f"{int(time.time())}-{secrets.token_hex(4)}"
    spill_dir = (
        Path(workspace) / ".endless-code" / "sessions" / session_id / "tool-results"
    )
    spill_dir.mkdir(parents=True, exist_ok=True)
    return SessionContext(session_id=session_id, spill_dir=str(spill_dir))


class ContentReplacementState:
    """工具结果替换决策账本。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._seen_ids: set[str] = set()
        self._replacements: dict[str, str] = {}

    def existing(self, tool_use_id: str) -> tuple[bool, str | None]:
        with self._lock:
            return tool_use_id in self._seen_ids, self._replacements.get(tool_use_id)

    def decide_once(
        self,
        tool_use_id: str,
        original_content: str,
        decide: Callable[[], tuple[str, str]],
    ) -> str:
        """原子完成查询、决策与账本写入。"""
        with self._lock:
            if tool_use_id in self._seen_ids:
                return self._replacements.get(tool_use_id, original_content)

            decision, preview = decide()
            if decision == "replaced":
                self._seen_ids.add(tool_use_id)
                self._replacements[tool_use_id] = preview
                return preview
            if decision == "kept":
                self._seen_ids.add(tool_use_id)
            return original_content


class CompactCircuitBreaker:
    """自动摘要连续失败计数。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._consecutive_failures = 0

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1

    def tripped(self) -> bool:
        with self._lock:
            return self._consecutive_failures >= MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES


@dataclass(frozen=True)
class FileReadRecord:
    path: str
    content: str
    timestamp: datetime


class RecoveryState:
    """最近成功读取文件的线程安全快照。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._files: dict[str, FileReadRecord] = {}

    def record_file(self, path: str, content: str) -> None:
        resolved = str(Path(path).expanduser().resolve())
        record = FileReadRecord(resolved, content, datetime.now(UTC))
        with self._lock:
            self._files[resolved] = record

    def snapshot(self) -> list[FileReadRecord]:
        with self._lock:
            records = list(self._files.values())
        return sorted(records, key=lambda record: record.timestamp, reverse=True)
