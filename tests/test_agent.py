"""Agent ReAct 循环、并发、停止和历史测试。"""

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from endless_code.agent import (
    MAX_ITERATIONS,
    MAX_UNKNOWN_RUN,
    NOTICE_CANCELLED,
    NOTICE_MAX_ITER,
    NOTICE_STREAM_ERROR,
    NOTICE_UNKNOWN_TOOLS,
    Agent,
    CompactPhase,
    Event,
    Phase,
    ToolEvent,
    new_session_runtime,
)
from endless_code.compact import SummaryState
from endless_code.compact.const import ESTIMATE_CHARS_PER_TOKEN
from endless_code.compact.rolling import STATE_FILENAME
from endless_code.conversation import Conversation
from endless_code.llm import (
    Message,
    PromptTooLongError,
    Request,
    StreamEvent,
    ToolCall,
    ToolDefinition,
    Usage,
)
from endless_code.permission import Decision, Mode, Outcome, new_engine
from endless_code.prompt import (
    PLAN_MODE_REMINDER,
    build_system_prompt,
)
from endless_code.tool import Registry, Result


class FakeProvider:
    def __init__(
        self, scripts: list[list[StreamEvent]], *, repeat_last: bool = False
    ) -> None:
        self.scripts = scripts
        self.repeat_last = repeat_last
        self.call_count = 0
        self.requests: list[tuple[list[Message], list[ToolDefinition], str]] = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(
        self,
        msgs: list[Message],
        tools: list[ToolDefinition],
        system_suffix: str = "",
    ):
        self.requests.append((msgs, tools, system_suffix))
        index = (
            min(self.call_count, len(self.scripts) - 1)
            if self.repeat_last
            else self.call_count
        )
        self.call_count += 1
        for event in self.scripts[index]:
            yield event


class MemoryRecorder:
    def __init__(self) -> None:
        self.done = asyncio.Event()
        self.messages: list[Message] = []

    def load_index(self) -> str:
        return ""

    async def update_async(self, messages: list[Message]) -> None:
        self.messages = messages
        self.done.set()


class BlockingProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__([])
        self.started = asyncio.Event()
        self.closed = False

    async def stream(self, msgs, tools, system_suffix=""):
        self.started.set()
        try:
            await asyncio.Event().wait()
            yield StreamEvent(done=True)
        finally:
            self.closed = True


class EchoTool:
    read_only = True

    def name(self) -> str:
        return "echo_tool"

    def description(self) -> str:
        return "echo"

    def parameters(self) -> dict:
        return {"type": "object"}

    async def execute(self, args: str) -> Result:
        data = json.loads(args or "{}")
        return Result(content=data.get("value", "ok"))


class BlockingTool(EchoTool):
    read_only = False

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    def name(self) -> str:
        return "blocking"

    async def execute(self, args: str) -> Result:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return Result(content="unreachable")


@dataclass
class Timeline:
    active_reads: int = 0
    peak_reads: int = 0
    events: list[str] = field(default_factory=list)


class TimedTool(EchoTool):
    def __init__(self, tool_name: str, read_only: bool, timeline: Timeline) -> None:
        self._name = tool_name
        self.read_only = read_only
        self.timeline = timeline

    def name(self) -> str:
        return self._name

    async def execute(self, args: str) -> Result:
        label = json.loads(args)["value"]
        self.timeline.events.append(f"start:{label}")
        if self.read_only:
            self.timeline.active_reads += 1
            self.timeline.peak_reads = max(
                self.timeline.peak_reads, self.timeline.active_reads
            )
            await asyncio.sleep(0.03)
            self.timeline.active_reads -= 1
        else:
            await asyncio.sleep(0)
        self.timeline.events.append(f"end:{label}")
        return Result(content=label)


def _registry(*tools) -> Registry:
    registry = Registry()
    for tool in tools:
        registry.register(tool)
    return registry


def _tool_event(
    call_id: str, name: str = "echo_tool", value: str = "ok"
) -> StreamEvent:
    return StreamEvent(
        tool_calls=[ToolCall(id=call_id, name=name, input=json.dumps({"value": value}))]
    )


