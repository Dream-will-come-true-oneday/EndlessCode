"""根据上下文窗口计算压缩与工具结果限制。"""

from dataclasses import dataclass

BASE_CONTEXT_WINDOW = 200_000
MAX_TOOL_LIMIT_CONTEXT = BASE_CONTEXT_WINDOW * 2

BASE_SINGLE_RESULT_BYTES = 50_000
BASE_MESSAGE_AGGREGATE_BYTES = 200_000
BASE_SUMMARY_RESERVE_TOKENS = 20_000
BASE_AUTO_SAFETY_MARGIN_TOKENS = 13_000
BASE_MANUAL_SAFETY_MARGIN_TOKENS = 3_000
BASE_RECENT_KEEP_TOKENS = 10_000
BASE_RECOVERY_TOKENS_PER_FILE = 5_000


@dataclass(frozen=True)
class ContextLimits:
    """一个有效上下文窗口对应的全部动态限制。"""

    context_window: int
    single_result_bytes: int
    message_aggregate_bytes: int
    summary_reserve_tokens: int
    auto_safety_margin_tokens: int
    manual_safety_margin_tokens: int
    recent_keep_tokens: int
    recovery_tokens_per_file: int

    @property
    def auto_compact_threshold(self) -> int:
        return (
            self.context_window
            - self.summary_reserve_tokens
            - self.auto_safety_margin_tokens
        )

    @property
    def emergency_retry_threshold(self) -> int:
        return (
            self.context_window
            - self.summary_reserve_tokens
            - self.manual_safety_margin_tokens
        )

    @property
    def supports_auto_compaction(self) -> bool:
        return self.auto_compact_threshold > 0


def _scale_up(base: int, context_window: int) -> int:
    return (base * context_window + BASE_CONTEXT_WINDOW - 1) // BASE_CONTEXT_WINDOW


def build_context_limits(context_window: int) -> ContextLimits:
    """按 200K 基线缩放限制，工具字节限制最多放大两倍。"""
    if (
        not isinstance(context_window, int)
        or isinstance(context_window, bool)
        or context_window <= 0
    ):
        raise ValueError("context_window must be a positive integer")

    tool_scale_window = min(context_window, MAX_TOOL_LIMIT_CONTEXT)
    return ContextLimits(
        context_window=context_window,
        single_result_bytes=_scale_up(BASE_SINGLE_RESULT_BYTES, tool_scale_window),
        message_aggregate_bytes=_scale_up(
            BASE_MESSAGE_AGGREGATE_BYTES, tool_scale_window
        ),
        summary_reserve_tokens=_scale_up(BASE_SUMMARY_RESERVE_TOKENS, context_window),
        auto_safety_margin_tokens=_scale_up(
            BASE_AUTO_SAFETY_MARGIN_TOKENS, context_window
        ),
        manual_safety_margin_tokens=_scale_up(
            BASE_MANUAL_SAFETY_MARGIN_TOKENS, context_window
        ),
        recent_keep_tokens=_scale_up(BASE_RECENT_KEEP_TOKENS, context_window),
        recovery_tokens_per_file=_scale_up(
            BASE_RECOVERY_TOKENS_PER_FILE, context_window
        ),
    )
