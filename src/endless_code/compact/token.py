"""基于 Provider usage 锚点的轻量 token 估算。"""

import math

from endless_code.compact.const import ESTIMATE_CHARS_PER_TOKEN
from endless_code.llm import Message, Usage


def usage_anchor(usage: Usage) -> int:
    return int(
        usage.input_tokens + usage.output_tokens + usage.cache_read + usage.cache_write
    )


def message_bytes(message: Message) -> int:
    """计算协议无关消息中会进入请求体的 UTF-8 字节数。"""
    parts = [message.role, message.content]
    for call in message.tool_calls:
        parts.extend((call.id, call.name, call.input))
    for result in message.tool_results:
        parts.extend((result.tool_call_id, result.content))
    return sum(len(part.encode("utf-8")) for part in parts)


def estimate_tokens(
    anchor: int,
    messages: list[Message],
    anchor_msg_len: int,
) -> int:
    """估算锚点之后新增消息的 token 数。"""
    start = min(max(anchor_msg_len, 0), len(messages))
    added_bytes = sum(message_bytes(message) for message in messages[start:])
    return max(0, int(anchor)) + math.ceil(added_bytes / ESTIMATE_CHARS_PER_TOKEN)
