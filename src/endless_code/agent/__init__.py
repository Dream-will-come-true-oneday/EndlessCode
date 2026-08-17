"""Agent 层：可取消的 ReAct 循环、保序工具调度与权限人在回路。"""

import asyncio
import inspect
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from endless_code.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    ManageInput,
    RecoveryState,
    TriggerKind,
    estimate_tokens,
    manage_context,
    new_session_context,
    usage_anchor,
)
from endless_code.compact.const import (
    AUTO_SAFETY_MARGIN,
    MANUAL_SAFETY_MARGIN,
    SUMMARY_RESERVE,
)
from endless_code.conversation import Conversation
from endless_code.llm import (
    Message,
    PromptTooLongError,
    Provider,
    Request,
    StreamEvent,
    System,
    ToolCall,
    ToolDefinition,
    ToolResult,
    Usage,
)
from endless_code.memory import Manager, has_memory_signal
from endless_code.permission import Decision, Mode, Outcome
from endless_code.permission.engine import Engine, new_engine
from endless_code.prompt import build_system_prompt, gather_environment, plan_reminder
from endless_code.tool import Registry, Result

MAX_ITERATIONS = 25
MAX_UNKNOWN_RUN = 3
PLAN_REMINDER_INTERVAL = 4
NOTICE_MAX_ITER = "（已达最大迭代轮数 25，自动停止；可继续发消息推进。）"
NOTICE_UNKNOWN_TOOLS = "（连续多轮只请求到未注册的工具，自动停止。）"
NOTICE_STREAM_ERROR = "（请求出错，本轮已中断。）"
NOTICE_CANCELLED = "（已取消。）"
NOTICE_EMPTY_FINAL = "（任务已结束，模型未返回文本。）"


class Phase(Enum):
    START = "start"
    END = "end"


class CompactPhase(Enum):
    BEFORE_AUTO = "before_auto"
    AFTER_AUTO = "after_auto"
    BEFORE_EMERGENCY = "before_emergency"
    AFTER_EMERGENCY = "after_emergency"


@dataclass
class CompactEvent:
    phase: CompactPhase
    before_tokens: int = 0
    after_tokens: int = 0
    err: Exception | None = None


@dataclass
class SessionRuntime:
    """跨用户轮次保留的上下文管理状态。"""

    replacement: ContentReplacementState
    recovery: RecoveryState
    auto_tracking: CompactCircuitBreaker
    session: object
    context_window: int = 200_000
    usage_anchor: int = 0
    anchor_msg_len: int = 0
    run_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    turn_count: int = 0


def new_session_runtime(
    workspace: str, context_window: int = 200_000
) -> SessionRuntime:
    return SessionRuntime(
        replacement=ContentReplacementState(),
        recovery=RecoveryState(),
        auto_tracking=CompactCircuitBreaker(),
        session=new_session_context(workspace),
        context_window=context_window,
    )


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
    approval: "ApprovalRequest | None" = None
    compact: CompactEvent | None = None


