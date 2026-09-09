"""token 比例自校准计量测试。"""

from endless_code.compact.const import ESTIMATE_CHARS_PER_TOKEN
from endless_code.compact.token import TokenMeter, estimate_tokens
from endless_code.llm import Message


def _message(size: int) -> Message:
    return Message(role="user", content="x" * size)


def test_default_ratio_matches_estimate_tokens() -> None:
    meter = TokenMeter()
    messages = [_message(700)]
    assert meter.chars_per_token == ESTIMATE_CHARS_PER_TOKEN
    assert meter.estimate(0, messages, 0) == estimate_tokens(0, messages, 0)


def test_small_samples_do_not_change_ratio() -> None:
    meter = TokenMeter()
    meter.observe(added_bytes=100, added_tokens=1000)
    meter.observe(added_bytes=10_000, added_tokens=50)
    assert meter.chars_per_token == ESTIMATE_CHARS_PER_TOKEN
    assert meter.samples == 0


def test_out_of_domain_sample_is_ignored() -> None:
    meter = TokenMeter()
    meter.observe(added_bytes=13_000, added_tokens=1_000)
    assert meter.chars_per_token == ESTIMATE_CHARS_PER_TOKEN


def test_dense_cjk_sample_lowers_ratio() -> None:
    meter = TokenMeter()
    meter.observe(added_bytes=1_000, added_tokens=1_000)
    assert meter.chars_per_token == 2.875
    assert meter.samples == 1


def test_repeated_extreme_samples_stay_inside_bounds() -> None:
    meter = TokenMeter()
    for _ in range(20):
        meter.observe(added_bytes=600, added_tokens=600)
    assert meter.chars_per_token == 1.5
    for _ in range(20):
        meter.observe(added_bytes=12_000, added_tokens=1_000)
    assert meter.chars_per_token <= 8.0


def test_reset_returns_to_default_after_history_replacement() -> None:
    meter = TokenMeter()
    for _ in range(5):
        meter.observe(added_bytes=1_000, added_tokens=1_000)
    assert meter.chars_per_token < ESTIMATE_CHARS_PER_TOKEN
    meter.reset()
    assert meter.chars_per_token == ESTIMATE_CHARS_PER_TOKEN
    assert meter.samples == 0


def test_calibrated_ratio_changes_token_estimate() -> None:
    messages = [_message(700)]
    meter = TokenMeter()
    before = meter.estimate(0, messages, 0)
    meter.observe(added_bytes=1_000, added_tokens=1_000)
    after = meter.estimate(0, messages, 0)
    assert after > before
    assert after == estimate_tokens(0, messages, 0, meter.chars_per_token)
