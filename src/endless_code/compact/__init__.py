"""Endless Code 的会话上下文管理。"""

from endless_code.compact.budget import (
    ContextBudget,
    build_context_budget,
    estimate_tool_schema_tokens,
)
from endless_code.compact.compact import (
    ManageInput,
    ManageOutput,
    TriggerKind,
    manage_context,
)
from endless_code.compact.layer1 import build_preview, offload_and_snip, spill_single
from endless_code.compact.layer2 import CompactionQualityError
from endless_code.compact.state import (
    CompactCircuitBreaker,
    ContentReplacementState,
    FileReadRecord,
    RecoveryState,
    SessionContext,
    new_session_context,
    open_session_context,
    parse_session_time,
)
from endless_code.compact.token import (
    TokenMeter,
    appended_bytes,
    estimate_tokens,
    usage_anchor,
)

__all__ = [
    "CompactCircuitBreaker",
    "CompactionQualityError",
    "ContentReplacementState",
    "ContextBudget",
    "FileReadRecord",
    "ManageInput",
    "ManageOutput",
    "RecoveryState",
    "SessionContext",
    "TokenMeter",
    "TriggerKind",
    "appended_bytes",
    "build_context_budget",
    "build_preview",
    "estimate_tokens",
    "estimate_tool_schema_tokens",
    "manage_context",
    "new_session_context",
    "offload_and_snip",
    "open_session_context",
    "parse_session_time",
    "spill_single",
    "usage_anchor",
]
