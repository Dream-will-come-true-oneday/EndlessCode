"""Agent 层：可取消的 ReAct 循环、保序工具调度与权限人在回路。"""

import asyncio
import copy
import inspect
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from endless_code.checkpoint import CheckpointManager
from endless_code.checkpoint.diff import (
    BINARY_PLACEHOLDER,
    diff_stat,
    file_diff,
    truncate_diff,
)
from endless_code.compact import (
    MIN_CALIBRATED_WINDOW,
    CompactCircuitBreaker,
    ContentReplacementState,
    ContextBudget,
    ManageInput,
    RecoveryState,
    SessionContext,
    SummaryState,
    TokenMeter,
    TriggerKind,
    TrimLedger,
    appended_bytes,
    build_context_budget,
    estimate_tool_schema_tokens,
    manage_context,
    new_session_context,
    usage_anchor,
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
from endless_code.permission import (
    AuditWriter,
    Decision,
    Mode,
    Outcome,
    PermissionExplanation,
)
from endless_code.permission.audit import audit_args_summary
from endless_code.permission.engine import Engine, new_engine
from endless_code.prompt import (
    DEFAULT_STYLE_NAME,
    build_system_prompt,
    gather_environment,
    plan_reminder,
)
from endless_code.tool import Registry, Result

MAX_ITERATIONS = 25
MAX_UNKNOWN_RUN = 3
PLAN_REMINDER_INTERVAL = 4
_DIFF_TOOLS = frozenset({"write_file", "edit_file"})
NOTICE_MAX_ITER = "（已达最大迭代轮数 25，自动停止；可继续发消息推进。）"
NOTICE_UNKNOWN_TOOLS = "（连续多轮只请求到未注册的工具，自动停止。）"
NOTICE_STREAM_ERROR = "（请求出错，本轮已中断。）"
NOTICE_CANCELLED = "（已取消。）"
NOTICE_EMPTY_FINAL = "（任务已结束，模型未返回文本。）"
NOTICE_WINDOW_CLAMPED = "（请求超限，有效上下文窗口已收敛为 {} token，后续按此压缩。）"
DEFERRED_TOOLS_MESSAGE = (
    "以下 MCP 工具尚未加载；如果任务需要其中某个工具，请先使用 "
    "ToolSearch（mcp_search_tools）查询并激活：\n"
)


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
    session: SessionContext
    context_window: int = 1_000_000
    budget: ContextBudget = field(
        default_factory=lambda: build_context_budget(1_000_000)
    )
    meter: TokenMeter = field(default_factory=TokenMeter)
    summary_state: SummaryState = field(default_factory=SummaryState)
    trim_ledger: TrimLedger = field(default_factory=TrimLedger)
    clamp_notice_sent: bool = False
    usage_anchor: int = 0
    anchor_msg_len: int = 0
    run_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    turn_count: int = 0

    def refresh_budget(self, tool_defs: list[ToolDefinition]) -> ContextBudget:
        """按当前窗口与工具定义开销重建预算；窗口变更后必须调用。"""
        self.budget = build_context_budget(
            self.context_window, estimate_tool_schema_tokens(tool_defs)
        )
        return self.budget


def new_session_runtime(
    workspace: str, context_window: int = 1_000_000
) -> SessionRuntime:
    runtime = SessionRuntime(
        replacement=ContentReplacementState(),
        recovery=RecoveryState(),
        auto_tracking=CompactCircuitBreaker(),
        session=new_session_context(workspace),
        context_window=context_window,
    )
    runtime.budget = build_context_budget(context_window)
    # 新会话目录里不会有状态文件，load 的缺失分支返回全新状态。
    runtime.summary_state = SummaryState.load(runtime.session.session_dir)
    return runtime


@dataclass
class ToolEvent:
    """一次工具调用的开始或结束。"""

    call_id: str
    name: str
    args: str = ""
    phase: Phase = Phase.START
    result: str = ""
    is_error: bool = False
    diff: str = ""
    stat: str = ""


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
    explanation: PermissionExplanation | None = None
    diff: str = ""


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
        audit_writer: AuditWriter | None = None,
        checkpoint: CheckpointManager | None = None,
        output_style: str = DEFAULT_STYLE_NAME,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._version = version
        self._engine = engine or new_engine(str(Path.cwd().resolve()))[0]
        self._runtime = runtime or new_session_runtime(str(Path.cwd().resolve()))
        self._memory_manager = memory_manager
        self._instruction_text = instruction_text
        self._memory_text = memory_text
        self._audit_writer = audit_writer
        self._checkpoint = checkpoint
        self._output_style = output_style

    def set_output_style(self, style: str) -> None:
        """设置输出样式；下一轮 run 组装稳定提示时生效。"""
        self._output_style = style

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
        stable_system = build_system_prompt(
            self._instruction_text, memory_text, self._output_style
        )

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

            budget = self._runtime.refresh_budget(definitions)
            estimated = self._runtime.meter.estimate(
                self._runtime.usage_anchor,
                conv.messages(),
                self._runtime.anchor_msg_len,
            )
            likely_auto = (
                estimated >= budget.effective_auto_threshold
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
                    budget=budget,
                    tool_defs=definitions,
                    replacement=self._runtime.replacement,
                    recovery=self._runtime.recovery,
                    auto_tracking=self._runtime.auto_tracking,
                    session=self._runtime.session,
                    usage_anchor=self._runtime.usage_anchor,
                    anchor_msg_len=self._runtime.anchor_msg_len,
                    estimated_token=estimated,
                    trigger=TriggerKind.AUTO,
                    summary_state=self._runtime.summary_state,
                    trim_ledger=self._runtime.trim_ledger,
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
            if managed.compacted:
                self._runtime.meter.reset()
                self._runtime.summary_state.save(self._runtime.session.session_dir)

            emergency_retried = False
            while True:
                round_state = _RoundState()
                request = Request(
                    messages=self._request_messages(conv),
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
                clamped = self._calibrate_window(conv, definitions)
                if clamped:
                    yield Event(notice=NOTICE_WINDOW_CLAMPED.format(clamped))
                yield Event(compact=CompactEvent(CompactPhase.BEFORE_EMERGENCY))
                try:
                    emergency = await manage_context(
                        ManageInput(
                            conv=conv,
                            provider=self._provider,
                            model=self._provider.model,
                            context_window=self._runtime.context_window,
                            budget=self._runtime.budget,
                            tool_defs=definitions,
                            replacement=self._runtime.replacement,
                            recovery=self._runtime.recovery,
                            auto_tracking=self._runtime.auto_tracking,
                            session=self._runtime.session,
                            usage_anchor=self._runtime.usage_anchor,
                            anchor_msg_len=self._runtime.anchor_msg_len,
                            estimated_token=self._runtime.meter.estimate(
                                self._runtime.usage_anchor,
                                conv.messages(),
                                self._runtime.anchor_msg_len,
                            ),
                            trigger=TriggerKind.EMERGENCY,
                            summary_state=self._runtime.summary_state,
                            trim_ledger=self._runtime.trim_ledger,
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
                self._runtime.meter.reset()
                if emergency.compacted:
                    self._invalidate_summary_state()
                retry_estimate = self._runtime.meter.estimate(0, conv.messages(), 0)
                if retry_estimate >= self._runtime.budget.effective_emergency_threshold:
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
                round_state.calls, cancel, execution, mode, conv
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
        request.messages = self._request_messages(conv)
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
                except Exception as exc:  # noqa: BLE001
                    # Provider 直接抛异常（网络重置等）也必须走错误恢复路径，
                    # 不得把异常透传给调用方导致回合任务崩溃。
                    state.error = exc
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
        total = usage_anchor(usage)
        self._runtime.meter.observe(
            appended_bytes(conv.messages(), self._runtime.anchor_msg_len),
            total - self._runtime.usage_anchor,
        )
        self._runtime.usage_anchor = total
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
                self._runtime.recovery.record_read(call.id, str(path))
            except (OSError, ValueError, json.JSONDecodeError):
                continue

    def _calibrate_window(
        self, conv: Conversation, tool_defs: list[ToolDefinition]
    ) -> int:
        """请求超限时把有效窗口收敛到模型实际接受量，返回新窗口，未变化返回 0。"""
        runtime = self._runtime
        observed = runtime.meter.estimate(
            runtime.usage_anchor, conv.messages(), runtime.anchor_msg_len
        )
        clamped = max(
            MIN_CALIBRATED_WINDOW,
            min(runtime.context_window, int(observed * 0.95)),
        )
        if clamped >= runtime.context_window:
            return 0
        runtime.context_window = clamped
        runtime.refresh_budget(tool_defs)
        if runtime.clamp_notice_sent:
            return 0
        runtime.clamp_notice_sent = True
        return clamped

    def _invalidate_summary_state(self) -> None:
        """全量重摘后作废滚动状态。

        手动/紧急压缩不写滚动状态（F7），但历史已被重建，旧覆盖点会
        指向不存在的内容，留着它下轮增量会拿陈旧摘要去合并、丢新摘要。
        """
        self._runtime.summary_state = SummaryState()
        self._runtime.summary_state.save(self._runtime.session.session_dir)

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
            estimated = self._runtime.meter.estimate(
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
                    budget=self._runtime.refresh_budget(definitions),
                    tool_defs=definitions,
                    replacement=self._runtime.replacement,
                    recovery=self._runtime.recovery,
                    auto_tracking=self._runtime.auto_tracking,
                    session=self._runtime.session,
                    usage_anchor=self._runtime.usage_anchor,
                    anchor_msg_len=self._runtime.anchor_msg_len,
                    estimated_token=estimated,
                    trigger=TriggerKind.MANUAL,
                    summary_state=self._runtime.summary_state,
                    trim_ledger=self._runtime.trim_ledger,
                )
            )
            self._runtime.usage_anchor = 0
            self._runtime.anchor_msg_len = 0
            self._runtime.meter.reset()
            if result.compacted:
                self._invalidate_summary_state()
            return result.before_tokens, result.after_tokens

    async def _execute_events(
        self,
        calls: list[ToolCall],
        cancel: asyncio.Event,
        state: _ExecutionState,
        mode: Mode,
        conv: Conversation,
    ) -> AsyncIterator[Event]:
        cancel_task = asyncio.create_task(cancel.wait())
        active_tasks: set[asyncio.Task[Result]] = set()
        diffs: dict[int, tuple[str, str]] = {}
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
                        explanation = self._engine.explain(mode, call, True)
                        self._audit_permission(call, explanation)
                        if self._registry.get(call.name) is None:
                            state.results[current] = Result(
                                content=f"未知工具: {call.name}", is_error=True
                            )
                            self._audit_approval(call, explanation, "not_required")
                            continue
                        decision = explanation.decision
                        reason = explanation.reason
                        if decision is Decision.DENY:
                            state.results[current] = Result(
                                content=reason, is_error=True
                            )
                            self._audit_approval(call, explanation, "not_required")
                            continue
                        self._audit_approval(call, explanation, "not_required")
                        task = asyncio.create_task(
                            self._execute_call(call, explanation)
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
                        explanation = self._engine.explain(mode, call, False)
                        self._audit_permission(call, explanation)
                        if self._registry.get(call.name) is None:
                            state.results[current] = Result(
                                content=f"未知工具: {call.name}", is_error=True
                            )
                            self._audit_approval(call, explanation, "not_required")
                            continue
                        decision = explanation.decision
                        reason = explanation.reason
                        if decision is Decision.DENY:
                            state.results[current] = Result(
                                content=reason, is_error=True
                            )
                            self._audit_approval(call, explanation, "not_required")
                            continue
                        if decision is Decision.ALLOW:
                            self._audit_approval(call, explanation, "not_required")
                            result, diff, stat = await self._run_side_effect(
                                call, cancel, cancel_task, explanation, conv
                            )
                            if result is None:
                                state.completed = False
                                state.results[current] = Result(
                                    content=NOTICE_CANCELLED, is_error=True
                                )
                                break
                            state.results[current] = result
                            diffs[current] = (diff, stat)
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
                                explanation=explanation,
                                diff=self._preview_diff(call),
                            )
                        )
                        outcome = await respond
                        self._audit_approval(call, explanation, outcome)

                        if outcome is Outcome.ALLOW_ONCE:
                            result, diff, stat = await self._run_side_effect(
                                call, cancel, cancel_task, explanation, conv
                            )
                            if result is None:
                                state.completed = False
                                state.results[current] = Result(
                                    content=NOTICE_CANCELLED, is_error=True
                                )
                                break
                            state.results[current] = result
                            diffs[current] = (diff, stat)
                        elif outcome is Outcome.ALLOW_FOREVER:
                            try:
                                self._engine.persist_local_allow(call)
                            except Exception:
                                logging.getLogger(__name__).warning(
                                    "持久化放行规则失败: %s", call.name, exc_info=True
                                )
                            result, diff, stat = await self._run_side_effect(
                                call, cancel, cancel_task, explanation, conv
                            )
                            if result is None:
                                state.completed = False
                                state.results[current] = Result(
                                    content=NOTICE_CANCELLED, is_error=True
                                )
                                break
                            state.results[current] = result
                            diffs[current] = (diff, stat)
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
                    diff, stat = diffs.get(current, ("", ""))
                    yield Event(
                        tool=ToolEvent(
                            call_id=call.id,
                            name=call.name,
                            args=call.input,
                            phase=Phase.END,
                            result=result.content,
                            is_error=result.is_error,
                            diff=diff,
                            stat=stat,
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
        explanation: PermissionExplanation | None = None,
    ) -> Result | None:
        """执行单个有副作用工具，支持外部取消；取消返回 None。"""
        tool_task = asyncio.create_task(
            self._execute_call(
                call,
                explanation
                or self._engine.explain(
                    Mode.DEFAULT, call, self._registry.is_read_only(call.name)
                ),
            )
        )
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

    async def _execute_call(
        self, call: ToolCall, explanation: PermissionExplanation
    ) -> Result:
        """Execute a permitted call and emit start/finish audit records."""
        started = time.monotonic()
        self._audit_event("execution_started", call, explanation)
        try:
            result = await self._registry.execute(call.name, call.input)
        except asyncio.CancelledError:
            self._audit_event(
                "execution_finished",
                call,
                explanation,
                result=NOTICE_CANCELLED,
                duration_ms=(time.monotonic() - started) * 1000,
                is_error=True,
            )
            raise
        except Exception as exc:  # noqa: BLE001
            result = Result(content=f"工具 {call.name} 异常: {exc}", is_error=True)
        self._audit_event(
            "execution_finished",
            call,
            explanation,
            result=result.content,
            duration_ms=(time.monotonic() - started) * 1000,
            is_error=result.is_error,
        )
        return result

    async def _run_side_effect(
        self,
        call: ToolCall,
        cancel: asyncio.Event,
        cancel_task: asyncio.Task,
        explanation: PermissionExplanation | None,
        conv: Conversation,
    ) -> tuple[Result | None, str, str]:
        """checkpoint 创建 → 执行 → 结果 diff 计算。返回 (result, diff, stat)。"""
        path = self._call_path(call)
        exists_pre, text_pre = self._read_file_state(path)
        if self._checkpoint is not None:
            target = explanation.target if explanation is not None else call.name
            await self._checkpoint.create(call.name, target, conv.length())
        result = await self._await_serial_call(call, cancel, cancel_task, explanation)
        if result is None or result.is_error:
            return result, "", ""
        diff = self._result_diff(call.name, path, exists_pre, text_pre)
        stat = diff_stat(diff) if diff else ""
        return result, diff, stat

    def _preview_diff(self, call: ToolCall) -> str:
        """审批前计算待执行 diff；失败静默返回空串，绝不阻塞审批。"""
        if call.name not in _DIFF_TOOLS:
            return ""
        path = self._call_path(call)
        if not path:
            return ""
        try:
            data = json.loads(call.input or "{}")
        except json.JSONDecodeError:
            return ""
        exists_pre, text_pre = self._read_file_state(path)
        if call.name == "write_file":
            new = data.get("content")
            if not isinstance(new, str):
                return ""
            old = text_pre if exists_pre else None
        else:
            if not exists_pre:
                return ""
            if text_pre is None:
                return BINARY_PLACEHOLDER
            old_s = data.get("old_string")
            new_s = data.get("new_string")
            if not isinstance(old_s, str) or not isinstance(new_s, str):
                return ""
            old = text_pre
            new = text_pre.replace(old_s, new_s, 1)
        return truncate_diff(file_diff(path, old, new))

    def _result_diff(
        self, tool_name: str, path: str, exists_pre: bool, text_pre: str | None
    ) -> str:
        if tool_name not in _DIFF_TOOLS or not path:
            return ""
        exists_now, text_now = self._read_file_state(path)
        if not exists_pre and not exists_now:
            return ""
        if (exists_pre and text_pre is None) or (exists_now and text_now is None):
            return BINARY_PLACEHOLDER
        old = text_pre if exists_pre else None
        new = text_now if exists_now else None
        return truncate_diff(file_diff(path, old, new))

    @staticmethod
    def _call_path(call: ToolCall) -> str:
        try:
            data = json.loads(call.input or "{}")
        except json.JSONDecodeError:
            return ""
        path = data.get("path")
        return path if isinstance(path, str) else ""

    @staticmethod
    def _read_file_state(path: str) -> tuple[bool, str | None]:
        """返回 (文件是否存在, 文本内容)。文本为 None 表示二进制或读取失败。"""
        if not path:
            return False, None
        file_path = Path(path)
        if not file_path.exists():
            return False, None
        try:
            raw = file_path.read_bytes()
        except OSError:
            return True, None
        try:
            return True, raw.decode("utf-8")
        except UnicodeDecodeError:
            return True, None

    def _request_messages(self, conv: Conversation) -> list[Message]:
        """Build provider-only messages without mutating persisted conversation."""
        messages = copy.deepcopy(conv.messages())
        deferred_names = self._registry.deferred_mcp_names()
        if deferred_names:
            names = "\n".join(f"- {name}" for name in deferred_names)
            messages.append(
                Message(
                    role="user",
                    content=f"{DEFERRED_TOOLS_MESSAGE}{names}",
                )
            )
        return messages

    def _audit_permission(
        self, call: ToolCall, explanation: PermissionExplanation
    ) -> None:
        self._audit_event("permission_checked", call, explanation)

    def _audit_approval(
        self,
        call: ToolCall,
        explanation: PermissionExplanation,
        approval_result: object,
    ) -> None:
        self._audit_event(
            "approval_responded",
            call,
            explanation,
            approval_result=approval_result,
        )

    def _audit_event(
        self,
        event: str,
        call: ToolCall,
        explanation: PermissionExplanation,
        *,
        approval_result: object = "",
        result: object = None,
        duration_ms: float | None = None,
        is_error: bool = False,
    ) -> None:
        writer = self._audit_writer
        if writer is None:
            return
        try:
            writer.record(
                event,
                call_id=call.id,
                tool=call.name,
                target=explanation.target,
                mode=explanation.mode,
                category=explanation.category,
                read_only=explanation.read_only,
                decision=explanation.decision,
                approval_result=approval_result,
                risk_level=explanation.risk_level,
                rule_source=explanation.rule_source,
                reason=explanation.reason,
                args_summary=audit_args_summary(call.name, call.input),
                result_summary=result,
                duration_ms=duration_ms,
                is_error=is_error,
            )
        except Exception:
            logging.getLogger(__name__).warning(
                "记录权限审计失败: %s/%s", event, call.name, exc_info=True
            )

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
