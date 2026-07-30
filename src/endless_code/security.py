"""用户可见文本的密钥脱敏与工具参数摘要。"""

import json
import re
from collections.abc import Collection
from typing import Any

_KEY_TOKEN = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_KEY_ASSIGNMENT = re.compile(
    r"(?i)(\bapi[_-]?key\b\s*[:=]\s*)([^\s,;\"']+|[\"'][^\"']+[\"'])"
)
_PREVIEW_LIMIT = 160


def redact_sensitive(text: Any, secrets: Collection[str] = ()) -> str:
    """隐藏确切密钥和常见 API key 文本形式。"""
    if text is None:
        return ""
    value = str(text)
    unique_secrets = {secret for secret in secrets if isinstance(secret, str) and len(secret) >= 4}
    for secret in sorted(unique_secrets, key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")
    value = _KEY_TOKEN.sub("[REDACTED]", value)
    return _KEY_ASSIGNMENT.sub(r"\1[REDACTED]", value)


def _display_value(value: Any, secrets: Collection[str]) -> str:
    if value is None:
        return ""
    return redact_sensitive(str(value), secrets)


def summarize_tool_args(
    tool_name: str,
    raw_args: str,
    secrets: Collection[str] = (),
) -> str:
    """返回适合界面展示的安全工具参数摘要。"""
    try:
        data = json.loads(raw_args.strip() or "{}")
    except (json.JSONDecodeError, TypeError):
        return redact_sensitive(raw_args, secrets)[:_PREVIEW_LIMIT]

    if not isinstance(data, dict):
        return redact_sensitive(raw_args, secrets)[:_PREVIEW_LIMIT]

    if tool_name == "read_file":
        return f"path={_display_value(data.get('path'), secrets)}"
    if tool_name == "write_file":
        content = data.get("content")
        size = len(content) if isinstance(content, str) else 0
        return f"path={_display_value(data.get('path'), secrets)}, content=<{size} chars>"
    if tool_name == "edit_file":
        old = data.get("old_string")
        new = data.get("new_string")
        old_size = len(old) if isinstance(old, str) else 0
        new_size = len(new) if isinstance(new, str) else 0
        return (
            f"path={_display_value(data.get('path'), secrets)}, "
            f"old=<{old_size} chars>, new=<{new_size} chars>"
        )
    if tool_name == "bash":
        return f"command={_display_value(data.get('command'), secrets)}"[:_PREVIEW_LIMIT]
    if tool_name == "glob":
        pattern = _display_value(data.get("pattern"), secrets)
        path = _display_value(data.get("path") or ".", secrets)
        return f"pattern={pattern}, path={path}"
    if tool_name == "grep":
        pattern = _display_value(data.get("pattern"), secrets)
        path = _display_value(data.get("path") or ".", secrets)
        file_glob = _display_value(data.get("glob") or "*", secrets)
        return f"pattern={pattern}, path={path}, glob={file_glob}"

    keys = ", ".join(sorted(str(key) for key in data))
    return redact_sensitive(f"arguments=<{keys or 'empty'}>", secrets)[:_PREVIEW_LIMIT]
