"""按配置窗口推导上下文压缩预算。"""

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass

from endless_code.compact.const import ESTIMATE_CHARS_PER_TOKEN
from endless_code.llm import ToolDefinition

# 比例按 1M 窗口校准，ceiling 沿用历史绝对常量，floor 保证 32K 级窗口仍有余量。
_SINGLE_RESULT_BYTES = (0.250, 8_000, 250_000)
_MESSAGE_AGGREGATE_BYTES = (1.000, 32_000, 1_000_000)
_SUMMARY_RESERVE_TOKENS = (0.100, 4_000, 100_000)
_AUTO_SAFETY_MARGIN_TOKENS = (0.065, 2_000, 65_000)
_MANUAL_SAFETY_MARGIN_TOKENS = (0.015, 1_000, 15_000)
_RECENT_KEEP_TOKENS = (0.050, 4_000, 50_000)
_RECOVERY_TOKENS_PER_FILE = (0.025, 2_000, 25_000)
_OUTPUT_RESERVE_TOKENS = (0.040, 4_096, 40_000)

# 压缩后的期望驻留量，供质量校验判定「摘得够不够」。
COMPACT_TARGET_RATIO = 0.6

# 窗口自校准的下限：防御异常小的估算把窗口打到不可用。
MIN_CALIBRATED_WINDOW = 16_000


@dataclass(frozen=True)
class ContextBudget:
    """一个有效上下文窗口对应的全部压缩量纲与派火线。"""

    context_window: int
    tool_schema_tokens: int
    output_reserve_tokens: int
    single_result_bytes: int
    message_aggregate_bytes: int
    summary_reserve_tokens: int
    auto_safety_margin_tokens: int
    manual_safety_margin_tokens: int
    recent_keep_tokens: int
    recovery_tokens_per_file: int

    @property
    def usable_window(self) -> int:
        """扣掉本轮输出与工具定义开销后，历史真正能占用的窗口。"""
        return max(
            1,
            self.context_window - self.output_reserve_tokens - self.tool_schema_tokens,
        )

    @property
    def auto_compact_threshold(self) -> int:
        return (
            self.usable_window
            - self.summary_reserve_tokens
            - self.auto_safety_margin_tokens
        )

    @property
    def emergency_retry_threshold(self) -> int:
        return (
            self.usable_window
            - self.summary_reserve_tokens
            - self.manual_safety_margin_tokens
        )

    @property
    def degraded(self) -> bool:
        """窗口小到容纳不下正常压缩余量，需要走降级留量。"""
        return self.auto_compact_threshold <= 0

    @property
    def effective_auto_threshold(self) -> int:
        """自动压缩触发线；降级时退化为可用窗口的一半而不是关闭。"""
        if self.auto_compact_threshold > 0:
            return self.auto_compact_threshold
        return max(1, self.usable_window // 2)

    @property
    def effective_emergency_threshold(self) -> int:
        """紧急压缩后的重试线；降级时退化为「不超过约用窗口本身」。"""
        if self.emergency_retry_threshold > 0:
            return self.emergency_retry_threshold
        return max(1, self.usable_window)

    @property
    def compact_target(self) -> int:
        """压缩成功的期望驻留上限。"""
        return max(1, int(self.usable_window * COMPACT_TARGET_RATIO))


def _scaled(window: int, spec: tuple[float, int, int]) -> int:
    ratio, low, high = spec
    return max(low, min(high, int(window * ratio)))


def build_context_budget(
    context_window: int, tool_schema_tokens: int = 0
) -> ContextBudget:
    """按比例表把窗口换算成压缩量纲；窗口必须是不小的正整数。"""
    if (
        not isinstance(context_window, int)
        or isinstance(context_window, bool)
        or context_window <= 0
    ):
        raise ValueError("context_window must be a positive integer")
    schema_tokens = max(0, tool_schema_tokens)
    return ContextBudget(
        context_window=context_window,
        tool_schema_tokens=schema_tokens,
        output_reserve_tokens=_scaled(context_window, _OUTPUT_RESERVE_TOKENS),
        single_result_bytes=_scaled(context_window, _SINGLE_RESULT_BYTES),
        message_aggregate_bytes=_scaled(context_window, _MESSAGE_AGGREGATE_BYTES),
        summary_reserve_tokens=_scaled(context_window, _SUMMARY_RESERVE_TOKENS),
        auto_safety_margin_tokens=_scaled(context_window, _AUTO_SAFETY_MARGIN_TOKENS),
        manual_safety_margin_tokens=_scaled(
            context_window, _MANUAL_SAFETY_MARGIN_TOKENS
        ),
        recent_keep_tokens=_scaled(context_window, _RECENT_KEEP_TOKENS),
        recovery_tokens_per_file=_scaled(context_window, _RECOVERY_TOKENS_PER_FILE),
    )


def estimate_tool_schema_tokens(tool_defs: Sequence[ToolDefinition]) -> int:
    """把工具定义折算成 token，用于从窗口里预先扣掉固定开销。"""
    total = 0
    for tool in tool_defs:
        schema = json.dumps(tool.input_schema, ensure_ascii=False, sort_keys=True)
        total += len(f"{tool.name}{tool.description}{schema}".encode())
    return math.ceil(total / ESTIMATE_CHARS_PER_TOKEN)
