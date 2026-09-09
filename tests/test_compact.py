"""上下文压缩核心行为测试。"""

from pathlib import Path

import pytest

from endless_code.compact import (
    CompactCircuitBreaker,
    CompactionQualityError,
    ContentReplacementState,
    ManageInput,
    RecoveryState,
    SummaryState,
    TriggerKind,
    TrimLedger,
    build_context_budget,
    estimate_tokens,
    manage_context,
    new_session_context,
    offload_and_snip,
)
from endless_code.compact.compact import _quality_problem
from endless_code.compact.layer2 import group_by_user_turn, pick_recent_tail
from endless_code.compact.recovery import build_recovery_attachment
from endless_code.compact.summary_prompt import build_summary_prompt, extract_summary
from endless_code.compact.trim import SUPERSEDED_PREFIX
from endless_code.conversation import Conversation
from endless_code.llm import (
    Message,
    StreamEvent,
    ToolCall,
    ToolDefinition,
    ToolResult,
)


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
    tmp_path: Path,
    conv: Conversation,
    provider: SummaryProvider,
    context_window: int = 200_000,
    trigger: TriggerKind = TriggerKind.MANUAL,
    estimated_token: int = 100,
    recovery: RecoveryState | None = None,
    summary_state: SummaryState | None = None,
    trim_ledger: TrimLedger | None = None,
) -> ManageInput:
    return ManageInput(
        conv=conv,
        provider=provider,
        model=provider.model,
        context_window=context_window,
        budget=build_context_budget(context_window),
        tool_defs=[
            ToolDefinition(
                name="read_file",
                description="read",
                input_schema={"type": "object"},
            )
        ],
        replacement=ContentReplacementState(),
        recovery=recovery if recovery is not None else RecoveryState(),
        auto_tracking=CompactCircuitBreaker(),
        session=new_session_context(str(tmp_path)),
        usage_anchor=0,
        anchor_msg_len=0,
        estimated_token=estimated_token,
        trigger=trigger,
        summary_state=summary_state,
        trim_ledger=trim_ledger,
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
    budget = build_context_budget(1_000_000)
    source = Message(
        role="tool",
        tool_results=[ToolResult(tool_call_id="large", content="x" * 260_000)],
    )
    first = offload_and_snip([source], state, context, budget)
    second = offload_and_snip(first, state, context, budget)
    preview = first[0].tool_results[0].content
    assert "original size: 260000 bytes" in preview
    assert "[head preview]" in preview
    assert "[saved to]" in preview
    assert "read_file" in preview
    assert preview == second[0].tool_results[0].content
    assert (Path(context.spill_dir) / "large").stat().st_size == 260_000
    assert source.tool_results[0].content == "x" * 260_000


def test_layer1_aggregate_limit_is_dynamic(tmp_path) -> None:
    state = ContentReplacementState()
    context = new_session_context(str(tmp_path))
    budget = build_context_budget(1_000_000)
    message = Message(
        role="tool",
        tool_results=[
            ToolResult(tool_call_id=f"id-{index}", content="x" * 300_000)
            for index in range(5)
        ],
    )
    result = offload_and_snip([message], state, context, budget)[0]
    remaining = sum(
        len(item.content.encode("utf-8"))
        for item in result.tool_results
        if "[tool result offloaded" not in item.content
    )
    assert remaining <= 1_000_000
    assert (
        sum("[tool result offloaded" in item.content for item in result.tool_results)
        >= 2
    )


def test_layer1_small_window_offloads_what_1m_window_keeps(tmp_path) -> None:
    """40KB 结果在 128k 窗口下必须落盘，在 1M 窗口下保留原文。"""
    content = "x" * 40_000
    result = Message(role="tool", tool_results=[ToolResult("call", content)])

    small = offload_and_snip(
        [result],
        ContentReplacementState(),
        new_session_context(str(tmp_path)),
        build_context_budget(128_000),
    )[0]
    assert "[tool result offloaded" in small.tool_results[0].content

    large = offload_and_snip(
        [result],
        ContentReplacementState(),
        new_session_context(str(tmp_path)),
        build_context_budget(1_000_000),
    )[0]
    assert large.tool_results[0].content == content


def test_token_and_recent_tail_keep_tool_pair() -> None:
    messages = [
        Message(role="user", content="u" * 20_000),
        Message(role="assistant", content="", tool_calls=[]),
        Message(role="tool", tool_results=[ToolResult("call", "ok")]),
        Message(role="assistant", content="a" * 20_000),
        Message(role="user", content="next"),
    ]
    assert estimate_tokens(5_000, [Message(role="user", content="x" * 350)], 0) >= 5_100
    tail = pick_recent_tail(messages, build_context_budget(1_000_000))
    assert tail[0].role != "tool"
    assert len(tail) >= 5
    tight = pick_recent_tail(
        messages, build_context_budget(32_000), max_tokens=1, min_messages=1
    )
    assert len(tight) < len(messages)


def test_summary_prompt_and_recovery_are_deterministic() -> None:
    prompt = build_summary_prompt([Message(role="user", content="hello")])
    assert len(prompt) == 1
    assert "<analysis>" in prompt[0].content
    assert "## 9 可能的下一步" in prompt[0].content
    assert extract_summary("draft<summary>kept</summary>tail") == "kept"

    recovery = RecoveryState()
    recovery.record_file("README.md", "content")
    defs = [ToolDefinition("read_file", "read", {"type": "object"})]
    budget = build_context_budget(1_000_000)
    first = build_recovery_attachment(recovery.snapshot(), defs, budget)
    second = build_recovery_attachment(recovery.snapshot(), defs, budget)
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


@pytest.mark.asyncio
async def test_small_window_auto_compaction_triggers_and_tightens_tail(
    tmp_path,
) -> None:
    """128k 窗口下自动压缩必须真跑；固定阈值时代这里永不压缩。"""
    conv = Conversation()
    for _ in range(10):
        conv.add_user("x" * 140_000)
    provider = SummaryProvider(
        "<summary>## 1 主要请求和意图\n延续既有任务并保留全部用户诉求。</summary>"
    )
    input_ = _input(
        tmp_path,
        conv,
        provider,
        context_window=128_000,
        trigger=TriggerKind.AUTO,
        estimated_token=400_000,
    )
    result = await manage_context(input_)
    assert result.compacted is True
    assert result.err is None
    assert result.after_tokens < input_.budget.compact_target
    assert "历史会话摘要" in conv.messages()[0].content
    # 首轮尾部保留量超标 → 收紧后重试一次。
    assert len(provider.requests) == 2


@pytest.mark.asyncio
async def test_quality_gate_failure_keeps_history_and_reports_error(tmp_path) -> None:
    conv = Conversation()
    conv.add_user("x" * 2_000_000)
    provider = SummaryProvider("<summary>## 1 主要请求和意图\n巨型上下文</summary>")
    state = SummaryState("旧版正文", 2, 3)
    input_ = _input(
        tmp_path,
        conv,
        provider,
        context_window=128_000,
        trigger=TriggerKind.AUTO,
        estimated_token=600_000,
        summary_state=state,
    )
    result = await manage_context(input_)
    assert result.compacted is False
    assert isinstance(result.err, CompactionQualityError)
    assert len(conv.messages()) == 1
    assert conv.messages()[0].content.startswith("x")
    # 摘要被拒 → 历史没换，滚动状态的覆盖点与轮次也不能前进
    assert state.snapshot() == ("旧版正文", 2, 3)


@pytest.mark.asyncio
async def test_degraded_window_still_compacts(tmp_path) -> None:
    conv = Conversation()
    conv.add_user("请总结")
    provider = SummaryProvider("<summary>## 1 主要请求和意图\n请总结</summary>")
    input_ = _input(
        tmp_path,
        conv,
        provider,
        context_window=1_000,
        trigger=TriggerKind.AUTO,
        estimated_token=10,
    )
    assert input_.budget.degraded is True
    result = await manage_context(input_)
    assert result.compacted is True
    assert "历史会话摘要" in conv.messages()[0].content


@pytest.mark.asyncio
async def test_blank_summary_keeps_history(tmp_path) -> None:
    """模型只返空标记时不能拿它替换历史。"""
    conv = Conversation()
    for _ in range(10):
        conv.add_user("x" * 40_000)
    provider = SummaryProvider("<summary>   </summary>")
    input_ = _input(
        tmp_path,
        conv,
        provider,
        context_window=128_000,
        trigger=TriggerKind.AUTO,
        estimated_token=120_000,
    )
    result = await manage_context(input_)
    assert result.compacted is False
    assert isinstance(result.err, CompactionQualityError)
    assert len(conv.messages()) == 10
    assert input_.auto_tracking.tripped() is False


def test_quality_problem_detects_orphan_tool_results() -> None:
    budget = build_context_budget(1_000_000)
    orphans = [
        Message(role="user", content="摘要"),
        Message(role="tool", tool_results=[ToolResult("dangling", "ok")]),
    ]
    assert "无对应调用的工具结果" in _quality_problem(orphans, 10, budget)
    paired = [
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="c1", name="read_file", input="{}")],
        ),
        Message(role="tool", tool_results=[ToolResult("c1", "ok")]),
    ]
    assert _quality_problem(paired, 10, budget) == ""
    assert _quality_problem([], 10, budget) == "压缩后历史为空"


