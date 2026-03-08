"""Agent 层：单轮闭环编排。"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum

from endless_code.conversation import Conversation
from endless_code.llm import (
    Message,
    Provider,
    StreamEvent,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from endless_code.tool import DEFAULT_TIMEOUT, Registry


class Phase(Enum):
    START = "start"
    END = "end"


@dataclass
class ToolEvent:
    """一次工具调用的开始/结束。"""

    name: str
    args: str = ""
    phase: Phase = Phase.START
    result: str = ""
    is_error: bool = False


@dataclass
class Event:
    """单轮闭环对外事件流元素。"""

    text: str = ""
    tool: ToolEvent | None = None
    done: bool = False
    err: Exception | None = None


class Agent:
    """持有 provider 与注册中心，执行单轮闭环。"""

    def __init__(self, provider: Provider, registry: Registry) -> None:
        self._provider = provider
        self._registry = registry

    async def run(self, conv: Conversation) -> AsyncIterator[Event]:
        defs = self._registry.definitions()

        preamble = ""
        calls: list[ToolCall] = []

        async for se in self._provider.stream(conv.messages(), defs):
            if se.err is not None:
                yield Event(err=se.err)
                return
            if se.text:
                preamble += se.text
                yield Event(text=se.text)
            if se.tool_calls:
                calls = se.tool_calls

        if not calls:
            conv.add_assistant(preamble)
            yield Event(done=True)
            return

        conv.add_assistant_with_tool_calls(preamble, calls)

        results: list[ToolResult] = []
        for call in calls:
            args_preview = call.input[:80]
            yield Event(tool=ToolEvent(name=call.name, args=args_preview, phase=Phase.START))

            r = await self._registry.execute(call.name, call.input, timeout=DEFAULT_TIMEOUT)

            yield Event(
                tool=ToolEvent(
                    name=call.name,
                    args=args_preview,
                    phase=Phase.END,
                    result=r.content,
                    is_error=r.is_error,
                )
            )
            results.append(ToolResult(tool_call_id=call.id, content=r.content, is_error=r.is_error))

        conv.add_tool_results(results)

        final = ""
        async for se in self._provider.stream(conv.messages(), defs):
            if se.err is not None:
                yield Event(err=se.err)
                return
            if se.text:
                final += se.text
                yield Event(text=se.text)

        if not final:
            final = "（已完成工具调用，无额外说明）"
            yield Event(text=final)

        conv.add_assistant(final)
        yield Event(done=True)
