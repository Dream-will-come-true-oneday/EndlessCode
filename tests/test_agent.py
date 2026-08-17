"""Agent ReAct 循环、并发、停止和历史测试。"""

import asyncio
import json
from dataclasses import dataclass, field

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
from endless_code.compact import ManageOutput
from endless_code.conversation import Conversation
from endless_code.llm import (
    Message,
    PromptTooLongError,
    StreamEvent,
    ToolCall,
    ToolDefinition,
    Usage,
)
from endless_code.permission import Decision, Mode, Outcome, new_engine
from endless_code.prompt import PLAN_MODE_REMINDER
from endless_code.session import Writer
from endless_code.tool import Registry, Result
from endless_code.tool.deferred import TOOL_SEARCH_NAME


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


def test_new_session_runtime_defaults_to_200k(tmp_path) -> None:
    runtime = new_session_runtime(str(tmp_path))
    assert runtime.context_window == 200_000


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


class DeferredEchoTool(EchoTool):
    def __init__(self, name: str, *, read_only: bool = True) -> None:
        self._name = name
        self.read_only = read_only
        self.calls: list[str] = []

    def name(self) -> str:
        return self._name

    def description(self) -> str:
        return f"deferred description for {self._name}"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        }

    async def execute(self, args: str) -> Result:
        self.calls.append(args)
        return await super().execute(args)


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


def _deferred_registry(*tools) -> Registry:
    registry = Registry()
    registry.register(EchoTool())
    for tool in tools:
        registry.register(tool, deferred=True)
    return registry


def _search_event(call_id: str, *names: str) -> StreamEvent:
    return StreamEvent(
        tool_calls=[
            ToolCall(
                id=call_id,
                name=TOOL_SEARCH_NAME,
                input=json.dumps({"names": list(names)}),
            )
        ]
    )