@pytest.mark.asyncio
async def test_l1_trims_stale_reads_and_skips_summary(tmp_path) -> None:
    """L1 本地裁剪零 LLM 调用，压到触发线以下后不再摘要（AC3）。"""
    recovery = RecoveryState()
    for index in range(1, 25):
        recovery.record_read(f"c{index}", str(tmp_path / "a.txt"))
    conv = Conversation()
    conv.add_tool_results(
        [
            ToolResult(tool_call_id=f"c{index}", content=f"READ{index}-" + "x" * 5_000)
            for index in range(1, 25)
        ]
    )
    for _ in range(5):
        conv.add_user("u" * 5_000)
    provider = SummaryProvider("<summary>不应被调用</summary>")
    state = SummaryState()
    ledger = TrimLedger()
    input_ = _input(
        tmp_path,
        conv,
        provider,
        context_window=32_000,
        trigger=TriggerKind.AUTO,
        estimated_token=200_000,
        recovery=recovery,
        summary_state=state,
        trim_ledger=ledger,
    )

    result = await manage_context(input_)

    assert len(provider.requests) == 0  # 全程零模型调用
    assert result.compacted is False
    results = conv.messages()[0].tool_results
    superseded = [r for r in results if r.content.startswith(SUPERSEDED_PREFIX)]
    assert len(superseded) == 23  # c24 是最新读取，保留原文
    # c24 已被 L0 落盘成预览，F1 优先于 F2：预览正文（含原文片段）不被削成纯指针
    assert not results[-1].content.startswith(SUPERSEDED_PREFIX)
    assert "READ24-" in results[-1].content
    assert result.after_tokens < input_.budget.effective_auto_threshold
    assert ledger.pointer_for("c1") is not None


