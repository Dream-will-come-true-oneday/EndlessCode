"""上下文管理编排入口。"""

import logging
from dataclasses import dataclass
from enum import Enum

from endless_code.compact.layer1 import offload_and_snip
from endless_code.compact.layer2 import auto_compact, force_compact
from endless_code.compact.limits import ContextLimits, build_context_limits
from endless_code.compact.token import estimate_tokens
from endless_code.llm import ToolDefinition

logger = logging.getLogger(__name__)


class TriggerKind(Enum):
    AUTO = "auto"
    MANUAL = "manual"
    EMERGENCY = "emergency"


@dataclass
class ManageInput:
    conv: object
    provider: object
    model: str
    context_window: int
    tool_defs: list[ToolDefinition]
    replacement: object
    recovery: object
    auto_tracking: object
    session: object
    usage_anchor: int
    anchor_msg_len: int
    estimated_token: int
    trigger: TriggerKind

    @property
    def limits(self) -> ContextLimits:
        return build_context_limits(self.context_window)


@dataclass
class ManageOutput:
    before_tokens: int
    after_tokens: int
    compacted: bool = False
    err: Exception | None = None


async def manage_context(input_: ManageInput) -> ManageOutput:
    """按触发类型执行第一层和/或第二层上下文管理。"""
    before = input_.estimated_token
    limits = input_.limits
    if input_.trigger is TriggerKind.MANUAL:
        messages, _, after = await force_compact(input_)
        input_.conv.replace_history(messages)
        return ManageOutput(before, after, compacted=True)

    layer1 = offload_and_snip(
        input_.conv.messages(), input_.replacement, input_.session, limits
    )
    input_.conv.replace_history(layer1)
    after_layer1 = estimate_tokens(input_.usage_anchor, layer1, input_.anchor_msg_len)

    if input_.trigger is TriggerKind.EMERGENCY:
        input_.estimated_token = before
        messages, _, after = await force_compact(input_)
        input_.conv.replace_history(messages)
        return ManageOutput(before, after, compacted=True)

    if not limits.supports_auto_compaction:
        logger.warning(
            "context_window too small for automatic compaction: %s",
            input_.context_window,
        )
        return ManageOutput(before, after_layer1)

    if after_layer1 < limits.auto_compact_threshold or input_.auto_tracking.tripped():
        return ManageOutput(before, after_layer1)

    input_.estimated_token = before
    try:
        messages, _, after = await auto_compact(input_)
    except Exception as exc:
        logger.info("automatic context compaction failed", exc_info=True)
        return ManageOutput(before, after_layer1, err=exc)
    input_.conv.replace_history(messages)
    return ManageOutput(before, after, compacted=True)
