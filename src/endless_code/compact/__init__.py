"""Endless Code 的会话上下文管理。"""

from endless_code.compact.budget import (
    MIN_CALIBRATED_WINDOW,
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
from endless_code.compact.layer2 import (
    CompactionQualityError,
    leading_compacted_count,
    recent_tail_start,
)
from endless_code.compact.rolling import SummaryState
from endless_code.compact.state import (
    CompactCircuitBreaker,
    ContentReplacementState,
    FileReadRecord,
    RecoveryState,
    SessionContext,
    TrimLedger,
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
from endless_code.compact.trim import apply_local_trim

__all__ = [
    "MIN_CALIBRATED_WINDOW",
    "CompactCircuitBreaker",
    "CompactionQualityError",
    "ContentReplacementState",
    "ContextBudget",
    "FileReadRecord",
    "ManageInput",
    "ManageOutput",
    "RecoveryState",
    "SessionContext",
    "SummaryState",
    "TokenMeter",
    "TriggerKind",
    "TrimLedger",
    "appended_bytes",
    "apply_local_trim",
    "build_context_budget",
    "build_preview",
    "estimate_tokens",
    "estimate_tool_schema_tokens",
    "leading_compacted_count",
    "manage_context",
    "new_session_context",
    "offload_and_snip",
    "open_session_context",
    "parse_session_time",
    "recent_tail_start",
    "spill_single",
    "usage_anchor",
]
