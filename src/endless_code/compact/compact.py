"""上下文管理编排入口。"""

import logging
from dataclasses import dataclass
from enum import Enum

from endless_code.compact.budget import ContextBudget
from endless_code.compact.layer1 import offload_and_snip
from endless_code.compact.layer2 import (
    CompactionQualityError,
    auto_compact,
    force_compact,
)
from endless_code.compact.rolling import SummaryState
from endless_code.compact.state import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    SessionContext,
    TrimLedger,
)
from endless_code.compact.token import estimate_tokens
from endless_code.compact.trim import apply_local_trim
from endless_code.conversation import Conversation
from endless_code.llm import Message, ToolDefinition

logger = logging.getLogger(__name__)


class TriggerKind(Enum):
    AUTO = "auto"
    MANUAL = "manual"
    EMERGENCY = "emergency"


@dataclass
class ManageInput:
    conv: Conversation
    provider: object
    model: str
    context_window: int
    budget: ContextBudget
    tool_defs: list[ToolDefinition]
    replacement: ContentReplacementState
    recovery: RecoveryState
    auto_tracking: CompactCircuitBreaker
    session: SessionContext
    usage_anchor: int
    anchor_msg_len: int
    estimated_token: int
    trigger: TriggerKind
    summary_state: SummaryState | None = None
    trim_ledger: TrimLedger | None = None


@dataclass
class ManageOutput:
    before_tokens: int
    after_tokens: int
    compacted: bool = False
    err: Exception | None = None


async def manage_context(input_: ManageInput) -> ManageOutput:
    """按触发类型执行第一层和/或第二层上下文管理。"""
    before = input_.estimated_token
    budget = input_.budget
    if input_.trigger is TriggerKind.MANUAL:
        messages, _, after = await force_compact(input_)
        input_.conv.replace_history(messages)
        return ManageOutput(before, after, compacted=True)

    layer1 = offload_and_snip(
        input_.conv.messages(), input_.replacement, input_.session, budget
    )
    input_.conv.replace_history(layer1)
    after_layer1 = estimate_tokens(input_.usage_anchor, layer1, input_.anchor_msg_len)

    if input_.trigger is TriggerKind.EMERGENCY:
        input_.estimated_token = before
        messages, _, after = await force_compact(input_)
        input_.conv.replace_history(messages)
        return ManageOutput(before, after, compacted=True)

    if budget.degraded:
        logger.warning(
            "context window %s leaves no normal compaction margin, running degraded",
            budget.context_window,
        )
    threshold = budget.effective_auto_threshold
    if after_layer1 < threshold or input_.auto_tracking.tripped():
        return ManageOutput(before, after_layer1)

    # L1：本地确定性裁剪，零模型调用；压到触发线以下就不再花 LLM。
    trimmed = apply_local_trim(
        layer1,
        input_.recovery,
        budget,
        input_.trim_ledger if input_.trim_ledger is not None else TrimLedger(),
        active=True,
    )
    input_.conv.replace_history(trimmed)
    after_trim = estimate_tokens(input_.usage_anchor, trimmed, input_.anchor_msg_len)
    if after_trim < threshold:
        return ManageOutput(before, after_trim)

    input_.estimated_token = before
    return await _auto_compact_guarded(input_, after_trim)


async def _auto_compact_guarded(input_, fallback_after: int) -> ManageOutput:
    """自动摘要并做质量校验，不达标时收紧近期原文保留量重试一次。"""
    before = input_.estimated_token
    budget = input_.budget
    keep: int | None = None
    min_messages: int | None = None
    problem = ""
    state = input_.summary_state
    for _ in range(2):
        snapshot = state.snapshot() if state is not None else None
        try:
            messages, _, after = await auto_compact(input_, keep, min_messages)
        except Exception as exc:
            logger.info("automatic context compaction failed", exc_info=True)
            return ManageOutput(before, fallback_after, err=exc)
        problem = _quality_problem(messages, after, budget)
        if not problem:
            input_.conv.replace_history(messages)
            return ManageOutput(before, after, compacted=True)
        # 摘要被拒意味着历史没被替换，不能让覆盖点停在未采纳的新摘要上。
        if state is not None and snapshot is not None:
            state.rollback(snapshot)
        keep = max(1, budget.recent_keep_tokens // 2)
        min_messages = 1
    logger.info("context compaction quality check failed: %s", problem)
    input_.auto_tracking.record_failure()
    return ManageOutput(before, fallback_after, err=CompactionQualityError(problem))


def _quality_problem(messages: list[Message], after: int, budget: ContextBudget) -> str:
    """返回压缩结果的质量问题，达标时返回空串。"""
    if not messages:
        return "压缩后历史为空"
    # 降级窗口下 usable 已经贴地，驻留量目标无法达成，只保留结构校验。
    if not budget.degraded and after > budget.compact_target:
        return f"压缩后仍驻留 {after} token，高于期望的 {budget.compact_target} token"
    orphans = _orphan_tool_results(messages)
    if orphans:
        return f"压缩后存在 {orphans} 条无对应调用的工具结果"
    return ""


def _orphan_tool_results(messages: list[Message]) -> int:
    """统计找不到对应工具调用的工具结果条数。"""
    called: set[str] = set()
    orphans = 0
    for message in messages:
        for call in message.tool_calls:
            called.add(call.id)
        for result in message.tool_results:
            if result.tool_call_id not in called:
                orphans += 1
    return orphans
