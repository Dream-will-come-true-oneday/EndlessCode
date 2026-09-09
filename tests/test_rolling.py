"""滚动摘要状态与增量摘要测试。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from endless_code.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    build_context_budget,
    new_session_context,
)
from endless_code.compact.layer2 import (
    PLACEHOLDER_TEXT,
    auto_compact,
    leading_compacted_count,
    run_rolling_summary,
)
from endless_code.compact.rolling import STATE_FILENAME, SummaryState
from endless_code.compact.summary_prompt import build_summary_prompt
from endless_code.conversation import Conversation
from endless_code.llm import Message, StreamEvent, ToolDefinition


def _write_state(session_dir: Path, payload: str) -> None:
    (session_dir / STATE_FILENAME).write_text(payload, encoding="utf-8")


def test_summary_state_round_trip(tmp_path: Path) -> None:
    state = SummaryState(summary_text="第一版摘要", covered_messages=2, revision=1)
    state.save(str(tmp_path))
    loaded = SummaryState.load(str(tmp_path))
    assert loaded == state


def test_summary_state_load_missing_returns_fresh(tmp_path: Path) -> None:
    loaded = SummaryState.load(str(tmp_path))
    assert loaded == SummaryState()


def test_summary_state_load_corrupt_returns_fresh(tmp_path: Path) -> None:
    _write_state(tmp_path, "{not-json")
    assert SummaryState.load(str(tmp_path)) == SummaryState()

    _write_state(tmp_path, '{"version": 1, "summary_text": 3, "covered_messages": "x"}')
    assert SummaryState.load(str(tmp_path)) == SummaryState()

    _write_state(tmp_path, '{"version": 2, "summary_text": "s", "covered_messages": 1}')
    assert SummaryState.load(str(tmp_path)) == SummaryState()

    _write_state(tmp_path, '[{"version": 1}]')
    assert SummaryState.load(str(tmp_path)) == SummaryState()


def test_summary_state_load_rejects_bool_revision(tmp_path: Path) -> None:
    _write_state(
        tmp_path,
        '{"version": 1, "summary_text": "s", "covered_messages": 1, "revision": true}',
    )
    assert SummaryState.load(str(tmp_path)) == SummaryState()


def test_usable_for_rolling_boundaries() -> None:
    fresh = SummaryState()
    assert fresh.usable_for_rolling(10) is False  # 无正文

    state = SummaryState(summary_text="摘要", covered_messages=0)
    assert state.usable_for_rolling(10) is False  # 覆盖点在开头，等于全量

    state = SummaryState(summary_text="摘要", covered_messages=10)
    assert state.usable_for_rolling(10) is False  # 覆盖点越界

    state = SummaryState(summary_text="摘要", covered_messages=2)
    assert state.usable_for_rolling(10) is True
    assert state.usable_for_rolling(2) is False  # 无新片段


def test_rolling_update_increments_revision_and_clamps_covered() -> None:
    state = SummaryState()
    state.rolling_update("第一版", 2)
    assert (state.summary_text, state.covered_messages, state.revision) == (
        "第一版",
        2,
        1,
    )
    state.rolling_update("第二版", -5)
    assert (state.summary_text, state.covered_messages, state.revision) == (
        "第二版",
        0,
        2,
    )


class RollingProvider:
    name = "roll-fake"
    model = "roll-model"

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.requests: list = []

    async def stream(self, request):
        self.requests.append(request)
        text = self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]
        yield StreamEvent(text=text)
        yield StreamEvent(done=True)


def _make_input(tmp_path: Path, conv: Conversation, provider: RollingProvider, state):
    return SimpleNamespace(
        conv=conv,
        provider=provider,
        model=provider.model,
        context_window=1_000_000,
        budget=build_context_budget(1_000_000),
        tool_defs=[
            ToolDefinition(
                name="read_file", description="read", input_schema={"type": "object"}
            )
        ],
        replacement=ContentReplacementState(),
        recovery=RecoveryState(),
        auto_tracking=CompactCircuitBreaker(),
        session=new_session_context(str(tmp_path)),
        usage_anchor=0,
        anchor_msg_len=0,
        estimated_token=100,
        trigger=None,
        summary_state=state,
        trim_ledger=None,
    )


def test_leading_compacted_count_three_shapes() -> None:
    summary = Message(role="user", content="## 历史会话摘要\n第一版\n\n恢复信息")
    assert leading_compacted_count([]) == 0
    assert leading_compacted_count([summary]) == 1
    assert (
        leading_compacted_count(
            [summary, Message(role="assistant", content=PLACEHOLDER_TEXT)]
        )
        == 2
    )
    messages = [
        summary,
        Message(role="assistant", content=PLACEHOLDER_TEXT),
        Message(role="user", content="新消息"),
    ]
    assert leading_compacted_count(messages) == 2
    assert (
        leading_compacted_count([summary, Message(role="assistant", content="ok")]) == 1
    )
    assert leading_compacted_count([Message(role="user", content="普通消息")]) == 0


def test_merge_prompt_injects_previous_summary() -> None:
    plain = build_summary_prompt([Message(role="user", content="hi")])
    assert "上一版摘要" not in plain[0].content

    merged = build_summary_prompt([Message(role="user", content="hi")], "旧摘要正文")
    assert "上一版摘要（滚动合并）" in merged[0].content
    assert "旧摘要正文" in merged[0].content
    assert "## 9 可能的下一步" in merged[0].content  # 九节契约不变


@pytest.mark.asyncio
async def test_rolling_summary_only_covers_new_segment(tmp_path: Path) -> None:
    provider = RollingProvider(["<summary>## 1 主要请求和意图\n新版摘要</summary>"])
    state = SummaryState(
        summary_text="STATE_MARKER_旧摘要正文", covered_messages=2, revision=1
    )
    conv = Conversation()
    conv.add_user("## 历史会话摘要\nHISTORY_MARKER_旧摘要正文\n\n恢复信息")
    conv.add_assistant(PLACEHOLDER_TEXT)
    conv.add_user("SEGMENT_MARKER_新增消息")

    input_ = _make_input(tmp_path, conv, provider, state)
    messages, summary_text = await run_rolling_summary(input_)

    assert summary_text == "## 1 主要请求和意图\n新版摘要"
    prompt = provider.requests[0].messages[0].content
    assert "STATE_MARKER_旧摘要正文" in prompt  # 上一版摘要走注入段
    assert "HISTORY_MARKER_旧摘要正文" not in prompt  # 已覆盖消息不再进 conversation
    assert "SEGMENT_MARKER_新增消息" in prompt
    assert messages[0].content.startswith("## 历史会话摘要")
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_auto_compact_builds_state_then_rolls_incrementally(
    tmp_path: Path,
) -> None:
    provider = RollingProvider(
        [
            "<summary>## 1 主要请求和意图\n第一版摘要内容</summary>",
            "<summary>## 1 主要请求和意图\n第二版摘要内容</summary>",
        ]
    )
    state = SummaryState()
    conv = Conversation()
    for index in range(1, 9):
        conv.add_user(f"U{index}-" + "x" * 140_000)
    input_ = _make_input(tmp_path, conv, provider, state)

    first = await auto_compact(input_)
    assert state.revision == 1
    assert state.covered_messages == 2  # 摘要 + 占位
    conv.replace_history(first[0])

    conv.add_user("NINE_MARKER_" + "y" * 140_000)
    await auto_compact(input_)

    assert state.revision == 2
    assert len(provider.requests) == 2
    second_prompt = provider.requests[1].messages[0].content
    assert "上一版摘要（滚动合并）" in second_prompt
    assert "第一版摘要内容" in second_prompt  # 注入的上一版正文
    serialized = second_prompt.split("[conversation]")[-1]
    assert '"U1-' not in serialized and '"U2-' not in serialized
    assert '"U3-' not in serialized  # 已被首版摘要覆盖且不在近期原文内
    assert '"U4-' in serialized and '"NINE_MARKER_' in serialized
