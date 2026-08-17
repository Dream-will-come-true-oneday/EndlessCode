"""摘要后的恢复附件渲染。"""

import json

from endless_code.compact.const import (
    ESTIMATE_CHARS_PER_TOKEN,
    RECOVERY_FILE_LIMIT,
)
from endless_code.compact.layer1 import _truncate_utf8
from endless_code.compact.limits import BASE_CONTEXT_WINDOW, build_context_limits
from endless_code.compact.state import FileReadRecord
from endless_code.llm import ToolDefinition

BOUNDARY_NOTICE = (
    "需要文件原文、错误原文或用户原话时，必须使用 read_file 重新读取；"
    "不要依赖摘要或预览猜测细节。"
)


def render_file_block(
    record: FileReadRecord, recovery_tokens_per_file: int | None = None
) -> str:
    if recovery_tokens_per_file is None:
        recovery_tokens_per_file = build_context_limits(
            BASE_CONTEXT_WINDOW
        ).recovery_tokens_per_file
    max_bytes = int(recovery_tokens_per_file * ESTIMATE_CHARS_PER_TOKEN)
    content = record.content
    if len(content.encode("utf-8")) > max_bytes:
        content = f"{_truncate_utf8(content, max_bytes)}\n(content truncated)"
    return (
        f"### {record.path}\n"
        f"读取时间: {record.timestamp.isoformat(timespec='seconds')}\n"
        "```text\n"
        f"{content}\n"
        "```"
    )


def build_recovery_attachment(
    snapshot: list[FileReadRecord],
    tool_defs: list[ToolDefinition],
    recovery_tokens_per_file: int | None = None,
) -> str:
    files = "\n\n".join(
        render_file_block(record, recovery_tokens_per_file)
        for record in snapshot[:RECOVERY_FILE_LIMIT]
    )
    if not files:
        files = "（本会话尚未成功读取文件。）"
    tools = "\n".join(
        "- "
        f"{tool.name}: {tool.description}\n"
        f"  schema: {json.dumps(tool.input_schema, ensure_ascii=False, sort_keys=True)}"
        for tool in tool_defs
    )
    if not tools:
        tools = "（当前没有可用工具。）"
    return (
        "## 恢复信息\n\n"
        "### 最近读过的文件\n"
        f"{files}\n\n"
        "### 当前可用工具\n"
        f"{tools}\n\n"
        "### 边界提示\n"
        f"{BOUNDARY_NOTICE}"
    )
