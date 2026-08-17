import pytest

from endless_code.compact.limits import ContextLimits, build_context_limits


def test_default_window_limits() -> None:
    limits = build_context_limits(200_000)
    assert limits == ContextLimits(
        context_window=200_000,
        single_result_bytes=50_000,
        message_aggregate_bytes=200_000,
        summary_reserve_tokens=20_000,
        auto_safety_margin_tokens=13_000,
        manual_safety_margin_tokens=3_000,
        recent_keep_tokens=10_000,
        recovery_tokens_per_file=5_000,
    )
    assert limits.auto_compact_threshold == 167_000
    assert limits.emergency_retry_threshold == 177_000
    assert limits.supports_auto_compaction is True


def test_one_million_limits() -> None:
    limits = build_context_limits(1_000_000)
    assert limits.single_result_bytes == 100_000
    assert limits.message_aggregate_bytes == 400_000
    assert limits.summary_reserve_tokens == 100_000
    assert limits.auto_safety_margin_tokens == 65_000
    assert limits.manual_safety_margin_tokens == 15_000
    assert limits.recent_keep_tokens == 50_000
    assert limits.recovery_tokens_per_file == 25_000
    assert limits.auto_compact_threshold == 835_000
    assert limits.emergency_retry_threshold == 885_000


@pytest.mark.parametrize(
    ("context_window", "single", "aggregate", "summary", "auto", "manual"),
    [
        (128_000, 32_000, 128_000, 12_800, 8_320, 1_920),
        (300_000, 75_000, 300_000, 30_000, 19_500, 4_500),
        (512_000, 100_000, 400_000, 51_200, 33_280, 7_680),
        (2_000_000, 100_000, 400_000, 200_000, 130_000, 30_000),
    ],
)
def test_limits_scale_and_tool_limits_cap(
    context_window: int,
    single: int,
    aggregate: int,
    summary: int,
    auto: int,
    manual: int,
) -> None:
    limits = build_context_limits(context_window)
    assert limits.single_result_bytes == single
    assert limits.message_aggregate_bytes == aggregate
    assert limits.summary_reserve_tokens == summary
    assert limits.auto_safety_margin_tokens == auto
    assert limits.manual_safety_margin_tokens == manual


def test_limits_round_up_without_floating_point() -> None:
    limits = build_context_limits(200_001)
    assert limits.single_result_bytes == 50_001
    assert limits.message_aggregate_bytes == 200_001
    assert limits.summary_reserve_tokens == 20_001
    assert limits.auto_safety_margin_tokens == 13_001
    assert limits.manual_safety_margin_tokens == 3_001
    assert limits.recent_keep_tokens == 10_001
    assert limits.recovery_tokens_per_file == 5_001


@pytest.mark.parametrize("context_window", [0, -1, True, 1.5, "200000"])
def test_limits_reject_non_positive_or_non_integer_window(context_window) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        build_context_limits(context_window)
