"""崩溃安全的会话 JSONL 追加写入器。"""

import json
import logging
import os
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Self

from endless_code.llm import Message

logger = logging.getLogger(__name__)


class Writer:
    """在同步 Conversation 回调中串行追加 JSONL，并立即刷盘。"""

    def __init__(self, session_dir: str, model: str) -> None:
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.session_dir / "conversation.jsonl"
        self._model = model
        self._lock = threading.Lock()
        self._file = self.path.open("a", encoding="utf-8", newline="\n")
        self._is_first = self.path.stat().st_size == 0

    @classmethod
    def open_existing(cls, session_dir: str, model: str) -> "Writer":
        return cls(session_dir, model)

    def append(self, message: Message) -> None:
        entry: dict[str, object] = {"role": message.role, "ts": int(time.time())}
        if message.content:
            entry["content"] = message.content
        if message.tool_calls:
            entry["tool_calls"] = [asdict(call) for call in message.tool_calls]
        if message.tool_results:
            entry["tool_results"] = [asdict(result) for result in message.tool_results]
        with self._lock:
            if self._is_first:
                entry["model"] = self._model
                self._is_first = False
            self._write(entry)

    def write_compact_marker(self) -> None:
        with self._lock:
            self._write({"type": "compact", "ts": int(time.time())})

    def append_all(self, messages: list[Message]) -> None:
        for message in messages:
            self.append(message)

    def replace(self, messages: list[Message]) -> None:
        self.write_compact_marker()
        self.append_all(messages)

    def _write(self, entry: dict[str, object]) -> None:
        try:
            self._file.write(
                json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            self._file.flush()
            os.fsync(self._file.fileno())
        except (OSError, ValueError):
            logger.warning("写入会话存档失败: %s", self.path, exc_info=True)

    def close(self) -> None:
        with self._lock:
            if not self._file.closed:
                self._file.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