@dataclass
class ApprovalRequest:
    """一次待用户批准的工具调用。"""

    name: str
    args: str
    reason: str
    respond: asyncio.Future[Outcome]


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
    """持有 Provider、注册中心与权限引擎，执行完整 ReAct 循环。"""

    def __init__(
        self,
        provider: Provider,
        registry: Registry,
        version: str = "0.1.0",
        engine: Engine | None = None,
        runtime: SessionRuntime | None = None,
        memory_manager: Manager | None = None,
        instruction_text: str = "",
        memory_text: str = "",
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._version = version
        self._engine = engine or new_engine(str(Path.cwd().resolve()))[0]
        self._runtime = runtime or new_session_runtime(str(Path.cwd().resolve()))
        self._memory_manager = memory_manager
        self._instruction_text = instruction_text
        self._memory_text = memory_text

    async def run(
        self,
        conv: Conversation,
        mode: Mode = Mode.DEFAULT,
        cancel: asyncio.Event | None = None,
    ) -> AsyncIterator[Event]:
        async with self._runtime.run_lock:
            async for event in self._run(conv, mode, cancel):
                yield event

    async def _run(
        self,
        conv: Conversation,
        mode: Mode = Mode.DEFAULT,
        cancel: asyncio.Event | None = None,
    ) -> AsyncIterator[Event]:
        cancel = cancel or asyncio.Event()
        environment = await gather_environment(self._version, self._provider.model)
        memory_text = (
            self._memory_manager.load_index()
            if self._memory_manager is not None
            else self._memory_text
        )
        stable_system = build_system_prompt(self._instruction_text, memory_text)

        unknown_run = 0
        for iteration in range(1, MAX_ITERATIONS + 1):
            if cancel.is_set():
                self._append_terminal(conv, "", NOTICE_CANCELLED)
                yield Event(notice=NOTICE_CANCELLED)
                yield Event(done=True)
                return

            yield Event(iteration=iteration)
            if mode is Mode.PLAN:
                definitions = self._registry.read_only_definitions()
            else:
                definitions = self._registry.definitions()
            reminder = (
                plan_reminder((iteration - 1) % PLAN_REMINDER_INTERVAL == 0)
                if mode is Mode.PLAN
                else ""
            )

            estimated = estimate_tokens(
                self._runtime.usage_anchor,
                conv.messages(),
                self._runtime.anchor_msg_len,
            )
            auto_threshold = (
                self._runtime.context_window - SUMMARY_RESERVE - AUTO_SAFETY_MARGIN
            )
            likely_auto = (
                estimated >= auto_threshold
                and not self._runtime.auto_tracking.tripped()
            )
            if likely_auto:
                yield Event(compact=CompactEvent(CompactPhase.BEFORE_AUTO))
            managed = await manage_context(
                ManageInput(
                    conv=conv,
                    provider=self._provider,
                    model=self._provider.model,
                    context_window=self._runtime.context_window,
                    tool_defs=definitions,
                    replacement=self._runtime.replacement,
                    recovery=self._runtime.recovery,
                    auto_tracking=self._runtime.auto_tracking,
                    session=self._runtime.session,
                    usage_anchor=self._runtime.usage_anchor,
                    anchor_msg_len=self._runtime.anchor_msg_len,
                    estimated_token=estimated,
                    trigger=TriggerKind.AUTO,
                )
            )
            if likely_auto:
                yield Event(
                    compact=CompactEvent(
                        CompactPhase.AFTER_AUTO,
                        managed.before_tokens,
                        managed.after_tokens,
                        managed.err,
                    )
                )

            emergency_retried = False
            while True:
                round_state = _RoundState()
                request = Request(
                    messages=conv.messages(),
                    tools=definitions,
                    system=System(
                        stable=stable_system,
                        environment=environment.render(),
                    ),
                    reminder=reminder,
                )
                async for event in self._stream_events(
                    conv,
                    definitions,
                    request,
                    cancel,
                    round_state,
                ):
                    yield event
                if not isinstance(round_state.error, PromptTooLongError):
                    break
                if emergency_retried:
                    break
                emergency_retried = True
                yield Event(compact=CompactEvent(CompactPhase.BEFORE_EMERGENCY))
                try:
                    emergency = await manage_context(
                        ManageInput(
                            conv=conv,
                            provider=self._provider,
                            model=self._provider.model,
                            context_window=self._runtime.context_window,
                            tool_defs=definitions,
                            replacement=self._runtime.replacement,
                            recovery=self._runtime.recovery,
                            auto_tracking=self._runtime.auto_tracking,
                            session=self._runtime.session,
                            usage_anchor=self._runtime.usage_anchor,
                            anchor_msg_len=self._runtime.anchor_msg_len,
                            estimated_token=estimate_tokens(
                                self._runtime.usage_anchor,
                                conv.messages(),
                                self._runtime.anchor_msg_len,
                            ),
                            trigger=TriggerKind.EMERGENCY,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    yield Event(
                        compact=CompactEvent(
                            CompactPhase.AFTER_EMERGENCY,
                            err=exc,
                        )
                    )
                    round_state.error = exc
                    break
                yield Event(
                    compact=CompactEvent(
                        CompactPhase.AFTER_EMERGENCY,
                        emergency.before_tokens,
                        emergency.after_tokens,
                    )
                )
                self._runtime.usage_anchor = 0
                self._runtime.anchor_msg_len = 0
                retry_estimate = estimate_tokens(0, conv.messages(), 0)
                if retry_estimate >= (
                    self._runtime.context_window
                    - SUMMARY_RESERVE
                    - MANUAL_SAFETY_MARGIN
                ):
                    round_state.error = PromptTooLongError("压缩后上下文仍超过安全阈值")
                    break

            if round_state.usage is not None:
                yield Event(usage=round_state.usage)

            if round_state.cancelled:
                self._append_terminal(conv, round_state.text, NOTICE_CANCELLED)
                yield Event(notice=NOTICE_CANCELLED)
                yield Event(done=True)
                return

            if round_state.error is not None:
                yield Event(err=round_state.error)
                self._append_terminal(conv, round_state.text, NOTICE_STREAM_ERROR)
                yield Event(done=True)
                return

            if not round_state.calls:
                final = round_state.text or NOTICE_EMPTY_FINAL
                if not round_state.text:
                    yield Event(text=final)
                conv.add_assistant(final)
                self._record_usage_anchor(round_state.usage, conv)
                self._schedule_memory_update(conv)
                yield Event(done=True)
                return

            conv.add_assistant_with_tool_calls(round_state.text, round_state.calls)
            self._record_usage_anchor(round_state.usage, conv)
            if all(self._registry.get(call.name) is None for call in round_state.calls):
                unknown_run += 1
            else:
                unknown_run = 0

            execution = _ExecutionState(results=[None] * len(round_state.calls))
            async for event in self._execute_events(
                round_state.calls, cancel, execution, mode
            ):
                yield event

            tool_results = [
                ToolResult(
                    tool_call_id=call.id,
                    content=result.content,
                    is_error=result.is_error,
                )
                for call, result in zip(
                    round_state.calls, execution.results, strict=True
                )
                if result is not None
            ]
            self._record_read_files(round_state.calls, execution.results)
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
        request: Request,
        cancel: asyncio.Event,
        state: _RoundState,
    ) -> AsyncIterator[Event]:
        request.messages = conv.messages()
        request.tools = definitions
        stream = self._call_provider(request).__aiter__()
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

    def _call_provider(self, request: Request):
        """Call the Request API while tolerating legacy providers."""
        stream_fn = self._provider.stream
        try:
            legacy = len(inspect.signature(stream_fn).parameters) >= 2
        except (TypeError, ValueError):
            legacy = False
        if legacy:
            return stream_fn(request.messages, request.tools, request.reminder)
        return stream_fn(request)

    def _record_usage_anchor(self, usage: Usage | None, conv: Conversation) -> None:
        if usage is None:
            return
        self._runtime.usage_anchor = usage_anchor(usage)
        self._runtime.anchor_msg_len = conv.length()

    def _schedule_memory_update(self, conv: Conversation) -> None:
        manager = self._memory_manager
        if manager is None:
            return
        self._runtime.turn_count += 1
        recent_turn = self._recent_turn(conv.messages())
        if self._runtime.turn_count % 5 == 0 or has_memory_signal(recent_turn):
            asyncio.create_task(manager.update_async(recent_turn))

    @staticmethod
    def _recent_turn(messages: list[Message]) -> list[Message]:
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].role == "user":
                return messages[index:]
        return []

    def _record_read_files(
        self, calls: list[ToolCall], results: list[Result | None]
    ) -> None:
        for call, result in zip(calls, results, strict=True):
            if call.name != "read_file" or result is None or result.is_error:
                continue
            try:
                raw_path = json.loads(call.input or "{}").get("path")
                if not isinstance(raw_path, str) or not raw_path:
                    continue
                path = Path(raw_path)
                content = path.read_text(encoding="utf-8", errors="replace")
                self._runtime.recovery.record_file(str(path), content)
            except (OSError, ValueError, json.JSONDecodeError):
                continue

    async def run_force_compact(
        self, conv: Conversation, mode: Mode = Mode.DEFAULT
    ) -> tuple[int, int]:
        """供 TUI `/compact` 调用的无条件摘要入口。"""
        async with self._runtime.run_lock:
            definitions = (
                self._registry.read_only_definitions()
                if mode is Mode.PLAN
                else self._registry.definitions()
            )
            estimated = estimate_tokens(
                self._runtime.usage_anchor,
                conv.messages(),
                self._runtime.anchor_msg_len,
            )
            result = await manage_context(
                ManageInput(
                    conv=conv,
                    provider=self._provider,
                    model=self._provider.model,
                    context_window=self._runtime.context_window,
                    tool_defs=definitions,
                    replacement=self._runtime.replacement,
                    recovery=self._runtime.recovery,
                    auto_tracking=self._runtime.auto_tracking,
                    session=self._runtime.session,
                    usage_anchor=self._runtime.usage_anchor,
                    anchor_msg_len=self._runtime.anchor_msg_len,
                    estimated_token=estimated,
                    trigger=TriggerKind.MANUAL,
                )
            )
            self._runtime.usage_anchor = 0
            self._runtime.anchor_msg_len = 0
            return result.before_tokens, result.after_tokens

    async def _execute_events(
        self,
        calls: list[ToolCall],
        cancel: asyncio.Event,
        state: _ExecutionState,
        mode: Mode,
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
                    while end < len(calls) and self._registry.is_read_only(
                        calls[end].name
                    ):
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

                if self._registry.is_read_only(calls[index].name):
                    task_indices: dict[asyncio.Task[Result], int] = {}
                    for current in batch_indices:
                        call = calls[current]
                        if self._registry.get(call.name) is None:
                            state.results[current] = Result(
                                content=f"未知工具: {call.name}", is_error=True
                            )
                            continue
                        decision, reason = self._engine.check(mode, call, True)
                        if decision is Decision.DENY:
                            state.results[current] = Result(
                                content=reason, is_error=True
                            )
                            continue
                        task = asyncio.create_task(
                            self._registry.execute(call.name, call.input)
                        )
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
                            except Exception as exc:  # noqa: BLE001
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
                else:
                    for current in batch_indices:
                        call = calls[current]
                        if self._registry.get(call.name) is None:
                            state.results[current] = Result(
                                content=f"未知工具: {call.name}", is_error=True
                            )
                            continue
                        decision, reason = self._engine.check(mode, call, False)
                        if decision is Decision.DENY:
                            state.results[current] = Result(
                                content=reason, is_error=True
                            )
                            continue
                        if decision is Decision.ALLOW:
                            result = await self._await_serial_call(
                                call, cancel, cancel_task
                            )
                            if result is None:
                                state.completed = False
                                state.results[current] = Result(
                                    content=NOTICE_CANCELLED, is_error=True
                                )
                                break
                            state.results[current] = result
                            continue

                        respond: asyncio.Future[Outcome] = (
                            asyncio.get_running_loop().create_future()
                        )
                        yield Event(
                            approval=ApprovalRequest(
                                name=call.name,
                                args=call.input,
                                reason=reason,
                                respond=respond,
                            )
                        )
                        outcome = await respond

                        if outcome is Outcome.ALLOW_ONCE:
                            result = await self._await_serial_call(
                                call, cancel, cancel_task
                            )
                            if result is None:
                                state.completed = False
                                state.results[current] = Result(
                                    content=NOTICE_CANCELLED, is_error=True
                                )
                                break
                            state.results[current] = result
                        elif outcome is Outcome.ALLOW_FOREVER:
                            try:
                                self._engine.persist_local_allow(call)
                            except Exception:
                                logging.getLogger(__name__).warning(
                                    "持久化放行规则失败: %s", call.name, exc_info=True
                                )
                            result = await self._await_serial_call(
                                call, cancel, cancel_task
                            )
                            if result is None:
                                state.completed = False
                                state.results[current] = Result(
                                    content=NOTICE_CANCELLED, is_error=True
                                )
                                break
                            state.results[current] = result
                        else:
                            state.results[current] = Result(
                                content=reason, is_error=True
                            )

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

    async def _await_serial_call(
        self,
        call: ToolCall,
        cancel: asyncio.Event,
        cancel_task: asyncio.Task,
    ) -> Result | None:
        """执行单个有副作用工具，支持外部取消；取消返回 None。"""
        tool_task = asyncio.create_task(self._registry.execute(call.name, call.input))
        try:
            done, _ = await asyncio.wait(
                {tool_task, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_task in done:
                tool_task.cancel()
                await asyncio.gather(tool_task, return_exceptions=True)
                return None
            try:
                return tool_task.result()
            except Exception as exc:  # noqa: BLE001
                return Result(content=f"工具 {call.name} 异常: {exc}", is_error=True)
        except asyncio.CancelledError:
            tool_task.cancel()
            await asyncio.gather(tool_task, return_exceptions=True)
            raise

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
