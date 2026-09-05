"""diff 纯函数：为审批与工具结果生成可着色的 unified 风格 diff。"""

import difflib

BINARY_PLACEHOLDER = "（二进制文件，不展示 diff）"
MAX_DIFF_LINES = 60
MAX_DIFF_CHARS = 8000


def _is_binary(text: str) -> bool:
    return "\x00" in text


def file_diff(path: str, old: str | None, new: str | None, *, context: int = 3) -> str:
    """计算单个文件的 unified 风格 diff。

    old=None 表示新建文件（全部为 + 行）；new=None 表示文件被删除。
    任一侧无法按文本处理（含 \\x00）时返回占位说明。
    """
    if (old is not None and _is_binary(old)) or (new is not None and _is_binary(new)):
        return BINARY_PLACEHOLDER

    old_lines = (old or "").splitlines(keepends=True)
    new_lines = (new or "").splitlines(keepends=True)
    if old_lines and not old_lines[-1].endswith("\n"):
        old_lines[-1] += "\n"
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"

    if old is None:
        if not new_lines:
            return ""
        header = f"新建文件: {path}\n"
        body = "".join(f"+{line}" for line in new_lines)
        return header + body
    if new is None:
        if not old_lines:
            return ""
        header = f"删除文件: {path}\n"
        body = "".join(f"-{line}" for line in old_lines)
        return header + body
    if old == new:
        return ""

    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=context,
    )
    return "".join(diff)


def truncate_diff(
    diff: str, max_lines: int = MAX_DIFF_LINES, max_chars: int = MAX_DIFF_CHARS
) -> str:
    """超限时截断并标注完整规模。"""
    total = len(diff.splitlines())
    truncated = False
    if len(diff) > max_chars:
        diff = diff[:max_chars]
        truncated = True
    lines = diff.splitlines(keepends=True)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True
    result = "".join(lines)
    if truncated:
        if result and not result.endswith("\n"):
            result += "\n"
        result += f"[diff truncated，共 {total} 行]"
    return result


def diff_stat(diff: str) -> str:
    """返回 `+X -Y` 形式的增删行统计（不含 +++/--- 头）。"""
    added = 0
    removed = 0
    for line in diff.splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return f"+{added} -{removed}"
