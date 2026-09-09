"""基于 Provider usage 锚点的轻量 token 估算与比例自校准。"""

import math

from endless_code.compact.const import ESTIMATE_CHARS_PER_TOKEN
from endless_code.llm import Message, Usage

# 参与校准的最小样本量，样本过小会让比例被噪声带走。
MIN_SAMPLE_BYTES = 500
MIN_SAMPLE_TOKENS = 200
# 单次观测的合法比例域，超出即认为锚点被外部因素污染。
OBSERVED_RATIO_BOUNDS = (1.0, 12.0)
# 校准结果的夹逼区间与平滑系数。
CALIBRATED_RATIO_BOUNDS = (1.5, 8.0)
SMOOTHING_ALPHA = 0.25


def usage_anchor(usage: Usage) -> int:
    """折算「截至该次响应的完整历史 token 量」。

    ``input_tokens + cache_read + cache_write`` 等于本次 prompt 全量，再加
    ``output_tokens`` 覆盖刚落进历史的助手消息。是否该把输出项从锚点里剔除，
    需要先实测各协议 usage 字段语义，此处保持原口径不做臆断。
    """
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


def appended_bytes(messages: list[Message], anchor_msg_len: int) -> int:
    """锚点之后新增消息的字节量，供比例校准取分子。"""
    start = min(max(anchor_msg_len, 0), len(messages))
    return sum(message_bytes(message) for message in messages[start:])


def estimate_tokens(
    anchor: int,
    messages: list[Message],
    anchor_msg_len: int,
    chars_per_token: float = ESTIMATE_CHARS_PER_TOKEN,
) -> int:
    """估算锚点之后新增消息的 token 数。"""
    start = min(max(anchor_msg_len, 0), len(messages))
    added_bytes = sum(message_bytes(message) for message in messages[start:])
    return max(0, int(anchor)) + math.ceil(added_bytes / chars_per_token)


class TokenMeter:
    """用 Provider 回报的真实用量平滑修正 chars/token 比例。

    固定 3.5 对中文和代码的偏差可达一倍，会让压缩时机系统性偏移；这里按每次
    响应的「新增字节 / 新增 token」做 EWMA 校准，并在历史被压缩替换后复位。
    """

    def __init__(self, chars_per_token: float = ESTIMATE_CHARS_PER_TOKEN) -> None:
        self._default = float(chars_per_token)
        self._ratio = float(chars_per_token)
        self.samples = 0

    @property
    def chars_per_token(self) -> float:
        return self._ratio

    def reset(self) -> None:
        """历史结构被压缩改写后，旧样本不再代表新内容，回到默认比例。"""
        self._ratio = self._default
        self.samples = 0

    def observe(self, added_bytes: int, added_tokens: int) -> None:
        """接受一次真实用量样本；不满足门限时静默忽略。"""
        if added_bytes < MIN_SAMPLE_BYTES or added_tokens < MIN_SAMPLE_TOKENS:
            return
        observed = added_bytes / added_tokens
        if not OBSERVED_RATIO_BOUNDS[0] <= observed <= OBSERVED_RATIO_BOUNDS[1]:
            return
        low, high = CALIBRATED_RATIO_BOUNDS
        updated = (1 - SMOOTHING_ALPHA) * self._ratio + SMOOTHING_ALPHA * observed
        self._ratio = min(high, max(low, updated))
        self.samples += 1

    def estimate(
        self, anchor: int, messages: list[Message], anchor_msg_len: int
    ) -> int:
        return estimate_tokens(anchor, messages, anchor_msg_len, self._ratio)
