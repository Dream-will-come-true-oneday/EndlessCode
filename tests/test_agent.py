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
    Event,
    Mode,
    Phase,
    ToolEvent,
)
from endless_code.conversation import Conversation
from endless_code.llm import Message, StreamEvent, ToolCall, ToolDefinition, Usage
from endless_code.prompt import PLAN_MODE_REMINDER
from endless_code.tool import Registry, Result


class FakeProvider:
    def __init__(self, scripts: list[list[StreamEvent]], *, repeat_last: bool = False) -> None:
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
        index = min(self.call_count, len(self.scripts) - 1) if self.repeat_last else self.call_count
        self.call_count += 1
        for event in self.scripts[index]:
            yield event


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
            self.timeline.peak_reads = max(self.timeline.peak_reads, self.timeline.active_reads)
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


def _tool_event(call_id: str, name: str = "echo_tool", value: str = "ok") -> StreamEvent:
    return StreamEvent(
        tool_calls=[ToolCall(id=call_id, name=name, input=json.dumps({"value": value}))]
    )


async def _run(agent: Agent, conv: Conversation, **kwargs) -> list[Event]:
    return [event async for event in agent.run(conv, **kwargs)]


def test_event_model_and_constants() -> None:
    event = Event(iteration=1, usage=Usage(2, 3))
    tool = ToolEvent(call_id="c1", name="read_file")
    assert event.iteration == 1
    assert event.usage.output_tokens == 3
    assert tool.phase is Phase.START
    assert Mode.NORMAL.value == "normal"
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
            [StreamEvent(text="finished"), StreamEvent(usage=Usage(5, 2)), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("work")
    events = await _run(Agent(provider, _registry(EchoTool())), conv)
    assert [event.iteration for event in events if event.iteration] == [1, 2]
    assert [event.tool.phase for event in events if event.tool] == [Phase.START, Phase.END]
    assert [(event.usage.input_tokens, event.usage.output_tokens) for event in events if event.usage] == [(5, 2)]
    assert sum(event.done for event in events) == 1
    messages = conv.messages()
    assert [message.role for message in messages] == ["user", "assistant", "tool", "assistant"]
    assert messages[1].tool_calls[0].id == "c1"
    assert messages[2].tool_results[0].tool_call_id == "c1"
    assert messages[-1].content == "finished"
    assert [message.role for message in provider.requests[1][0]] == ["user", "assistant", "tool"]


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
    provider = FakeProvider([[_tool_event("same"), StreamEvent(done=True)]], repeat_last=True)
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
    starts = [event.tool.call_id for event in events if event.tool and event.tool.phase is Phase.START]
    ends = [event.tool.call_id for event in events if event.tool and event.tool.phase is Phase.END]
    assert starts == ["r1", "r2", "w1", "r3"]
    assert ends == starts
    results = conv.messages()[2].tool_results
    assert [result.tool_call_id for result in results] == starts


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
    task = asyncio.create_task(_run(Agent(provider, _registry(tool)), conv, cancel=cancel))
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
