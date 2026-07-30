"""agent 单测：单轮闭环 + 单轮上限。"""

import json

import pytest

from endless_code.agent import Agent, Event, Phase
from endless_code.conversation import Conversation
from endless_code.llm import Message, StreamEvent, ToolCall, ToolDefinition
from endless_code.tool import Registry, Result, new_default_registry


class FakeProvider:
    """模拟 Provider，按脚本产出 StreamEvent。"""

    def __init__(self, scripts: list[list[StreamEvent]]) -> None:
        self._scripts = scripts
        self._call_count = 0

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
        script = self._scripts[self._call_count]
        self._call_count += 1
        for ev in script:
            yield ev


class TestSingleRoundTrip:
    @pytest.mark.asyncio
    async def test_tool_call_and_final_reply(self, tmp_path) -> None:
        """AC8: 请求#1 调工具 → 执行 → 请求#2 给最终答复。"""
        f = tmp_path / "hello.txt"
        f.write_text("hello world")

        provider = FakeProvider([
            [
                StreamEvent(text="让我读一下文件"),
                StreamEvent(tool_calls=[
                    ToolCall(id="tc1", name="read_file", input=json.dumps({"path": str(f)}))
                ]),
                StreamEvent(done=True),
            ],
            [
                StreamEvent(text="文件内容是 hello world"),
                StreamEvent(done=True),
            ],
        ])

        registry = new_default_registry()
        agent = Agent(provider, registry)
        conv = Conversation()
        conv.add_user("读 hello.txt")

        events: list[Event] = []
        async for ev in agent.run(conv):
            events.append(ev)

        tool_starts = [e for e in events if e.tool and e.tool.phase == Phase.START]
        tool_ends = [e for e in events if e.tool and e.tool.phase == Phase.END]
        assert len(tool_starts) == 1
        assert len(tool_ends) == 1
        assert tool_ends[0].tool.name == "read_file"
        assert not tool_ends[0].tool.is_error

        done_events = [e for e in events if e.done]
        assert len(done_events) == 1

        msgs = conv.messages()
        assert msgs[-1].role == "assistant"
        assert "hello world" in msgs[-1].content

    @pytest.mark.asyncio
    async def test_single_round_limit(self, tmp_path) -> None:
        """AC9: 请求#2 仍返回工具调用，不再执行。"""
        f = tmp_path / "a.txt"
        f.write_text("aaa")

        provider = FakeProvider([
            [
                StreamEvent(tool_calls=[
                    ToolCall(id="tc1", name="read_file", input=json.dumps({"path": str(f)}))
                ]),
                StreamEvent(done=True),
            ],
            [
                StreamEvent(text="done"),
                StreamEvent(tool_calls=[
                    ToolCall(id="tc2", name="read_file", input=json.dumps({"path": str(f)}))
                ]),
                StreamEvent(done=True),
            ],
        ])

        registry = new_default_registry()
        agent = Agent(provider, registry)
        conv = Conversation()
        conv.add_user("test")

        events: list[Event] = []
        async for ev in agent.run(conv):
            events.append(ev)

        tool_starts = [e for e in events if e.tool and e.tool.phase == Phase.START]
        assert len(tool_starts) == 1

    @pytest.mark.asyncio
    async def test_no_tool_call(self) -> None:
        """纯文本回合不走工具。"""
        provider = FakeProvider([
            [
                StreamEvent(text="你好！"),
                StreamEvent(done=True),
            ],
        ])

        registry = new_default_registry()
        agent = Agent(provider, registry)
        conv = Conversation()
        conv.add_user("hi")

        events: list[Event] = []
        async for ev in agent.run(conv):
            events.append(ev)

        assert any(e.done for e in events)
        assert not any(e.tool for e in events)
