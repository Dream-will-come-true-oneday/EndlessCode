"""第一层：工具结果落盘与稳定预览。"""

import copy
from pathlib import Path

from endless_code.compact.const import (
    MESSAGE_AGGREGATE_LIMIT,
    PREVIEW_HEAD_BYTES,
    PREVIEW_HEAD_LINES,
    SINGLE_RESULT_LIMIT,
)
from endless_code.compact.state import ContentReplacementState, SessionContext
from endless_code.llm import Message, ToolResult


def spill_single(session: SessionContext, tool_use_id: str, content: str) -> str:
    """以工具调用 id 为文件名保存完整 UTF-8 内容，已存在时不重复写。"""
    path = Path(session.spill_dir) / tool_use_id
    if path.exists():
        return str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(content.encode("utf-8"))
    return str(path)


def _truncate_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def preview_head(content: str) -> str:
    lines = content.splitlines(keepends=True)[:PREVIEW_HEAD_LINES]
    return _truncate_utf8("".join(lines), PREVIEW_HEAD_BYTES)


def build_preview(original_bytes: int, head: str, spill_path: str) -> str:
    """构建在历史中稳定复用的工具结果预览。"""
    return (
        f"[tool result offloaded; original size: {original_bytes} bytes]\n"
        "[head preview]\n"
        f"{head}\n"
        f"[saved to] {spill_path}\n"
        "如需完整内容，请使用 read_file 读取该路径；不要凭头部预览猜测。"
    )


def _replacement_for(
    result: ToolResult,
    state: ContentReplacementState,
    session: SessionContext,
) -> str:
    original = result.content
    size = len(original.encode("utf-8"))

    def decide() -> tuple[str, str]:
        try:
            path = spill_single(session, result.tool_call_id, original)
        except OSError:
            return "skip", ""
        return "replaced", build_preview(size, preview_head(original), path)

    return state.decide_once(result.tool_call_id, original, decide)


def offload_and_snip(
    messages: list[Message],
    state: ContentReplacementState,
    session: SessionContext,
) -> list[Message]:
    """返回处理后的历史副本，不修改传入的消息或工具结果。"""
    output = copy.deepcopy(messages)
    for message in output:
        if message.role != "tool" or not message.tool_results:
            continue

        remaining = 0
        unknown: list[tuple[int, int, ToolResult]] = []
        for index, result in enumerate(message.tool_results):
            seen, replacement = state.existing(result.tool_call_id)
            if seen and replacement is not None:
                result.content = replacement
                continue
            size = len(result.content.encode("utf-8"))
            remaining += size
            if not seen:
                unknown.append((size, index, result))

        for size, _index, result in sorted(
            unknown, key=lambda item: item[0], reverse=True
        ):
            should_replace = (
                size > SINGLE_RESULT_LIMIT or remaining > MESSAGE_AGGREGATE_LIMIT
            )
            if should_replace:
                replacement = _replacement_for(result, state, session)
                seen, stored = state.existing(result.tool_call_id)
                if seen and stored is not None:
                    result.content = replacement
                    remaining -= size
                continue
            state.decide_once(result.tool_call_id, result.content, lambda: ("kept", ""))
    return output
