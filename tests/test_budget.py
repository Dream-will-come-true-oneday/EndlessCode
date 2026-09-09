"""上下文压缩预算推导测试。"""

import pytest

from endless_code.compact.budget import (
    ContextBudget,
    build_context_budget,
    estimate_tool_schema_tokens,
)
from endless_code.llm import ToolDefinition


def test_thirty_two_thousand_window_uses_floors() -> None:
    budget = build_context_budget(32_000)
    assert budget == ContextBudget(
        context_window=32_000,
        tool_schema_tokens=0,
        output_reserve_tokens=4_096,
        single_result_bytes=8_000,
        message_aggregate_bytes=32_000,
        summary_reserve_tokens=4_000,
        auto_safety_margin_tokens=2_080,
        manual_safety_margin_tokens=1_000,
        recent_keep_tokens=4_000,
        recovery_tokens_per_file=2_000,
    )
    assert budget.usable_window == 27_904
    assert budget.auto_compact_threshold == 21_824
    assert budget.emergency_retry_threshold == 22_904
    assert budget.degraded is False
    assert budget.effective_auto_threshold == 21_824


def test_one_hundred_twenty_eight_thousand_window() -> None:
    budget = build_context_budget(128_000)
    assert (
        budget.single_result_bytes,
        budget.message_aggregate_bytes,
        budget.summary_reserve_tokens,
        budget.auto_safety_margin_tokens,
        budget.manual_safety_margin_tokens,
        budget.recent_keep_tokens,
        budget.recovery_tokens_per_file,
        budget.output_reserve_tokens,
    ) == (32_000, 128_000, 12_800, 8_320, 1_920, 6_400, 3_200, 5_120)
    assert budget.usable_window == 122_880
    assert budget.auto_compact_threshold == 101_760
    assert budget.emergency_retry_threshold == 108_160
    assert budget.compact_target == 73_728
    assert budget.degraded is False


def test_two_hundred_thousand_window_matches_legacy_base_line() -> None:
    budget = build_context_budget(200_000)
    assert (
        budget.single_result_bytes,
        budget.message_aggregate_bytes,
        budget.summary_reserve_tokens,
        budget.auto_safety_margin_tokens,
        budget.manual_safety_margin_tokens,
        budget.recent_keep_tokens,
        budget.recovery_tokens_per_file,
    ) == (50_000, 200_000, 20_000, 13_000, 3_000, 10_000, 5_000)
    assert budget.auto_compact_threshold == 159_000
    assert budget.emergency_retry_threshold == 169_000


def test_one_million_window_reproduces_previous_absolute_constants() -> None:
    """1M 档必须逐值等于改动前 const.py 的绝对常量。"""
    budget = build_context_budget(1_000_000)
    assert budget.single_result_bytes == 250_000
    assert budget.message_aggregate_bytes == 1_000_000
    assert budget.summary_reserve_tokens == 100_000
    assert budget.auto_safety_margin_tokens == 65_000
    assert budget.manual_safety_margin_tokens == 15_000
    assert budget.recent_keep_tokens == 50_000
    assert budget.recovery_tokens_per_file == 25_000
    assert budget.output_reserve_tokens == 40_000
    assert budget.usable_window == 960_000
    assert budget.auto_compact_threshold == 795_000
    assert budget.emergency_retry_threshold == 845_000
    assert budget.compact_target == 576_000


def test_two_million_window_is_capped_by_ceiling() -> None:
    budget = build_context_budget(2_000_000)
    assert budget.single_result_bytes == 250_000
    assert budget.message_aggregate_bytes == 1_000_000
    assert budget.summary_reserve_tokens == 100_000
    assert budget.auto_safety_margin_tokens == 65_000
    assert budget.manual_safety_margin_tokens == 15_000
    assert budget.recent_keep_tokens == 50_000
    assert budget.recovery_tokens_per_file == 25_000
    assert budget.output_reserve_tokens == 40_000
    assert budget.auto_compact_threshold == 1_795_000


def test_tiny_window_degrades_without_disabling_compaction() -> None:
    budget = build_context_budget(1_000)
    assert budget.degraded is True
    assert budget.usable_window == 1
    assert budget.auto_compact_threshold < 0
    assert budget.effective_auto_threshold == 1
    assert budget.effective_emergency_threshold == 1
    assert budget.compact_target == 1


@pytest.mark.parametrize("window", [32_000, 128_000, 200_000, 1_000_000, 2_000_000])
def test_thresholds_stay_ordered_and_positive(window: int) -> None:
    budget = build_context_budget(window)
    assert budget.effective_auto_threshold >= 1
    assert budget.effective_emergency_threshold >= budget.effective_auto_threshold
    assert budget.auto_compact_threshold <= budget.emergency_retry_threshold


def test_tool_schema_tokens_reduce_usable_window() -> None:
    plain = build_context_budget(200_000)
    loaded = build_context_budget(200_000, 20_000)
    assert loaded.usable_window == plain.usable_window - 20_000
    assert loaded.auto_compact_threshold == plain.auto_compact_threshold - 20_000
    assert loaded.tool_schema_tokens == 20_000


def test_negative_tool_schema_tokens_are_normalized_to_zero() -> None:
    assert build_context_budget(200_000, -500).tool_schema_tokens == 0


def test_estimate_tool_schema_tokens_scales_with_definitions() -> None:
    one = estimate_tool_schema_tokens(
        [
            ToolDefinition(
                name="read_file", description="read", input_schema={"type": "object"}
            )
        ]
    )
    assert one == 9
    assert estimate_tool_schema_tokens([]) == 0
    many = estimate_tool_schema_tokens(
        [
            ToolDefinition(
                name=f"tool_{index}",
                description="x" * 200,
                input_schema={
                    "type": "object",
                    "properties": {"a": {"type": "string"}},
                },
            )
            for index in range(10)
        ]
    )
    assert many > one


@pytest.mark.parametrize("window", [0, -1, True, False, 1.5, "200000", None])
def test_invalid_window_raises_value_error(window: object) -> None:
    with pytest.raises(ValueError):
        build_context_budget(window)  # type: ignore[arg-type]
