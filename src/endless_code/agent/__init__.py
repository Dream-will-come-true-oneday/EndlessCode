"""Agent 层：可取消的 ReAct 循环与保序工具调度。"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum

from endless_code.conversation import Conversation
from endless_code.llm import (
    Provider,
    StreamEvent,
    ToolCall,
    ToolDefinition,
    ToolResult,
    Usage,
)
from endless_code.prompt import PLAN_MODE_REMINDER
from endless_code.tool import Registry, Result

MAX_ITERATIONS = 25
MAX_UNKNOWN_RUN = 3
NOTICE_MAX_ITER = "（已达最大迭代轮数 25，自动停止；可继续发消息推进。）"
NOTICE_UNKNOWN_TOOLS = "（连续多轮只请求到未注册的工具，自动停止。）"
NOTICE_STREAM_ERROR = "（请求出错，本轮已中断。）"
NOTICE_CANCELLED = "（已取消。）"
NOTICE_EMPTY_FINAL = "（任务已结束，模型未返回文本。）"


class Phase(Enum):
    START = "start"
    END = "end"


class Mode(Enum):
    NORMAL = "normal"
    PLAN = "plan"


@dataclass
class ToolEvent:
    """一次工具调用的开始或结束。"""

    call_id: str
    name: str
    args: str = ""
    phase: Phase = Phase.START
    result: str = ""
    is_error: bool = False


@dataclass
class Event:
    """Agent Loop 对外事件。"""

    text: str = ""
    tool: ToolEvent | None = None
    usage: Usage | None = None
    iteration: int | None = None
    notice: str = ""
    done: bool = False
    err: Exception | None = None


@dataclass
class _RoundState:
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    usage: Usage | None = None
    error: Exception | None = None
    cancelled: bool = False


@dataclass
class _ExecutionState:
    results: list[Result | None]
    completed: bool = True


class Agent:
    """持有 Provider 与注册中心，执行完整 ReAct 循环。"""

    def __init__(self, provider: Provider, registry: Registry) -> None:
        self._provider = provider
        self._registry = registry

    async def run(
        self,
        conv: Conversation,
        mode: Mode = Mode.NORMAL,
        cancel: asyncio.Event | None = None,
    ) -> AsyncIterator[Event]:
        cancel = cancel or asyncio.Event()
        if mode is Mode.PLAN:
            definitions = self._registry.read_only_definitions()
            system_suffix = PLAN_MODE_REMINDER
        else:
            definitions = self._registry.definitions()
            system_suffix = ""

        unknown_run = 0
        for iteration in range(1, MAX_ITERATIONS + 1):
            if cancel.is_set():
                self._append_terminal(conv, "", NOTICE_CANCELLED)
                yield Event(notice=NOTICE_CANCELLED)
                yield Event(done=True)
                return

            yield Event(iteration=iteration)
            round_state = _RoundState()
            async for event in self._stream_events(
                conv,
                definitions,
                system_suffix,
                cancel,
                round_state,
            ):
                yield event

            if round_state.usage is not None:
                yield Event(usage=round_state.usage)

            if round_state.cancelled:
                self._append_terminal(conv, round_state.text, NOTICE_CANCELLED)
                yield Event(notice=NOTICE_CANCELLED)
                yield Event(done=True)
                return

            if round_state.error is not None:
                self._append_terminal(conv, round_state.text, NOTICE_STREAM_ERROR)
                yield Event(done=True)
                return

            if not round_state.calls:
                final = round_state.text or NOTICE_EMPTY_FINAL
                if not round_state.text:
                    yield Event(text=final)
                conv.add_assistant(final)
                yield Event(done=True)
                return

            conv.add_assistant_with_tool_calls(round_state.text, round_state.calls)
            if all(self._registry.get(call.name) is None for call in round_state.calls):
                unknown_run += 1
            else:
                unknown_run = 0

            execution = _ExecutionState(results=[None] * len(round_state.calls))
            async for event in self._execute_events(round_state.calls, cancel, execution):
                yield event

            tool_results = [
                ToolResult(
                    tool_call_id=call.id,
                    content=result.content,
                    is_error=result.is_error,
                )
                for call, result in zip(round_state.calls, execution.results, strict=True)
                if result is not None
            ]
            conv.add_tool_results(tool_results)

            if not execution.completed:
                conv.add_assistant(NOTICE_CANCELLED)
                yield Event(notice=NOTICE_CANCELLED)
                yield Event(done=True)
                return

            if unknown_run >= MAX_UNKNOWN_RUN:
                conv.add_assistant(NOTICE_UNKNOWN_TOOLS)
                yield Event(notice=NOTICE_UNKNOWN_TOOLS)
                yield Event(done=True)
                return

        conv.add_assistant(NOTICE_MAX_ITER)
        yield Event(notice=NOTICE_MAX_ITER)
        yield Event(done=True)

    async def _stream_events(
        self,
        conv: Conversation,
        definitions: list[ToolDefinition],
        system_suffix: str,
        cancel: asyncio.Event,
        state: _RoundState,
    ) -> AsyncIterator[Event]:
        stream = self._provider.stream(conv.messages(), definitions, system_suffix).__aiter__()
        cancel_task = asyncio.create_task(cancel.wait())
        next_task: asyncio.Task[StreamEvent] | None = None
        try:
            while True:
                if cancel.is_set():
                    state.cancelled = True
                    return

                next_task = asyncio.create_task(anext(stream))
                done, _ = await asyncio.wait(
                    {next_task, cancel_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if cancel_task in done:
                    state.cancelled = True
                    return

                try:
                    stream_event = next_task.result()
                except StopAsyncIteration:
                    return
                finally:
                    next_task = None

                if stream_event.err is not None:
                    state.error = stream_event.err
                    yield Event(err=stream_event.err)
                    return
                if stream_event.text:
                    state.text += stream_event.text
                    yield Event(text=stream_event.text)
                if stream_event.tool_calls:
                    state.calls.extend(stream_event.tool_calls)
                if stream_event.usage is not None:
                    state.usage = stream_event.usage
                if stream_event.done:
                    return
        finally:
            if next_task is not None and not next_task.done():
                next_task.cancel()
                await asyncio.gather(next_task, return_exceptions=True)
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()

    async def _execute_events(
        self,
        calls: list[ToolCall],
        cancel: asyncio.Event,
        state: _ExecutionState,
    ) -> AsyncIterator[Event]:
        cancel_task = asyncio.create_task(cancel.wait())
        active_tasks: set[asyncio.Task[Result]] = set()
        try:
            index = 0
            while index < len(calls):
                if cancel.is_set():
                    state.completed = False
                    async for event in self._cancel_remaining(calls, state, index):
                        yield event
                    return

                end = index + 1
                if self._registry.is_read_only(calls[index].name):
                    while end < len(calls) and self._registry.is_read_only(calls[end].name):
                        end += 1

                batch_indices = list(range(index, end))
                for current in batch_indices:
                    call = calls[current]
                    yield Event(
                        tool=ToolEvent(
                            call_id=call.id,
                            name=call.name,
                            args=call.input,
                            phase=Phase.START,
                        )
                    )

                task_indices: dict[asyncio.Task[Result], int] = {}
                for current in batch_indices:
                    call = calls[current]
                    task = asyncio.create_task(self._registry.execute(call.name, call.input))
                    active_tasks.add(task)
                    task_indices[task] = current

                pending = set(task_indices)
                while pending:
                    done, _ = await asyncio.wait(
                        pending | {cancel_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    completed_tasks = done - {cancel_task}
                    for task in completed_tasks:
                        current = task_indices[task]
                        try:
                            state.results[current] = task.result()
                        except Exception as exc:
                            state.results[current] = Result(
                                content=f"工具 {calls[current].name} 异常: {exc}",
                                is_error=True,
                            )
                        pending.remove(task)
                        active_tasks.discard(task)

                    if cancel_task in done:
                        state.completed = False
                        for task in pending:
                            task.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                        active_tasks.difference_update(pending)
                        for task in pending:
                            current = task_indices[task]
                            state.results[current] = Result(
                                content=NOTICE_CANCELLED,
                                is_error=True,
                            )
                        break

                for current in batch_indices:
                    result = state.results[current]
                    if result is None:
                        result = Result(content=NOTICE_CANCELLED, is_error=True)
                        state.results[current] = result
                    call = calls[current]
                    yield Event(
                        tool=ToolEvent(
                            call_id=call.id,
                            name=call.name,
                            args=call.input,
                            phase=Phase.END,
                            result=result.content,
                            is_error=result.is_error,
                        )
                    )

                if not state.completed:
                    async for event in self._cancel_remaining(calls, state, end):
                        yield event
                    return
                index = end
        finally:
            for task in active_tasks:
                task.cancel()
            if active_tasks:
                await asyncio.gather(*active_tasks, return_exceptions=True)
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)

    async def _cancel_remaining(
        self,
        calls: list[ToolCall],
        state: _ExecutionState,
        start: int,
    ) -> AsyncIterator[Event]:
        for current in range(start, len(calls)):
            result = Result(content=NOTICE_CANCELLED, is_error=True)
            state.results[current] = result
            call = calls[current]
            yield Event(
                tool=ToolEvent(
                    call_id=call.id,
                    name=call.name,
                    args=call.input,
                    phase=Phase.END,
                    result=result.content,
                    is_error=True,
                )
            )

    @staticmethod
    def _append_terminal(conv: Conversation, partial_text: str, notice: str) -> None:
        content = f"{partial_text}\n\n{notice}" if partial_text else notice
        if conv.last_role() != "assistant":
            conv.add_assistant(content)
