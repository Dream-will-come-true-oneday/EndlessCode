"""上下文压缩核心行为测试。"""

from pathlib import Path

import pytest

from endless_code.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    ManageInput,
    RecoveryState,
    TriggerKind,
    estimate_tokens,
    manage_context,
    new_session_context,
    offload_and_snip,
)
from endless_code.compact.layer2 import group_by_user_turn, pick_recent_tail
from endless_code.compact.recovery import build_recovery_attachment
from endless_code.compact.summary_prompt import build_summary_prompt, extract_summary
from endless_code.conversation import Conversation
from endless_code.llm import Message, StreamEvent, ToolDefinition, ToolResult


class SummaryProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self, response: str) -> None:
        self.response = response
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        yield StreamEvent(text=self.response)
        yield StreamEvent(done=True)


def _input(
    tmp_path: Path, conv: Conversation, provider: SummaryProvider
) -> ManageInput:
    return ManageInput(
        conv=conv,
        provider=provider,
        model=provider.model,
        context_window=200_000,
        tool_defs=[
            ToolDefinition(
                name="read_file",
                description="read",
                input_schema={"type": "object"},
            )
        ],
        replacement=ContentReplacementState(),
        recovery=RecoveryState(),
        auto_tracking=CompactCircuitBreaker(),
        session=new_session_context(str(tmp_path)),
        usage_anchor=0,
        anchor_msg_len=0,
        estimated_token=100,
        trigger=TriggerKind.MANUAL,
    )


def test_session_context_and_decision_freeze(tmp_path) -> None:
    context = new_session_context(str(tmp_path))
    assert Path(context.spill_dir).is_dir()
    state = ContentReplacementState()
    assert state.decide_once("id", "full", lambda: ("replaced", "preview")) == "preview"
    assert state.decide_once("id", "full", lambda: ("kept", "")) == "preview"


def test_layer1_offloads_large_result_and_is_stable(tmp_path) -> None:
    state = ContentReplacementState()
    context = new_session_context(str(tmp_path))
    source = Message(
        role="tool",
        tool_results=[ToolResult(tool_call_id="large", content="x" * 60_000)],
    )
    first = offload_and_snip([source], state, context)
    second = offload_and_snip(first, state, context)
    preview = first[0].tool_results[0].content
    assert "original size: 60000 bytes" in preview
    assert "[head preview]" in preview
    assert "[saved to]" in preview
    assert "read_file" in preview
    assert preview == second[0].tool_results[0].content
    assert (Path(context.spill_dir) / "large").stat().st_size == 60_000
    assert source.tool_results[0].content == "x" * 60_000


def test_layer1_limits_aggregate_results(tmp_path) -> None:
    state = ContentReplacementState()
    context = new_session_context(str(tmp_path))
    message = Message(
        role="tool",
        tool_results=[
            ToolResult(tool_call_id=f"id-{index}", content="x" * 80_000)
            for index in range(3)
        ],
    )
    result = offload_and_snip([message], state, context)[0]
    remaining = sum(
        len(item.content.encode("utf-8"))
        for item in result.tool_results
        if "[tool result offloaded" not in item.content
    )
    assert remaining <= 200_000
    assert (
        sum("[tool result offloaded" in item.content for item in result.tool_results)
        >= 2
    )


def test_token_and_recent_tail_keep_tool_pair() -> None:
    messages = [
        Message(role="user", content="u" * 20_000),
        Message(role="assistant", content="", tool_calls=[]),
        Message(role="tool", tool_results=[ToolResult("call", "ok")]),
        Message(role="assistant", content="a" * 20_000),
        Message(role="user", content="next"),
    ]
    assert estimate_tokens(5_000, [Message(role="user", content="x" * 350)], 0) >= 5_100
    tail = pick_recent_tail(messages)
    assert tail[0].role != "tool"
    assert len(tail) >= 5


def test_summary_prompt_and_recovery_are_deterministic() -> None:
    prompt = build_summary_prompt([Message(role="user", content="hello")])
    assert len(prompt) == 1
    assert "<analysis>" in prompt[0].content
    assert "## 9 可能的下一步" in prompt[0].content
    assert extract_summary("draft<summary>kept</summary>tail") == "kept"

    recovery = RecoveryState()
    recovery.record_file("README.md", "content")
    defs = [ToolDefinition("read_file", "read", {"type": "object"})]
    first = build_recovery_attachment(recovery.snapshot(), defs)
    second = build_recovery_attachment(recovery.snapshot(), defs)
    assert first == second
    assert "当前可用工具" in first
    assert '"type": "object"' in first


@pytest.mark.asyncio
async def test_manual_manage_context_replaces_history(tmp_path) -> None:
    conv = Conversation()
    conv.add_user("请总结")
    provider = SummaryProvider(
        "<analysis>draft</analysis><summary>## 1 主要请求和意图\n请总结</summary>"
    )
    input_ = _input(tmp_path, conv, provider)
    result = await manage_context(input_)
    assert result.compacted is True
    assert result.before_tokens == 100
    assert provider.requests[0].tools == []
    assert "历史会话摘要" in conv.messages()[0].content


def test_group_by_user_turn() -> None:
    groups = group_by_user_turn(
        [
            Message(role="user", content="one"),
            Message(role="assistant", content="a"),
            Message(role="tool", tool_results=[]),
            Message(role="user", content="two"),
        ]
    )
    assert [len(group) for group in groups] == [3, 1]
