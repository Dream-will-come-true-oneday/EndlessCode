"""上下文压缩核心行为测试。"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

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
    parse_session_time,
    spill_single,
    usage_anchor,
)
from endless_code.compact.const import (
    PREVIEW_HEAD_BYTES,
    PREVIEW_HEAD_LINES,
    RECOVERY_FILE_LIMIT,
)
from endless_code.compact.layer1 import _replacement_for, _truncate_utf8, preview_head
from endless_code.compact.layer2 import (
    _join_after_summary,
    _provider_stream,
    group_by_user_turn,
    pick_recent_tail,
    ptl_retry,
)
from endless_code.compact.limits import build_context_limits
from endless_code.compact.recovery import build_recovery_attachment, render_file_block
from endless_code.compact.state import FileReadRecord
from endless_code.compact.summary_prompt import (
    build_summary_prompt,
    extract_summary,
    serialize_conversation,
)
from endless_code.compact.token import message_bytes
from endless_code.conversation import Conversation
from endless_code.llm import (
    Message,
    PromptTooLongError,
    Request,
    StreamEvent,
    ToolCall,
    ToolDefinition,
    ToolResult,
    Usage,
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
        tool_results=[ToolResult(tool_call_id="large", content="x" * 260_000)],
    )
    first = offload_and_snip([source], state, context)
    second = offload_and_snip(first, state, context)
    preview = first[0].tool_results[0].content
    assert "original size: 260000 bytes" in preview
    assert "[head preview]" in preview
    assert "[saved to]" in preview
    assert "read_file" in preview
    assert preview == second[0].tool_results[0].content
    assert (Path(context.spill_dir) / "large").stat().st_size == 260_000
    assert source.tool_results[0].content == "x" * 260_000


def test_layer1_limits_aggregate_results(tmp_path) -> None:
    state = ContentReplacementState()
    context = new_session_context(str(tmp_path))
    message = Message(
        role="tool",
        tool_results=[
            ToolResult(tool_call_id=f"id-{index}", content="x" * 300_000)
            for index in range(5)
        ],
    )
    result = offload_and_snip([message], state, context)[0]
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


def test_layer1_1m_limits_and_tool_cap(tmp_path) -> None:
    state = ContentReplacementState()
    context = new_session_context(str(tmp_path))
    limits = build_context_limits(1_000_000)
    message = Message(
        role="tool",
        tool_results=[
            ToolResult(tool_call_id="kept", content="x" * 100_000),
            ToolResult(tool_call_id="spilled", content="y" * 100_001),
        ],
    )

    result = offload_and_snip([message], state, context, limits)[0]

    assert "[tool result offloaded" not in result.tool_results[0].content
    assert "[tool result offloaded" in result.tool_results[1].content


def test_layer1_1m_aggregate_limit_is_400k(tmp_path) -> None:
    state = ContentReplacementState()
    context = new_session_context(str(tmp_path))
    limits = build_context_limits(1_000_000)
    message = Message(
        role="tool",
        tool_results=[
            ToolResult(tool_call_id=f"id-{index}", content="x" * 100_000)
            for index in range(5)
        ],
    )

    result = offload_and_snip([message], state, context, limits)[0]
    remaining = sum(
        len(item.content.encode("utf-8"))
        for item in result.tool_results
        if "[tool result offloaded" not in item.content
    )

    assert remaining <= 400_000
    assert (
        sum("[tool result offloaded" in item.content for item in result.tool_results)
        == 1
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


def test_usage_anchor_and_message_bytes() -> None:
    usage = Usage(input_tokens=10, output_tokens=20, cache_write=3, cache_read=7)
    assert usage_anchor(usage) == 40

    message = Message(
        role="assistant",
        content="hi",
        tool_calls=[ToolCall(id="c", name="read_file", input="{}")],
        tool_results=[ToolResult(tool_call_id="c", content="ok")],
    )
    assert message_bytes(message) == 26


def test_estimate_tokens_clamps_and_rounds() -> None:
    messages = [Message(role="user", content="abc")]
    assert estimate_tokens(100, messages, 99) == 100
    assert estimate_tokens(-5, messages, 0) == 2
    assert estimate_tokens(0, messages, -1) == 2
    with_context = estimate_tokens(
        0,
        messages,
        0,
        fixed_messages=[Message(role="assistant", content="durable memory")],
        reminder="recalled note",
    )
    assert with_context > estimate_tokens(0, messages, 0)
    assert (
        estimate_tokens(
            100,
            messages,
            99,
            fixed_messages=[Message(role="assistant", content="durable memory")],
            reminder="recalled note",
        )
        == 100
    )


def test_parse_session_time_new_old_and_invalid() -> None:
    parsed = parse_session_time("20260814-120000-abcd")
    assert parsed is not None
    assert (parsed.year, parsed.month, parsed.day, parsed.hour, parsed.minute) == (
        2026,
        8,
        14,
        12,
        0,
    )
    assert parse_session_time("legacy-id") is None
    assert parse_session_time("99999999-990000-abcd") is None


def test_circuit_breaker_trips_at_three_and_resets() -> None:
    breaker = CompactCircuitBreaker()
    breaker.record_failure()
    breaker.record_failure()
    assert not breaker.tripped()
    breaker.record_failure()
    assert breaker.tripped()
    breaker.record_success()
    assert not breaker.tripped()


def test_recovery_snapshot_dedups_normalizes_and_sorts(tmp_path) -> None:
    state = RecoveryState()
    state.record_file(str(tmp_path / "a.txt"), "a")
    state.record_file(str(tmp_path / "b.txt"), "b")
    state.record_file(str(tmp_path / "sub" / ".." / "a.txt"), "updated")

    snapshot = state.snapshot()
    assert len(snapshot) == 2
    by_path = {record.path: record.content for record in snapshot}
    assert by_path[str((tmp_path / "a.txt").resolve())] == "updated"
    assert by_path[str((tmp_path / "b.txt").resolve())] == "b"
    stamps = [record.timestamp for record in snapshot]
    assert all(stamps[i] >= stamps[i + 1] for i in range(len(stamps) - 1))


def test_spill_single_is_idempotent(tmp_path) -> None:
    session = new_session_context(str(tmp_path))
    first = spill_single(session, "id-1", "hello")
    second = spill_single(session, "id-1", "changed")
    assert first == second
    assert Path(first).read_text(encoding="utf-8") == "hello"


def test_truncate_utf8_does_not_split_multibyte() -> None:
    result = _truncate_utf8("你好世界", 7)
    assert result == "你好"
    assert len(result.encode("utf-8")) <= 7


def test_preview_head_limits_lines_and_bytes() -> None:
    many_lines = "\n".join(["x"] * 100)
    assert preview_head(many_lines).count("\n") <= PREVIEW_HEAD_LINES

    huge_line = "中" * 5000
    assert len(preview_head(huge_line).encode("utf-8")) <= PREVIEW_HEAD_BYTES


def test_replacement_skips_on_oserror(tmp_path, monkeypatch) -> None:
    state = ContentReplacementState()
    session = new_session_context(str(tmp_path))

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("endless_code.compact.layer1.spill_single", _boom)
    result = ToolResult(tool_call_id="x", content="original")
    assert _replacement_for(result, state, session) == "original"
    assert state.existing("x") == (False, None)


def test_provider_stream_legacy_and_new_signatures() -> None:
    class LegacyProvider:
        def __init__(self):
            self.args = None

        def stream(self, messages, tools, reminder):
            self.args = (messages, tools, reminder)
            return iter([])

    class NewProvider:
        def __init__(self):
            self.args = None

        def stream(self, request):
            self.args = (request,)
            return iter([])

    request = Request(messages=[Message(role="user", content="x")], reminder="r")
    legacy = LegacyProvider()
    new = NewProvider()
    _provider_stream(legacy, request)
    _provider_stream(new, request)
    assert legacy.args == (request.messages, request.tools, request.reminder)
    assert new.args == (request,)


def test_pick_recent_tail_empty() -> None:
    assert pick_recent_tail([]) == []


def test_join_after_summary_branches() -> None:
    summary = Message(role="user", content="S")
    assert _join_after_summary(summary, []) == [summary]

    tool_recent = [Message(role="tool", tool_results=[])]
    assert _join_after_summary(summary, tool_recent) == [summary]

    user_recent = [Message(role="user", content="u")]
    joined = _join_after_summary(summary, user_recent)
    assert joined[0] == summary
    assert joined[1].role == "assistant"
    assert joined[2:] == user_recent

    assistant_recent = [Message(role="assistant", content="a")]
    assert _join_after_summary(summary, assistant_recent) == [
        summary,
        *assistant_recent,
    ]


@pytest.mark.asyncio
async def test_ptl_retry_drops_groups_until_success(monkeypatch) -> None:
    messages = []
    for index in range(10):
        messages.append(Message(role="user", content=f"u{index}"))
        messages.append(Message(role="assistant", content=f"a{index}"))

    calls = []

    async def _fake_summarize(_input, reduced):
        calls.append(len(reduced))
        if len(calls) < 4:
            raise PromptTooLongError("too long")
        return "ok"

    monkeypatch.setattr("endless_code.compact.layer2.summarize_once", _fake_summarize)
    result = await ptl_retry(SimpleNamespace(), messages, PromptTooLongError("first"))
    assert result == "ok"
    assert len(calls) == 4
    assert calls == sorted(calls, reverse=True)


@pytest.mark.asyncio
async def test_ptl_retry_raises_when_exhausted(monkeypatch) -> None:
    messages = [
        Message(role="user", content="u"),
        Message(role="assistant", content="a"),
    ]

    async def _always_too_long(_input, _reduced):
        raise PromptTooLongError("still too long")

    monkeypatch.setattr("endless_code.compact.layer2.summarize_once", _always_too_long)
    with pytest.raises(PromptTooLongError):
        await ptl_retry(SimpleNamespace(), messages, PromptTooLongError("first"))


def test_render_file_block_truncates_long_content() -> None:
    record = FileReadRecord(
        path="/p", content="中" * 100_000, timestamp=datetime.now(UTC)
    )
    rendered = render_file_block(record)
    assert "(content truncated)" in rendered
    assert len(rendered) < len(record.content)


def test_build_recovery_attachment_empty_cases() -> None:
    rendered = build_recovery_attachment([], [])
    assert "本会话尚未成功读取文件" in rendered
    assert "当前没有可用工具" in rendered


def test_build_recovery_attachment_limits_files(tmp_path) -> None:
    state = RecoveryState()
    for index in range(8):
        state.record_file(str(tmp_path / f"f{index}.txt"), f"c{index}")
    rendered = build_recovery_attachment(state.snapshot(), [])
    assert rendered.count("读取时间:") == RECOVERY_FILE_LIMIT


def test_recovery_attachment_limit_scales_with_window(tmp_path) -> None:
    record = FileReadRecord(
        path="/p", content="x" * 100_000, timestamp=datetime.now(UTC)
    )
    small = render_file_block(record, recovery_tokens_per_file=5_000)
    large = render_file_block(record, recovery_tokens_per_file=25_000)
    assert "(content truncated)" in small
    assert "(content truncated)" in large
    assert len(large) > len(small)


def test_serialize_conversation_is_deterministic_and_complete() -> None:
    message = Message(
        role="assistant",
        content="hi",
        tool_calls=[ToolCall(id="c", name="read_file", input="{}")],
        tool_results=[ToolResult(tool_call_id="c", content="ok", is_error=False)],
    )
    first = serialize_conversation([message])
    second = serialize_conversation([message])
    assert first == second
    assert '"tool_calls"' in first
    assert '"tool_results"' in first
    assert '"is_error":false' in first


def test_extract_summary_falls_back_without_tag() -> None:
    assert extract_summary("just plain text") == "just plain text"
    assert extract_summary("  padded  ") == "padded"


@pytest.mark.asyncio
async def test_emergency_manage_context_compacts(tmp_path) -> None:
    conv = Conversation()
    conv.add_user("请总结")
    provider = SummaryProvider("<summary>emergency summary</summary>")
    input_ = _input(tmp_path, conv, provider)
    input_.trigger = TriggerKind.EMERGENCY

    result = await manage_context(input_)
    assert result.compacted is True
    assert "历史会话摘要" in conv.messages()[0].content


@pytest.mark.asyncio
async def test_auto_manage_context_below_threshold_does_not_compact(tmp_path) -> None:
    conv = Conversation()
    conv.add_user("short")
    provider = SummaryProvider("<summary>x</summary>")
    input_ = _input(tmp_path, conv, provider)
    input_.trigger = TriggerKind.AUTO

    result = await manage_context(input_)
    assert result.compacted is False
    assert result.err is None


@pytest.mark.asyncio
async def test_auto_manage_context_too_small_window_does_not_compact(tmp_path) -> None:
    conv = Conversation()
    conv.add_user("x")
    provider = SummaryProvider("<summary>x</summary>")
    input_ = _input(tmp_path, conv, provider)
    input_.trigger = TriggerKind.AUTO
    input_.context_window = 100_000

    result = await manage_context(input_)
    assert result.compacted is False
    assert result.err is None


@pytest.mark.asyncio
async def test_auto_manage_context_tripped_breaker_does_not_compact(
    tmp_path, monkeypatch
) -> None:
    conv = Conversation()
    conv.add_user("x")
    provider = SummaryProvider("<summary>x</summary>")
    input_ = _input(tmp_path, conv, provider)
    input_.trigger = TriggerKind.AUTO
    monkeypatch.setattr(
        "endless_code.compact.compact.estimate_tokens", lambda *_args: 200_000
    )
    for _ in range(3):
        input_.auto_tracking.record_failure()

    result = await manage_context(input_)
    assert result.compacted is False


@pytest.mark.asyncio
async def test_auto_manage_context_swallows_compact_error(
    tmp_path, monkeypatch
) -> None:
    conv = Conversation()
    conv.add_user("x")
    provider = SummaryProvider("<summary>x</summary>")
    input_ = _input(tmp_path, conv, provider)
    input_.trigger = TriggerKind.AUTO
    monkeypatch.setattr(
        "endless_code.compact.compact.estimate_tokens", lambda *_args: 200_000
    )

    async def _boom(_input):
        raise RuntimeError("boom")

    monkeypatch.setattr("endless_code.compact.compact.auto_compact", _boom)
    result = await manage_context(input_)
    assert result.compacted is False
    assert isinstance(result.err, RuntimeError)


@pytest.mark.asyncio
async def test_auto_manage_context_compacts_on_success(tmp_path, monkeypatch) -> None:
    conv = Conversation()
    conv.add_user("请总结")
    provider = SummaryProvider("<summary>auto summary</summary>")
    input_ = _input(tmp_path, conv, provider)
    input_.trigger = TriggerKind.AUTO
    monkeypatch.setattr(
        "endless_code.compact.compact.estimate_tokens", lambda *_args: 200_000
    )

    result = await manage_context(input_)
    assert result.compacted is True
    assert "历史会话摘要" in conv.messages()[0].content


def test_provider_stream_signature_failure_falls_back_to_new(monkeypatch) -> None:
    import inspect

    class Provider:
        def __init__(self):
            self.args = None

        def stream(self, request):
            self.args = (request,)
            return iter([])

    def _raise(*_args):
        raise TypeError("cannot inspect")

    monkeypatch.setattr(inspect, "signature", _raise)
    request = Request()
    provider = Provider()
    _provider_stream(provider, request)
    assert provider.args == (request,)


def test_pick_recent_tail_breaks_and_backs_up_over_tool() -> None:
    big = "a" * 43_680
    messages = [
        Message(role="user", content="first"),
        Message(role="assistant", content="before"),
        Message(
            role="tool",
            tool_results=[ToolResult(tool_call_id="c", content="x" * 240)],
        ),
        *[Message(role="assistant", content=big) for _ in range(4)],
    ]
    tail = pick_recent_tail(messages)
    assert len(tail) < len(messages)
    assert tail[0].role == "assistant"
    assert tail[0].content == "before"


@pytest.mark.asyncio
async def test_summarize_once_raises_provider_error() -> None:
    from endless_code.compact.layer2 import summarize_once

    class ErrProvider:
        async def stream(self, request):
            yield StreamEvent(err=RuntimeError("boom"))
            yield StreamEvent(done=True)

    with pytest.raises(RuntimeError):
        await summarize_once(
            SimpleNamespace(provider=ErrProvider()),
            [Message(role="user", content="x")],
        )


@pytest.mark.asyncio
async def test_run_summary_empty_and_ptl_recovery(monkeypatch) -> None:
    from endless_code.compact.layer2 import run_summary

    empty_input = SimpleNamespace(
        conv=Conversation(), recovery=RecoveryState(), tool_defs=[]
    )
    assert await run_summary(empty_input) == []

    conv = Conversation()
    conv.add_user("请总结")
    input_ = SimpleNamespace(conv=conv, recovery=RecoveryState(), tool_defs=[])

    async def _too_long(_input, _messages):
        raise PromptTooLongError("too long")

    async def _recover(_input, _messages, _error):
        return "recovered summary"

    monkeypatch.setattr("endless_code.compact.layer2.summarize_once", _too_long)
    monkeypatch.setattr("endless_code.compact.layer2.ptl_retry", _recover)
    result = await run_summary(input_)
    assert "recovered summary" in result[0].content


@pytest.mark.asyncio
async def test_auto_compact_records_failure_and_raises(monkeypatch) -> None:
    from endless_code.compact.layer2 import auto_compact

    breaker = CompactCircuitBreaker()
    input_ = SimpleNamespace(estimated_token=5, auto_tracking=breaker)

    async def _boom(_input):
        raise RuntimeError("boom")

    monkeypatch.setattr("endless_code.compact.layer2.run_summary", _boom)
    with pytest.raises(RuntimeError):
        await auto_compact(input_)
    assert not breaker.tripped()