def _named_tool_event(call_id: str, name: str, value: str = "ok") -> StreamEvent:
    return StreamEvent(
        tool_calls=[ToolCall(id=call_id, name=name, input=json.dumps({"value": value}))]
    )


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
@pytest.mark.parametrize(
    ("context_window", "estimated"),
    [(200_000, 167_000), (1_000_000, 835_000)],
)
async def test_auto_compact_event_uses_window_threshold(
    context_window, estimated, tmp_path, monkeypatch
) -> None:
    provider = FakeProvider([[StreamEvent(text="done"), StreamEvent(done=True)]])
    conv = Conversation()
    conv.add_user("work")
    managed_inputs = []

    async def _fake_manage(input_):
        managed_inputs.append(input_)
        return ManageOutput(input_.estimated_token, input_.estimated_token)

    monkeypatch.setattr("endless_code.agent.estimate_tokens", lambda *_: estimated)
    monkeypatch.setattr("endless_code.agent.manage_context", _fake_manage)
    runtime = new_session_runtime(str(tmp_path), context_window)

    events = await _run(Agent(provider, _registry(), runtime=runtime), conv)

    assert any(
        event.compact is not None and event.compact.phase is CompactPhase.BEFORE_AUTO
        for event in events
    )
    assert managed_inputs[0].context_window == context_window


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
async def test_deferred_first_request_tool_search_next_round_and_not_persisted(
    tmp_path,
) -> None:
    remote = DeferredEchoTool("mcp__demo__read")
    provider = RequestProvider(
        [
            [_search_event("search", remote.name()), StreamEvent(done=True)],
            [
                _named_tool_event("remote", remote.name(), "loaded"),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    writer = Writer(str(tmp_path / "session"), "fake-model")
    conv = Conversation(writer.append, writer.replace)
    try:
        conv.add_user("use the remote tool")
        await _run(Agent(provider, _deferred_registry(remote)), conv)
    finally:
        writer.close()

    first_names = [tool.name for tool in provider.requests[0].tools]
    second_names = [tool.name for tool in provider.requests[1].tools]
    assert first_names == ["echo_tool", TOOL_SEARCH_NAME]
    assert remote.name() not in first_names
    assert remote.name() in provider.requests[0].reminder
    assert remote.name() in second_names
    assert remote.name() not in provider.requests[1].reminder
    remote_definition = next(
        tool for tool in provider.requests[1].tools if tool.name == remote.name()
    )
    assert remote_definition.description == remote.description()
    assert remote_definition.input_schema == remote.parameters()
    assert "ToolSearch" in provider.requests[0].system.stable
    assert all(
        "Deferred MCP tools available" not in message.content
        for message in conv.messages()
    )
    archive = writer.path.read_text(encoding="utf-8")
    assert "Deferred MCP tools available" not in archive
    assert remote.description() not in archive
    assert remote.calls == [json.dumps({"value": "loaded"})]


@pytest.mark.asyncio
async def test_unactivated_deferred_tool_is_blocked_before_permission() -> None:
    remote = DeferredEchoTool("mcp__demo__write", read_only=False)
    provider = RequestProvider(
        [
            [_named_tool_event("remote", remote.name()), StreamEvent(done=True)],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("call it directly")
    events = await _run(
        Agent(provider, _deferred_registry(remote)), conv, mode=Mode.DEFAULT
    )

    assert not any(event.approval is not None for event in events)
    assert remote.calls == []
    result = next(
        event.tool
        for event in events
        if event.tool is not None and event.tool.phase is Phase.END
    )
    assert result.is_error
    assert TOOL_SEARCH_NAME in result.result


@pytest.mark.asyncio
async def test_e2e_same_response_tool_search_activation_boundary() -> None:
    remote = DeferredEchoTool("mcp__demo__read")
    provider = RequestProvider(
        [
            [
                StreamEvent(
                    tool_calls=[
                        ToolCall(
                            id="search",
                            name=TOOL_SEARCH_NAME,
                            input=json.dumps({"names": [remote.name()]}),
                        ),
                        ToolCall(
                            id="early",
                            name=remote.name(),
                            input=json.dumps({"value": "early"}),
                        ),
                    ]
                ),
                StreamEvent(done=True),
            ],
            [
                _named_tool_event("later", remote.name(), "later"),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("search and call")
    events = await _run(Agent(provider, _deferred_registry(remote)), conv)

    early = next(
        event.tool
        for event in events
        if event.tool is not None
        and event.tool.call_id == "early"
        and event.tool.phase is Phase.END
    )
    assert early.is_error
    assert TOOL_SEARCH_NAME in early.result
    assert remote.name() not in [tool.name for tool in provider.requests[0].tools]
    assert remote.name() in [tool.name for tool in provider.requests[1].tools]
    assert remote.calls == [json.dumps({"value": "later"})]


@pytest.mark.asyncio
async def test_deferred_activation_does_not_bypass_permission() -> None:
    remote = DeferredEchoTool("mcp__demo__write", read_only=False)
    provider = RequestProvider(
        [
            [_search_event("search", remote.name()), StreamEvent(done=True)],
            [_named_tool_event("write", remote.name()), StreamEvent(done=True)],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("load then write")
    approvals = 0
    async for event in Agent(provider, _deferred_registry(remote)).run(
        conv, mode=Mode.DEFAULT
    ):
        if event.approval is not None:
            approvals += 1
            event.approval.respond.set_result(Outcome.DENY_ONCE)

    assert approvals == 1
    assert remote.calls == []


@pytest.mark.asyncio
async def test_e2e_deferred_plan_mode_catalog_and_tools_are_read_only() -> None:
    read = DeferredEchoTool("mcp__demo__read")
    write = DeferredEchoTool("mcp__demo__write", read_only=False)
    provider = RequestProvider(
        [
            [_search_event("search", read.name()), StreamEvent(done=True)],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("plan with remote data")
    await _run(Agent(provider, _deferred_registry(read, write)), conv, mode=Mode.PLAN)

    assert read.name() in provider.requests[0].reminder
    assert write.name() not in provider.requests[0].reminder
    assert read.name() in [tool.name for tool in provider.requests[1].tools]
    assert write.name() not in [tool.name for tool in provider.requests[1].tools]


@pytest.mark.asyncio
async def test_e2e_deferred_discover_activate_authorize_execute() -> None:
    remote = DeferredEchoTool("mcp__demo__write", read_only=False)
    provider = RequestProvider(
        [
            [_search_event("search", remote.name()), StreamEvent(done=True)],
            [
                _named_tool_event("write", remote.name(), "authorized"),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("discover, authorize, and execute")
    approvals = 0
    async for event in Agent(provider, _deferred_registry(remote)).run(
        conv, mode=Mode.DEFAULT
    ):
        if event.approval is not None:
            approvals += 1
            event.approval.respond.set_result(Outcome.ALLOW_ONCE)

    assert approvals == 1
    assert remote.calls == [json.dumps({"value": "authorized"})]
    assert remote.name() not in [tool.name for tool in provider.requests[0].tools]
    assert remote.name() in [tool.name for tool in provider.requests[1].tools]


@pytest.mark.asyncio
async def test_deferred_activation_survives_compact_and_reset_clears_it() -> None:
    remote = DeferredEchoTool("mcp__demo__read")
    provider = RequestProvider(
        [
            [_search_event("search", remote.name()), StreamEvent(done=True)],
            [StreamEvent(text="loaded"), StreamEvent(done=True)],
            [StreamEvent(text="<summary>brief</summary>"), StreamEvent(done=True)],
            [StreamEvent(text="after compact"), StreamEvent(done=True)],
            [StreamEvent(text="after reset"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("load")
    agent = Agent(provider, _deferred_registry(remote))
    await _run(agent, conv)

    await agent.run_force_compact(conv)
    conv.add_user("continue after compact")
    await _run(agent, conv)
    assert remote.name() in [tool.name for tool in provider.requests[3].tools]

    agent.reset_deferred_tools()
    conv.add_user("continue after reset")
    await _run(agent, conv)
    assert remote.name() not in [tool.name for tool in provider.requests[4].tools]
    assert remote.name() in provider.requests[4].reminder


@pytest.mark.asyncio
async def test_e2e_without_mcp_preserves_request_shape() -> None:
    provider = RequestProvider([[StreamEvent(text="done"), StreamEvent(done=True)]])
    conv = Conversation()
    conv.add_user("local only")
    await _run(Agent(provider, _registry(EchoTool())), conv)

    request = provider.requests[0]
    assert [tool.name for tool in request.tools] == ["echo_tool"]
    assert request.reminder == ""
    assert TOOL_SEARCH_NAME not in request.system.stable


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