@pytest.mark.asyncio
async def test_manual_compact_keeps_full_semantics(tmp_path) -> None:
    """手动 /compact 不触发 L1、不写滚动状态，仍为无条件全量重摘（F7/AC7）。"""
    recovery = RecoveryState()
    recovery.record_read("c1", str(tmp_path / "a.txt"))
    conv = Conversation()
    conv.add_tool_results([ToolResult(tool_call_id="c1", content="x" * 5_000)])
    conv.add_user("请总结")
    provider = SummaryProvider("<summary>## 1 主要请求和意图\n请总结</summary>")
    state = SummaryState()
    ledger = TrimLedger()
    input_ = _input(
        tmp_path,
        conv,
        provider,
        context_window=32_000,
        trigger=TriggerKind.MANUAL,
        recovery=recovery,
        summary_state=state,
        trim_ledger=ledger,
    )

    result = await manage_context(input_)

    assert result.compacted is True
    assert len(provider.requests) == 1  # 全量摘要照常发生
    assert ledger.pointer_for("c1") is None  # 不做本地裁剪
    assert state.revision == 0  # 不写滚动状态
    assert conv.messages()[0].content.startswith("## 历史会话摘要")


@pytest.mark.asyncio
async def test_below_threshold_skips_l1_and_summary(tmp_path) -> None:
    conv = Conversation()
    conv.add_user("小消息")
    provider = SummaryProvider("<summary>不应被调用</summary>")
    input_ = _input(
        tmp_path,
        conv,
        provider,
        context_window=128_000,
        trigger=TriggerKind.AUTO,
        estimated_token=10,
    )

    result = await manage_context(input_)

    assert len(provider.requests) == 0
    assert result.compacted is False