async def _run(agent: Agent, conv: Conversation, **kwargs) -> list[Event]:
    kwargs.setdefault("mode", Mode.BYPASS)
    return [event async for event in agent.run(conv, **kwargs)]


def test_event_model_and_constants() -> None:
    event = Event(iteration=1, usage=Usage(2, 3))
    tool = ToolEvent(call_id="c1", name="read_file")
    assert event.iteration == 1
    assert event.usage.output_tokens == 3
    assert tool.phase is Phase.START
    assert Mode.DEFAULT.value == 0
    assert MAX_ITERATIONS == 25
    assert MAX_UNKNOWN_RUN == 3


@pytest.mark.asyncio
async def test_natural_completion() -> None:
    provider = FakeProvider([[StreamEvent(text="hello"), StreamEvent(done=True)]])
    conv = Conversation()
    conv.add_user("hi")
    events = await _run(Agent(provider, _registry()), conv)
    assert provider.call_count == 1
    assert "".join(event.text for event in events) == "hello"
    assert sum(event.done for event in events) == 1
    assert [(msg.role, msg.content) for msg in conv.messages()] == [
        ("user", "hi"),
        ("assistant", "hello"),
    ]


@pytest.mark.asyncio
async def test_multi_round_event_sequence_and_history() -> None:
    provider = FakeProvider(
        [
            [StreamEvent(text="checking"), _tool_event("c1"), StreamEvent(done=True)],
            [
                StreamEvent(text="finished"),
                StreamEvent(usage=Usage(5, 2)),
                StreamEvent(done=True),
            ],
        ]
    )
    conv = Conversation()
    conv.add_user("work")
    events = await _run(Agent(provider, _registry(EchoTool())), conv)
    assert [event.iteration for event in events if event.iteration] == [1, 2]
    assert [event.tool.phase for event in events if event.tool] == [
        Phase.START,
        Phase.END,
    ]
    assert [
        (event.usage.input_tokens, event.usage.output_tokens)
        for event in events
        if event.usage
    ] == [(5, 2)]
    assert sum(event.done for event in events) == 1
    messages = conv.messages()
    assert [message.role for message in messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert messages[1].tool_calls[0].id == "c1"
    assert messages[2].tool_results[0].tool_call_id == "c1"
    assert messages[-1].content == "finished"
    assert [message.role for message in provider.requests[1][0]] == [
        "user",
        "assistant",
        "tool",
    ]


@pytest.mark.asyncio
async def test_plan_mode_limits_tools_and_suffix() -> None:
    provider = FakeProvider([[StreamEvent(text="plan"), StreamEvent(done=True)]])
    registry = _registry(EchoTool(), TimedTool("writer", False, Timeline()))
    conv = Conversation()
    conv.add_user("plan it")
    await _run(Agent(provider, registry), conv, mode=Mode.PLAN)
    _, tools, suffix = provider.requests[0]
    assert [tool.name for tool in tools] == ["echo_tool"]
    assert suffix == PLAN_MODE_REMINDER


@pytest.mark.asyncio
async def test_max_iterations_stops_at_limit() -> None:
    provider = FakeProvider(
        [[_tool_event("same"), StreamEvent(done=True)]], repeat_last=True
    )
    conv = Conversation()
    conv.add_user("loop")
    events = await _run(Agent(provider, _registry(EchoTool())), conv)
    assert provider.call_count == MAX_ITERATIONS
    assert any(event.notice == NOTICE_MAX_ITER for event in events)
    assert conv.messages()[-1].content == NOTICE_MAX_ITER


@pytest.mark.asyncio
async def test_unknown_tools_stop_and_known_tool_resets_count() -> None:
    unknown = lambda index: [
        _tool_event(f"u{index}", name="missing"),
        StreamEvent(done=True),
    ]
    provider = FakeProvider([unknown(1), unknown(2), unknown(3)])
    conv = Conversation()
    conv.add_user("unknown")
    events = await _run(Agent(provider, _registry()), conv)
    assert provider.call_count == MAX_UNKNOWN_RUN
    assert any(event.notice == NOTICE_UNKNOWN_TOOLS for event in events)

    provider = FakeProvider(
        [
            unknown(1),
            unknown(2),
            [_tool_event("known"), StreamEvent(done=True)],
            unknown(3),
            unknown(4),
            unknown(5),
        ]
    )
    conv = Conversation()
    conv.add_user("reset")
    events = await _run(Agent(provider, _registry(EchoTool())), conv)
    assert provider.call_count == 6
    assert any(event.notice == NOTICE_UNKNOWN_TOOLS for event in events)


@pytest.mark.asyncio
async def test_batch_execution_is_concurrent_and_ordered() -> None:
    timeline = Timeline()
    registry = _registry(
        TimedTool("reader", True, timeline),
        TimedTool("writer", False, timeline),
    )
    calls = [
        ToolCall(id="r1", name="reader", input=json.dumps({"value": "r1"})),
        ToolCall(id="r2", name="reader", input=json.dumps({"value": "r2"})),
        ToolCall(id="w1", name="writer", input=json.dumps({"value": "w1"})),
        ToolCall(id="r3", name="reader", input=json.dumps({"value": "r3"})),
    ]
    provider = FakeProvider(
        [
            [StreamEvent(tool_calls=calls), StreamEvent(done=True)],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("batch")
    events = await _run(Agent(provider, registry), conv)
    assert timeline.peak_reads == 2
    assert timeline.events.index("start:w1") > timeline.events.index("end:r1")
    assert timeline.events.index("start:w1") > timeline.events.index("end:r2")
    assert timeline.events.index("start:r3") > timeline.events.index("end:w1")
    starts = [
        event.tool.call_id
        for event in events
        if event.tool and event.tool.phase is Phase.START
    ]
    ends = [
        event.tool.call_id
        for event in events
        if event.tool and event.tool.phase is Phase.END
    ]
    assert starts == ["r1", "r2", "w1", "r3"]
    assert ends == starts
    results = conv.messages()[2].tool_results
    assert [result.tool_call_id for result in results] == starts


@pytest.mark.asyncio
async def test_batch_history_is_committed_atomically() -> None:
    timeline = Timeline()
    calls = [
        ToolCall(id="r1", name="reader", input=json.dumps({"value": "r1"})),
        ToolCall(id="r2", name="reader", input=json.dumps({"value": "r2"})),
    ]
    provider = FakeProvider(
        [
            [StreamEvent(tool_calls=calls), StreamEvent(done=True)],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("batch history")
    stream = (
        Agent(
            provider,
            _registry(TimedTool("reader", True, timeline)),
        )
        .run(conv, mode=Mode.BYPASS)
        .__aiter__()
    )

    while True:
        event = await anext(stream)
        if event.tool is not None and event.tool.phase is Phase.END:
            break

    assert [message.role for message in conv.messages()] == ["user", "assistant"]

    remaining = [event async for event in stream]
    assert any(event.done for event in remaining)
    messages = conv.messages()
    assert [message.role for message in messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert [result.tool_call_id for result in messages[2].tool_results] == ["r1", "r2"]


@pytest.mark.asyncio
async def test_stream_events_cancel_closes_provider_and_history_recovers() -> None:
    provider = BlockingProvider()
    conv = Conversation()
    conv.add_user("wait")
    cancel = asyncio.Event()
    task = asyncio.create_task(_run(Agent(provider, _registry()), conv, cancel=cancel))
    await provider.started.wait()
    cancel.set()
    events = await asyncio.wait_for(task, timeout=1)
    assert provider.closed
    assert any(event.notice == NOTICE_CANCELLED for event in events)
    assert sum(event.done for event in events) == 1
    assert conv.last_role() == "assistant"


@pytest.mark.asyncio
async def test_tool_cancel_pairs_results_and_history_recovers() -> None:
    tool = BlockingTool()
    provider = FakeProvider(
        [
            [_tool_event("block", name="blocking"), StreamEvent(done=True)],
            [StreamEvent(text="recovered"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("block")
    cancel = asyncio.Event()
    task = asyncio.create_task(
        _run(Agent(provider, _registry(tool)), conv, cancel=cancel)
    )
    await tool.started.wait()
    cancel.set()
    events = await asyncio.wait_for(task, timeout=1)
    assert tool.cancelled
    assert any(event.notice == NOTICE_CANCELLED for event in events)
    assert conv.messages()[-2].tool_results[0].tool_call_id == "block"
    assert conv.messages()[-2].tool_results[0].is_error
    assert conv.last_role() == "assistant"

    conv.add_user("continue")
    followup = await _run(Agent(provider, _registry(tool)), conv)
    assert any(event.text == "recovered" for event in followup)
    assert conv.messages()[-1].content == "recovered"


@pytest.mark.asyncio
async def test_stream_error_has_terminal_history_and_can_continue() -> None:
    provider = FakeProvider(
        [
            [StreamEvent(text="partial"), StreamEvent(err=RuntimeError("offline"))],
            [StreamEvent(text="ok"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("first")
    events = await _run(Agent(provider, _registry()), conv)
    assert any(isinstance(event.err, RuntimeError) for event in events)
    assert sum(event.done for event in events) == 1
    assert NOTICE_STREAM_ERROR in conv.messages()[-1].content
    assert conv.last_role() == "assistant"

    conv.add_user("second")
    events = await _run(Agent(provider, _registry()), conv)
    assert any(event.text == "ok" for event in events)
    assert conv.messages()[-1].content == "ok"


class RaisingProvider:
    """stream 产出部分文本后直接抛异常，模拟网络重置等带外故障。"""

    name = "raising-fake"
    model = "raising-model"

    def __init__(self) -> None:
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        yield StreamEvent(text="partial")
        if len(self.requests) == 1:
            raise RuntimeError("connection reset")
        yield StreamEvent(done=True)


@pytest.mark.asyncio
async def test_provider_raised_error_takes_recovery_path_and_can_continue() -> None:
    provider = RaisingProvider()
    conv = Conversation()
    conv.add_user("first")
    events = await _run(Agent(provider, _registry()), conv)
    assert any(isinstance(event.err, RuntimeError) for event in events)
    assert sum(event.done for event in events) == 1
    assert NOTICE_STREAM_ERROR in conv.messages()[-1].content
    assert conv.last_role() == "assistant"

    conv.add_user("second")
    events = await _run(Agent(provider, _registry()), conv)
    assert any(event.text == "partial" for event in events)
    assert conv.messages()[-1].content == "partial"


class RequestProvider:
    name = "request-fake"
    model = "request-model"

    def __init__(self, scripts):
        self.scripts = scripts
        self.requests = []
        self.index = 0

    async def stream(self, request):
        self.requests.append(request)
        script = self.scripts[self.index]
        self.index += 1
        for event in script:
            yield event


@pytest.mark.asyncio
async def test_stable_prefix_plan_reminder_and_history_are_isolated() -> None:
    provider = RequestProvider(
        [
            [_tool_event("one"), StreamEvent(done=True)],
            [_tool_event("two"), StreamEvent(done=True)],
            [_tool_event("three"), StreamEvent(done=True)],
            [_tool_event("four"), StreamEvent(done=True)],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("plan")
    await _run(Agent(provider, _registry(EchoTool())), conv, mode=Mode.PLAN)
    reminders = [request.reminder for request in provider.requests]
    assert reminders[0] == PLAN_MODE_REMINDER
    assert reminders[1] != PLAN_MODE_REMINDER
    assert reminders[4] == PLAN_MODE_REMINDER
    assert len({request.system.stable for request in provider.requests}) == 1
    assert all(
        [tool.name for tool in request.tools] == ["echo_tool"]
        for request in provider.requests
    )
    assert all("system-reminder" not in message.content for message in conv.messages())


@pytest.mark.asyncio
async def test_cache_usage_is_forwarded_from_request_provider() -> None:
    provider = RequestProvider(
        [
            [
                StreamEvent(usage=Usage(3, 2, 7, 5)),
                StreamEvent(text="done"),
                StreamEvent(done=True),
            ]
        ]
    )
    conv = Conversation()
    conv.add_user("work")
    events = await _run(Agent(provider, _registry()), conv)
    usage = next(event.usage for event in events if event.usage)
    assert (
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_write,
        usage.cache_read,
    ) == (3, 2, 7, 5)


async def _request_round(
    provider: RequestProvider, conv: Conversation, agent: Agent
) -> None:
    """跑一轮对话，让 provider 记录一次完整 Request。"""
    conv.add_user("round")
    await _run(agent, conv)


@pytest.mark.asyncio
async def test_output_style_reaches_stable_system_prompt() -> None:
    provider = RequestProvider([[StreamEvent(text="ok"), StreamEvent(done=True)]])
    conv = Conversation()
    agent = Agent(provider, _registry(), output_style="concise")
    await _request_round(provider, conv, agent)
    stable = provider.requests[0].system.stable
    assert "precedence over the tone guidance" in stable
    assert "Be extremely brief" in stable


@pytest.mark.asyncio
async def test_default_style_stable_prompt_unchanged() -> None:
    provider = RequestProvider([[StreamEvent(text="ok"), StreamEvent(done=True)]])
    conv = Conversation()
    agent = Agent(provider, _registry())
    await _request_round(provider, conv, agent)
    assert provider.requests[0].system.stable == build_system_prompt("", "")


@pytest.mark.asyncio
async def test_set_output_style_applies_next_round() -> None:
    provider = RequestProvider(
        [
            [StreamEvent(text="ok"), StreamEvent(done=True)],
            [StreamEvent(text="ok2"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    agent = Agent(provider, _registry())
    await _request_round(provider, conv, agent)
    assert "precedence over the tone guidance" not in (
        provider.requests[0].system.stable
    )
    agent.set_output_style("explanatory")
    await _request_round(provider, conv, agent)
    stable = provider.requests[1].system.stable
    assert "precedence over the tone guidance" in stable
    assert "why this approach was chosen" in stable


class EmergencyProvider:
    name = "emergency-fake"
    model = "emergency-model"

    def __init__(self) -> None:
        self.main_calls = 0
        self.summary_calls = 0

    async def stream(self, request):
        if not request.system.stable:
            self.summary_calls += 1
            yield StreamEvent(text="<summary>## 1 主要请求和意图\n继续任务</summary>")
            yield StreamEvent(done=True)
            return
        self.main_calls += 1
        if self.main_calls == 1:
            yield StreamEvent(err=PromptTooLongError("too long"))
            return
        yield StreamEvent(text="recovered")
        yield StreamEvent(done=True)


@pytest.mark.asyncio
async def test_prompt_too_long_compacts_then_retries_once(tmp_path) -> None:
    provider = EmergencyProvider()
    conv = Conversation()
    conv.add_user("继续任务")
    agent = Agent(
        provider,
        _registry(),
        runtime=new_session_runtime(str(tmp_path), 200_000),
    )
    events = await _run(agent, conv)
    phases = [event.compact.phase for event in events if event.compact is not None]
    assert provider.main_calls == 2
    assert provider.summary_calls == 1
    assert phases == [
        CompactPhase.BEFORE_EMERGENCY,
        CompactPhase.AFTER_EMERGENCY,
    ]
    assert conv.messages()[-1].content == "recovered"


@pytest.mark.asyncio
async def test_ask_approval_allow_once(tmp_path) -> None:
    registry = _registry(TimedTool("write_file", False, Timeline()))
    call = ToolCall(
        id="w1",
        name="write_file",
        input=json.dumps({"path": "src/x.py", "content": "x", "value": "w"}),
    )
    provider = FakeProvider(
        [
            [StreamEvent(tool_calls=[call]), StreamEvent(done=True)],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("go")
    engine, err = new_engine(str(tmp_path))
    assert err is None
    agent = Agent(provider, registry, engine=engine)

    async for event in agent.run(conv, mode=Mode.DEFAULT):
        if event.approval is not None:
            event.approval.respond.set_result(Outcome.ALLOW_ONCE)

    assert [message.role for message in conv.messages()] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert conv.messages()[-2].tool_results[0].is_error is False


@pytest.mark.asyncio
async def test_ask_approval_deny_once(tmp_path) -> None:
    registry = _registry(TimedTool("write_file", False, Timeline()))
    call = ToolCall(
        id="w1",
        name="write_file",
        input=json.dumps({"path": "src/x.py", "content": "x", "value": "w"}),
    )
    provider = FakeProvider(
        [
            [StreamEvent(tool_calls=[call]), StreamEvent(done=True)],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("go")
    engine, err = new_engine(str(tmp_path))
    assert err is None
    agent = Agent(provider, registry, engine=engine)

    async for event in agent.run(conv, mode=Mode.DEFAULT):
        if event.approval is not None:
            event.approval.respond.set_result(Outcome.DENY_ONCE)

    assert conv.messages()[-2].tool_results[0].is_error is True


@pytest.mark.asyncio
async def test_ask_approval_allow_forever_persists(tmp_path) -> None:
    registry = _registry(TimedTool("write_file", False, Timeline()))
    call = ToolCall(
        id="w1",
        name="write_file",
        input=json.dumps({"path": "src/x.py", "content": "x", "value": "w"}),
    )
    provider = FakeProvider(
        [
            [StreamEvent(tool_calls=[call]), StreamEvent(done=True)],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("go")
    engine, err = new_engine(str(tmp_path))
    assert err is None
    agent = Agent(provider, registry, engine=engine)

    async for event in agent.run(conv, mode=Mode.DEFAULT):
        if event.approval is not None:
            event.approval.respond.set_result(Outcome.ALLOW_FOREVER)

    local = tmp_path / ".endless-code" / "settings.local.yaml"
    assert "Write(src/x.py)" in local.read_text(encoding="utf-8")
    engine2, _ = new_engine(str(tmp_path))
    assert engine2.check(Mode.DEFAULT, call, False)[0] is Decision.ALLOW


@pytest.mark.asyncio
async def test_explicit_memory_signal_schedules_background_update(tmp_path) -> None:
    provider = FakeProvider([[StreamEvent(text="done"), StreamEvent(done=True)]])
    manager = MemoryRecorder()
    conv = Conversation()
    conv.add_user("请记住我偏好中文回复")
    agent = Agent(
        provider,
        _registry(),
        runtime=new_session_runtime(str(tmp_path)),
        memory_manager=manager,  # type: ignore[arg-type]
    )

    await _run(agent, conv)
    await asyncio.wait_for(manager.done.wait(), timeout=1)
    assert [message.content for message in manager.messages] == [
        "请记住我偏好中文回复",
        "done",
    ]


def test_refresh_budget_follows_window_and_tools(tmp_path) -> None:
    runtime = new_session_runtime(str(tmp_path), 128_000)
    assert runtime.budget.effective_auto_threshold == 101_760

    runtime.context_window = 1_000_000
    plain = runtime.refresh_budget([])
    assert plain.auto_compact_threshold == 795_000

    loaded = runtime.refresh_budget(
        [
            ToolDefinition(
                name="read_file",
                description="d" * 3_500,
                input_schema={"type": "object"},
            )
        ]
    )
    assert loaded.tool_schema_tokens > 0
    assert loaded.usable_window == plain.usable_window - loaded.tool_schema_tokens


class SummaryOnlyProvider:
    """主请求走 system，摘要请求不带 system，据此分流。"""

    name = "auto-compact-fake"
    model = "auto-compact-model"

    def __init__(self) -> None:
        self.main_calls = 0
        self.summary_calls = 0

    async def stream(self, request: Request):
        if not request.system.stable:
            self.summary_calls += 1
            yield StreamEvent(
                text="<summary>## 1 主要请求和意图\n继续既有任务。</summary>"
            )
            yield StreamEvent(done=True)
            return
        self.main_calls += 1
        yield StreamEvent(text="ok")
        yield StreamEvent(done=True)


@pytest.mark.asyncio
async def test_small_window_turn_triggers_auto_compaction(tmp_path) -> None:
    """128k 窗口下回合内必须看到自动压缩；固定阈值时代阈值是负数。"""
    provider = SummaryOnlyProvider()
    conv = Conversation()
    for _ in range(10):
        conv.add_user("x" * 40_000)
    agent = Agent(
        provider,
        _registry(),
        runtime=new_session_runtime(str(tmp_path), 128_000),
    )
    events = await _run(agent, conv)
    phases = [event.compact.phase for event in events if event.compact is not None]
    assert phases == [CompactPhase.BEFORE_AUTO, CompactPhase.AFTER_AUTO]
    assert provider.summary_calls == 1
    assert "历史会话摘要" in conv.messages()[0].content


class UsageProvider:
    name = "usage-fake"
    model = "usage-model"

    def __init__(self, usage: Usage) -> None:
        self.usage = usage
        self.calls = 0

    async def stream(self, request: Request):
        self.calls += 1
        yield StreamEvent(text="收到")
        yield StreamEvent(done=True, usage=self.usage)


@pytest.mark.asyncio
async def test_reported_usage_calibrates_token_meter(tmp_path) -> None:
    provider = UsageProvider(Usage(input_tokens=1000, output_tokens=100))
    conv = Conversation()
    conv.add_user("请分析" + "中" * 600)
    runtime = new_session_runtime(str(tmp_path))
    agent = Agent(provider, _registry(), runtime=runtime)

    await _run(agent, conv)
    assert runtime.meter.samples >= 1
    assert runtime.meter.chars_per_token != ESTIMATE_CHARS_PER_TOKEN
    assert runtime.usage_anchor == 1100


@pytest.mark.asyncio
async def test_compaction_resets_token_meter(tmp_path) -> None:
    provider = SummaryOnlyProvider()
    conv = Conversation()
    for _ in range(10):
        conv.add_user("中" * 40_000)
    runtime = new_session_runtime(str(tmp_path), 128_000)
    agent = Agent(provider, _registry(), runtime=runtime)
    runtime.meter.observe(added_bytes=400_000, added_tokens=400_000)
    assert runtime.meter.chars_per_token != ESTIMATE_CHARS_PER_TOKEN

    await _run(agent, conv)
    assert runtime.meter.chars_per_token == ESTIMATE_CHARS_PER_TOKEN


class LimitedWindowProvider:
    """按模型真实接受量（而非配置窗口）返回 PTL 的 provider。"""

    name = "limited-fake"
    model = "limited-model"

    def __init__(self, limit_tokens: int) -> None:
        self.limit_bytes = limit_tokens * ESTIMATE_CHARS_PER_TOKEN
        self.ptl_calls = 0
        self.main_calls = 0
        self.summary_calls = 0

    async def stream(self, request: Request):
        if not request.system.stable:
            self.summary_calls += 1
            yield StreamEvent(text="<summary>## 1 主要请求和意图\n收敛窗口</summary>")
            yield StreamEvent(done=True)
            return
        self.main_calls += 1
        size = sum(len(item.content.encode("utf-8")) for item in request.messages)
        if size > self.limit_bytes:
            self.ptl_calls += 1
            yield StreamEvent(err=PromptTooLongError("too long"))
            return
        yield StreamEvent(text="ok")
        yield StreamEvent(done=True)


def _history_of_tokens(tokens: int, parts: int) -> Conversation:
    """构造估算量刚好是 tokens 的历史（ASCII 按默认 chars/token 折算）。"""
    conv = Conversation()
    total_bytes = int(tokens * ESTIMATE_CHARS_PER_TOKEN)
    payload = (total_bytes - len("user") * parts) // parts
    for _ in range(parts):
        conv.add_user("x" * payload)
    return conv


@pytest.mark.asyncio
async def test_prompt_too_long_calibrates_window_and_budget(tmp_path) -> None:
    """配置 1M、模型只吃 100k：PTL 后窗口收敛为 121_600 并重建预算（AC6）。"""
    provider = LimitedWindowProvider(100_000)
    conv = _history_of_tokens(128_000, 20)
    runtime = new_session_runtime(str(tmp_path), 1_000_000)
    agent = Agent(provider, _registry(), runtime=runtime)

    events = await _run(agent, conv)

    notices = [event for event in events if event.notice and "收敛" in event.notice]
    assert len(notices) == 1
    assert runtime.context_window == 121_600
    assert runtime.budget.context_window == 121_600
    assert runtime.clamp_notice_sent is True
    assert provider.ptl_calls == 1
    # 校准先于紧急压缩，让修正后的预算把本轮抗下来
    assert conv.messages()[-1].content == "ok"
    assert [event.compact.phase for event in events if event.compact is not None] == [
        CompactPhase.BEFORE_EMERGENCY,
        CompactPhase.AFTER_EMERGENCY,
    ]


@pytest.mark.asyncio
async def test_calibration_clamps_further_without_repeat_notice(tmp_path) -> None:
    """同一会话再次超限：只取更小的窗口，不重复提示（AC6）。"""
    provider = LimitedWindowProvider(10_000)
    conv = _history_of_tokens(128_000, 20)
    runtime = new_session_runtime(str(tmp_path), 1_000_000)
    agent = Agent(provider, _registry(), runtime=runtime)

    first = await _run(agent, conv)

    assert runtime.context_window == 121_600
    assert (
        len([event for event in first if event.notice and "收敛" in event.notice]) == 1
    )

    second = await _run(agent, conv)

    assert provider.ptl_calls >= 3  # 新一轮仍撞上限
    assert 16_000 < runtime.context_window < 121_600
    assert (
        len([event for event in second if event.notice and "收敛" in event.notice]) == 0
    )


@pytest.mark.asyncio
async def test_calibrated_window_keeps_following_turns_within_budget(tmp_path) -> None:
    """自愈端到端：收敛后的预算使后续轮次不再超限（AC6）。"""
    provider = LimitedWindowProvider(100_000)
    conv = _history_of_tokens(128_000, 20)
    runtime = new_session_runtime(str(tmp_path), 1_000_000)
    agent = Agent(provider, _registry(), runtime=runtime)
    await _run(agent, conv)

    conv.add_user("x" * 40_000)
    events = await _run(agent, conv)

    assert provider.ptl_calls == 1  # 新一轮没再撞上限
    assert runtime.context_window == 121_600
    assert conv.messages()[-1].content == "ok"
    assert not [event for event in events if event.compact is not None]


@pytest.mark.asyncio
async def test_auto_compaction_persists_rolling_state(tmp_path) -> None:
    """自动压缩后滚动状态写入会话目录（AC5）。"""
    provider = SummaryOnlyProvider()
    conv = Conversation()
    for _ in range(10):
        conv.add_user("x" * 40_000)
    runtime = new_session_runtime(str(tmp_path), 128_000)
    agent = Agent(provider, _registry(), runtime=runtime)

    await _run(agent, conv)

    assert provider.summary_calls == 1
    state_path = Path(runtime.session.session_dir) / STATE_FILENAME
    assert state_path.exists()
    loaded = SummaryState.load(runtime.session.session_dir)
    assert loaded.revision == 1
    assert loaded.covered_messages > 0
    assert loaded.summary_text


class RollingProbeProvider:
    """记录每次摘要请求的提示词，用于验证增量只摘新片段。"""

    name = "rolling-probe-fake"
    model = "rolling-probe-model"

    def __init__(self) -> None:
        self.summary_prompts: list[str] = []
        self.round_marker = 0

    async def stream(self, request: Request):
        if not request.system.stable:
            self.round_marker += 1
            self.summary_prompts.append(
                "\n".join(item.content for item in request.messages)
            )
            yield StreamEvent(
                text=f"<summary>## 1 主要请求和意图\n第{self.round_marker}版摘要</summary>"
            )
            yield StreamEvent(done=True)
            return
        yield StreamEvent(text="ok")
        yield StreamEvent(done=True)


@pytest.mark.asyncio
async def test_rolling_progression_summarizes_only_new_segments(tmp_path) -> None:
    """长会话增量演进：首轮建状态，后续只摘覆盖点之后的新片段（AC4、端到端）。"""
    provider = RollingProbeProvider()
    conv = _history_of_tokens(128_000, 20)
    runtime = new_session_runtime(str(tmp_path), 128_000)
    agent = Agent(provider, _registry(), runtime=runtime)

    await _run(agent, conv)

    assert runtime.summary_state.revision == 1
    first_prompt = provider.summary_prompts[0]
    assert "上一版摘要" not in first_prompt
    x_block = "x" * 22_396  # _history_of_tokens(128_000, 20) 的正文
    assert first_prompt.count(x_block) == 20  # 首轮全量：全部历史送入模型

    for _ in range(14):
        conv.add_user("y" * 22_396)
    await _run(agent, conv)

    assert runtime.summary_state.revision == 2
    second_prompt = provider.summary_prompts[1]
    assert "上一版摘要" in second_prompt
    assert "第1版摘要" in second_prompt
    assert second_prompt.count("y" * 22_396) == 14
    assert second_prompt.count(x_block) == 5  # 只重送上次保留的近期原文
    assert len(second_prompt) < len(first_prompt)  # 摘要成本逐轮下降
