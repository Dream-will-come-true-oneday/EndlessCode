"""Best-effort, per-session permission audit logging."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Collection
from pathlib import Path
from typing import Self

from endless_code.security import redact_sensitive, summarize_tool_args

logger = logging.getLogger(__name__)

_SUMMARY_LIMIT = 512
_SENSITIVE_ASSIGNMENT = re.compile(
    r'(?i)("?(?:password|passwd|secret|token|authorization)"?\s*[:=]\s*)'
    r'("[^"]*"|\'[^\']*\'|[^,;\s}]+)'
)


def _bounded_summary(value: object, secrets: Collection[str] = ()) -> str:
    """Return a redacted, bounded representation suitable for an audit record."""
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            text = str(value)
    text = redact_sensitive(text, secrets)
    text = _SENSITIVE_ASSIGNMENT.sub(r"\1[REDACTED]", text)
    if len(text) > _SUMMARY_LIMIT:
        return text[:_SUMMARY_LIMIT] + "...[truncated]"
    return text


class AuditWriter:
    """Append-only JSONL audit writer.

    The writer deliberately catches all filesystem/serialization failures. A
    permission log is useful evidence, but it must never prevent a tool call
    from completing.
    """

    def __init__(
        self,
        session_dir: str | os.PathLike[str],
        session_id: str | None = None,
        *,
        secrets: Collection[str] = (),
    ) -> None:
        self.session_dir = Path(session_dir)
        self.session_id = session_id or self.session_dir.name
        self.path = self.session_dir / "permission-audit.jsonl"
        self._secrets = tuple(secrets)
        self._lock = threading.Lock()
        self._file = None
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            self._file = self.path.open("a", encoding="utf-8", newline="\n")
        except (OSError, ValueError):
            logger.warning("打开权限审计文件失败: %s", self.path, exc_info=True)

    def record(
        self,
        event: str,
        *,
        call_id: str = "",
        tool: str = "",
        target: str = "",
        mode: object = "",
        category: object = "",
        read_only: bool = False,
        decision: object = "",
        approval_result: object = "",
        risk: object = "",
        risk_level: object = "",
        rule_source: str = "",
        reason: str = "",
        args: object = None,
        args_summary: str | None = None,
        result: object = None,
        result_summary: str | None = None,
        duration_ms: float | None = None,
        is_error: bool = False,
        timestamp: float | None = None,
        **extra: object,
    ) -> None:
        """Append one normalized event, swallowing write failures."""
        now = time.time() if timestamp is None else timestamp
        record: dict[str, object] = {
            "event": event,
            "session_id": self.session_id,
            "call_id": call_id,
            "timestamp": now,
            "ts": int(now),
            "tool": tool,
            "target": _bounded_summary(target, self._secrets),
            "mode": str(mode),
            "category": _enum_value(category),
            "read_only": bool(read_only),
            "decision": _enum_value(decision),
            "approval_result": _enum_value(approval_result),
            "approval": _enum_value(approval_result),
            "risk": _enum_value(risk_level or risk),
            "risk_level": _enum_value(risk_level or risk),
            "rule_source": rule_source,
            "reason": _bounded_summary(reason, self._secrets),
            "args_summary": (
                _bounded_summary(args_summary, self._secrets)
                if args_summary is not None
                else _bounded_summary(args, self._secrets)
            ),
            "result_summary": (
                _bounded_summary(result_summary, self._secrets)
                if result_summary is not None
                else _bounded_summary(result, self._secrets)
            ),
            "duration_ms": duration_ms,
            "duration": duration_ms,
            "is_error": bool(is_error),
            "error": bool(is_error),
        }
        for key, value in extra.items():
            if key not in record:
                record[key] = _bounded_summary(value, self._secrets)
        self.append(record)

    def append(self, record: dict[str, object]) -> None:
        """Append an already formed event dictionary."""
        try:
            payload = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            with self._lock:
                if self._file is None or self._file.closed:
                    return
                self._file.write(payload + "\n")
                self._file.flush()
                os.fsync(self._file.fileno())
        except (OSError, TypeError, ValueError):
            logger.warning("写入权限审计失败: %s", self.path, exc_info=True)

    def recent(self, limit: int = 50) -> list[dict[str, object]]:
        """Read at most the newest ``limit`` valid records."""
        if limit <= 0:
            return []
        try:
            with self._lock:
                if self._file is not None and not self._file.closed:
                    self._file.flush()
                lines = self.path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            logger.warning("读取权限审计失败: %s", self.path, exc_info=True)
            return []
        records: list[dict[str, object]] = []
        for line in lines[-limit:]:
            try:
                item = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(item, dict):
                records.append(item)
        return records[-limit:]

    def close(self) -> None:
        with self._lock:
            try:
                if self._file is not None and not self._file.closed:
                    self._file.flush()
                    os.fsync(self._file.fileno())
                    self._file.close()
            except (OSError, ValueError):
                logger.warning("关闭权限审计失败: %s", self.path, exc_info=True)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def _enum_value(value: object) -> object:
    if hasattr(value, "name"):
        name = value.name
        if isinstance(name, str):
            return name.lower()
    return getattr(value, "value", value)


def audit_args_summary(tool: str, args: str, secrets: Collection[str] = ()) -> str:
    """Use the existing tool-aware argument summarizer for audit records."""
    return _bounded_summary(summarize_tool_args(tool, args, secrets), secrets)
