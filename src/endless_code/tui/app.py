"""Textual TUI：Agent 事件渲染、四档权限模式、审批交互与取消。"""

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import ClassVar

from rich.console import Group
from rich.panel import Panel
from rich.text import Text as RichText
from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll
from textual.timer import Timer
from textual.widgets import Footer, Header, Input, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from endless_code import __version__
from endless_code.agent import (
    Agent,
    ApprovalRequest,
    CompactEvent,
    CompactPhase,
    Phase,
    SessionRuntime,
    new_session_runtime,
)
from endless_code.checkpoint import CheckpointManager, RestoreScope
from endless_code.command import (
    CommandSpec,
    Dispatcher,
    ModelOption,
    Registry,
    SessionInfo,
    StyleOption,
    SwitchResult,
    parse_command,
    register_builtin_commands,
)
from endless_code.compact import estimate_tokens, open_session_context
from endless_code.compact.const import AUTO_SAFETY_MARGIN, SUMMARY_RESERVE
from endless_code.config import ConfigError, ProviderConfig, effective_context_window
from endless_code.conversation import Conversation
from endless_code.llm import Provider, new_provider
from endless_code.memory import Manager
from endless_code.permission import AuditWriter, Mode, Outcome
from endless_code.permission.engine import Engine, new_engine
from endless_code.prompt import (
    DEFAULT_STYLE_NAME,
    EXECUTE_DIRECTIVE,
    all_styles,
    find_style,
    style_label,
)
from endless_code.security import redact_sensitive, summarize_tool_args
from endless_code.session import Writer, list_sessions, load_session
from endless_code.tool import Registry as ToolRegistry
from endless_code.tool import new_default_registry

MODE_LABELS = {
    Mode.DEFAULT: "DEFAULT",
    Mode.ACCEPT_EDITS: "ACCEPT EDITS",
    Mode.PLAN: "PLAN",
    Mode.BYPASS: "BYPASS",
}
OUTCOME_BY_INDEX = [Outcome.ALLOW_ONCE, Outcome.ALLOW_FOREVER, Outcome.DENY_ONCE]


class SessionState(Enum):
    """会话状态。"""

    SELECTING = "selecting"
    IDLE = "idle"
    STREAMING = "streaming"
    APPROVING = "approving"
    RESUMING = "resuming"
    REWINDING = "rewinding"


@dataclass
class ToolDisplay:
    call_id: str
    name: str
    args: str


def next_mode(mode: Mode) -> Mode:
    return Mode((int(mode) + 1) % 4)


class EndlessCodeApp(App):
    """多协议 LLM 终端 Agent 客户端。"""

    CSS = """
    #chat-area {
        height: 1fr;
        padding: 0 1;
    }

    #streaming-box {
        height: auto;
        min-height: 0;
        padding: 0 1;
        margin: 1 0 0 0;
        border: dashed $accent;
        color: $text;
    }

    #streaming-box.hidden {
        display: none;
    }

    #input-area {
        height: auto;
        margin: 1 0 0 0;
    }

    #input-area Input {
        width: 1fr;
    }

    #resume-list {
        height: 1fr;
        margin: 0 1;
    }

    #resume-list.hidden, #chat-area.hidden {
        display: none;
    }

#suggest-list {
        height: auto;
        max-height: 12;
        margin: 0 1;
    }

#suggest-list.hidden {
        display: none;
    }
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("ctrl+d", "quit", "退出"),
        ("ctrl+c", "cancel_or_quit", "取消/退出"),
        ("escape", "cancel_turn", "取消本轮"),
        ("shift+tab", "cycle_mode", "切换权限模式"),
        ("tab", "complete_command", "补全命令"),
    ]

    def __init__(
        self,
        providers: list[ProviderConfig],
        registry: ToolRegistry | None = None,
        version: str = __version__,
        engine: Engine | None = None,
        instruction_text: str = "",
        memory_manager: Manager | None = None,
    ) -> None:
        super().__init__()
        self._providers = providers
        self._version = version
        self._provider: Provider | None = None
        self._agent: Agent | None = None
        self._runtime: SessionRuntime | None = None
        self._tool_registry = registry or new_default_registry()
        self._conv = Conversation()
        self._state = SessionState.SELECTING
        self._engine = engine or new_engine(str(Path.cwd().resolve()))[0]
        self._mode: Mode = self._engine.start_mode()
        self._cur_reply = ""
        self._cur_tools: list[ToolDisplay] = []
        self._turn_start = 0.0
        self._iteration = 0
        self._usage_in = 0
        self._usage_out = 0
        self._stream_task: asyncio.Task | None = None
        self._turn_cancel: asyncio.Event | None = None
        self._timer: Timer | None = None
        self._secrets: set[str] = set()
        self.pending: ApprovalRequest | None = None
        self.approve_cursor: int = 0
        self._instruction_text = instruction_text
        self._memory_manager = memory_manager
        self._writer: Writer | None = None
        self._audit_writer: AuditWriter | None = None
        self._sessions_dir = Path.cwd().resolve() / ".endless-code" / "sessions"
        self._resume_sessions = []
        self._visible_resume_sessions = []
        self._last_error = ""
        self._last_reply = ""
        self._checkpoint: CheckpointManager | None = None
        self._rewind_metas: list = []
        self._rewind_selected = None
        self._rewind_phase = "list"
        self._command_registry = Registry()
        register_builtin_commands(self._command_registry)
        self._dispatcher = Dispatcher(self._command_registry, self)
        self._suggest_specs: list[CommandSpec] = []
        self._output_style: str = DEFAULT_STYLE_NAME
        for provider in providers:
            try:
                secret = provider.resolve_api_key()
            except ConfigError:
                continue
            if secret:
                self._secrets.add(secret)

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def mode(self) -> Mode:
        return self._mode

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield VerticalScroll(RichLog(id="chat-area", highlight=True, markup=True))
        yield OptionList(id="resume-list", classes="hidden")
        yield Static("", id="streaming-box", classes="hidden")
        yield OptionList(id="suggest-list", classes="hidden")
        yield Container(
            Input(placeholder="输入消息…", id="user-input"), id="input-area"
        )
        yield Footer()

    def on_mount(self) -> None:
        """启动后进入 IDLE 或 Provider 选择。"""
        if len(self._providers) == 1 and self._activate_provider(self._providers[0]):
            self._state = SessionState.IDLE
            self._write_welcome()
        else:
            self._state = SessionState.SELECTING
            self._show_provider_select()
        self._update_status()
        self._input.focus()

    @property
    def _chat(self) -> RichLog:
        return self.query_one("#chat-area", RichLog)

    @property
    def _input(self) -> Input:
        return self.query_one("#user-input", Input)

    @property
    def _streaming(self) -> Static:
        return self.query_one("#streaming-box", Static)

    @property
    def _resume_list(self) -> OptionList:
        return self.query_one("#resume-list", OptionList)

    @property
    def _suggest_list(self) -> OptionList:
        return self.query_one("#suggest-list", OptionList)

    def _safe(self, text: object) -> str:
        return redact_sensitive(text, self._secrets)

    def _activate_provider(self, cfg: ProviderConfig) -> bool:
        try:
            secret = cfg.resolve_api_key()
            if self._writer is not None:
                self._writer.close()
            if self._audit_writer is not None:
                self._audit_writer.close()
            self._provider = new_provider(cfg)
            self._runtime = new_session_runtime(
                str(Path.cwd().resolve()), effective_context_window(cfg)
            )
            self._writer = Writer(
                self._runtime.session.session_dir, self._provider.model
            )
            if secret:
                self._secrets.add(secret)
            self._audit_writer = AuditWriter(
                self._runtime.session.session_dir,
                self._runtime.session.session_id,
                secrets=self._secrets,
            )
            self._checkpoint = CheckpointManager(
                str(Path.cwd().resolve()),
                str(self._runtime.session.session_dir),
                self._runtime.session.session_id,
                audit=self._audit_writer,
            )
            self._conv = Conversation(self._writer.append, self._writer.replace)
            if self._memory_manager is not None:
                self._memory_manager.set_provider(self._provider, self._provider.model)
            self._agent = Agent(
                self._provider,
                self._tool_registry,
                self._version,
                self._engine,
                self._runtime,
                memory_manager=self._memory_manager,
                instruction_text=self._instruction_text,
                audit_writer=self._audit_writer,
                checkpoint=self._checkpoint,
                output_style=self._output_style,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            self._provider = None
            self._write_error(f"{type(exc).__name__}: {exc}")
            return False

    def _resolve_model_selector(self, selector: str) -> ProviderConfig | None:
        """把编号或名称/模型标识解析为目标配置；未命中返回 None。"""
        needle = selector.strip()
        if not needle:
            return None
        if needle.isdigit():
            index = int(needle) - 1
            if 0 <= index < len(self._providers):
                return self._providers[index]
            return None
        for cfg in self._providers:
            if needle.lower() in (cfg.name.lower(), cfg.model.lower()):
                return cfg
        return None

    def _switch_provider(self, cfg: ProviderConfig) -> SwitchResult:
        """运行期切换模型：先构造后提交，保证失败时原状态零变更。"""
        if (
            self._provider is not None
            and cfg.name == self._provider.name
            and cfg.model == self._provider.model
        ):
            return SwitchResult(
                True, f"当前已在使用 {cfg.name}（{cfg.model}）。", cfg.name, cfg.model
            )
        try:
            secret = cfg.resolve_api_key()
        except Exception as exc:  # noqa: BLE001
            return SwitchResult(
                False,
                self._safe(
                    f"模型 {cfg.name} 的 API key 不可用：{type(exc).__name__}: {exc}"
                ),
            )
        try:
            provider = new_provider(cfg)
        except Exception as exc:  # noqa: BLE001
            return SwitchResult(
                False, self._safe(f"切换失败：{type(exc).__name__}: {exc}")
            )

        old_model = self._provider.model if self._provider else ""
        self._provider = provider
        if secret:
            self._secrets.add(secret)
        self._runtime.context_window = effective_context_window(cfg)
        self._runtime.usage_anchor = 0
        self._runtime.anchor_msg_len = 0
        if self._writer is not None:
            self._writer.write_model_marker(old_model, provider.model)
        if self._memory_manager is not None:
            self._memory_manager.set_provider(provider, provider.model)
        self._agent = Agent(
            provider,
            self._tool_registry,
            self._version,
            self._engine,
            self._runtime,
            memory_manager=self._memory_manager,
            instruction_text=self._instruction_text,
            audit_writer=self._audit_writer,
            checkpoint=self._checkpoint,
            output_style=self._output_style,
        )
        self._update_status()
        return SwitchResult(
            True, f"已切换到 {cfg.name}（{provider.model}）。", cfg.name, provider.model
        )

    def _command_set_style(self, name: str) -> SwitchResult:
        """切换输出样式；下一轮请求生效，不触碰历史消息。"""
        style = find_style(name)
        if style is None:
            options = " / ".join(item.name for item in all_styles())
            return SwitchResult(False, f"未知输出样式：{name}。可选：{options}")
        if style.name == self._output_style:
            return SwitchResult(True, f"当前已是 {style.label} 样式。", style.name)
        self._output_style = style.name
        if self._agent is not None:
            self._agent.set_output_style(style.name)
        if self._writer is not None:
            self._writer.write_style_marker(style.name)
        self._update_status()
        return SwitchResult(
            True, f"已切换到 {style.label} 样式，下一轮回答生效。", style.name
        )

    def _update_status(self) -> None:
        provider_name = self._provider.name if self._provider else "--"
        model = self._provider.model if self._provider else "--"
        mode_label = MODE_LABELS.get(self._mode, str(self._mode))
        style = style_label(self._output_style)
        window = self._runtime.context_window if self._runtime else 0
        self.sub_title = (
            f"{mode_label} | {provider_name} | {model} | {style} | {window} ctx | "
            f"↑{self._usage_in} ↓{self._usage_out} tok | /help 查看命令"
        )

    def _write_welcome(self) -> None:
        self._chat.write(
            Panel(
                RichText.from_markup(
                    f"[bold]endless-code v{__version__}[/]\n"
                    "输入消息后按 Enter 提交，Shift+Tab 切换权限模式，输入 /exit 退出。"
                ),
                title="欢迎",
            )
        )

    def _write_user_message(self, text: str) -> None:
        self._chat.write(Panel(self._safe(text), title="You", border_style="blue"))

    def _write_assistant_message(self, text: str) -> None:
        self._chat.write(RichText.from_markup(f"[green]{self._safe(text)}[/]"))

    def _write_error(self, err_msg: str) -> None:
        self._last_error = err_msg
        self._chat.write(Panel(self._safe(err_msg), title="错误", border_style="red"))

    def _write_notice(self, text: str) -> None:
        self._chat.write(RichText(self._safe(text), style="dim"))

    def _show_provider_select(self) -> None:
        from rich.table import Table

        table = Table(title="选择 Provider")
        table.add_column("#", style="dim")
        table.add_column("名称")
        table.add_column("模型")
        for index, provider in enumerate(self._providers, 1):
            table.add_row(str(index), provider.name, provider.model)
        self._chat.write(table)
        self._chat.write("\n输入编号选择 provider，或输入 /exit 退出。")

    def _handle_select_input(self, text: str) -> None:
        try:
            index = int(text) - 1
        except ValueError:
            index = -1
        if 0 <= index < len(self._providers):
            cfg = self._providers[index]
            if self._activate_provider(cfg):
                self._state = SessionState.IDLE
                self._chat.write(f"\n[bold]已选择: {cfg.name} ({cfg.model})[/]\n")
                self._update_status()
                return
        self._chat.write("[bold red]请输入有效的编号[/]")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self._suggest_specs and self._state is SessionState.IDLE:
            self._accept_suggestion()
            return
        text = event.value.strip()
        self._input.clear()
        if self._state is SessionState.RESUMING:
            self._resume_selected()
            return
        if self._state is SessionState.REWINDING:
            self._rewind_select()
            return
        if not text:
            return
        if self._dispatcher.try_dispatch(text):
            return
        if self._state is SessionState.SELECTING:
            self._handle_select_input(text)
            return
        if self._state is not SessionState.IDLE:
            return
        self._handle_idle_input(text)

    def _handle_idle_input(self, text: str) -> None:
        if self._dispatcher.try_dispatch(text):
            return
        self._start_turn(text)

    def _command_plan(self) -> None:
        if self._state is not SessionState.IDLE:
            return
        self._mode = Mode.PLAN
        self._write_notice("已进入计划模式（只读工具）。")
        self._update_status()

    def _command_do(self) -> None:
        if self._state is not SessionState.IDLE:
            return
        self._mode = Mode.DEFAULT
        self._write_notice("已退出计划模式，开始执行上文计划。")
        self._update_status()
        self._start_turn(EXECUTE_DIRECTIVE, display_user=False)

    def _command_compact(self) -> None:
        if self._state is not SessionState.IDLE:
            self._write_notice("当前任务尚未结束，暂不能压缩上下文。")
            return
        if self._agent is None:
            self._write_error("尚未选择可用的 Provider。")
            return
        self._state = SessionState.STREAMING
        self._input.disabled = True
        self._streaming.remove_class("hidden")
        self._streaming.update("正在压缩上下文...")
        self._stream_task = asyncio.create_task(self._consume_force_compact())

    def _command_resume(self) -> None:
        if self._state is not SessionState.IDLE:
            self._write_notice("请等待当前任务完成后再恢复会话。")
            return
        self._resume_sessions = list_sessions(str(self._sessions_dir))
        self._visible_resume_sessions = self._resume_sessions
        if not self._resume_sessions:
            self._write_notice("没有可恢复的历史会话。")
            return
        self._state = SessionState.RESUMING
        self._chat.add_class("hidden")
        self._resume_list.remove_class("hidden")
        self._input.placeholder = "搜索会话，Enter 恢复，Esc 取消"
        self._render_resume_options("")
        self._input.focus()

    def _command_audit(self) -> None:
        """显示当前会话最近的脱敏权限审计事件。"""
        if self._state is not SessionState.IDLE:
            self._write_notice("请等待当前任务完成后再查看权限审计。")
            return
        writer = self._audit_writer
        if writer is None:
            self._write_notice("当前会话尚未建立权限审计记录。")
            return
        events = writer.recent(50)
        if not events:
            self._write_notice("当前会话暂无权限审计记录。")
            return
        lines: list[str] = []
        for item in events:
            event = self._safe(item.get("event", "unknown"))
            tool = self._safe(item.get("tool", ""))
            target = self._safe(item.get("target", ""))
            risk = self._safe(item.get("risk_level") or item.get("risk", ""))
            decision = self._safe(item.get("decision", ""))
            source = self._safe(item.get("rule_source", ""))
            approval = self._safe(item.get("approval_result", ""))
            duration = self._safe(item.get("duration_ms", ""))
            error = self._safe(item.get("is_error", False))
            lines.append(
                f"{event} | {tool} | target={target} | risk={risk} "
                f"| source={source} | decision={decision} | approval={approval} "
                f"| duration_ms={duration} | error={error}"
            )
        self._chat.write(Panel("\n".join(lines), title="权限审计"))

    # ---- CommandHost 实现：命令系统与渲染层解耦的唯一通道 ----

    def show_notice(self, text: str) -> None:
        self._write_notice(text)

    def show_error(self, text: str) -> None:
        self._write_error(text)

    def send_user_message(self, text: str) -> None:
        self._start_turn(text)

    def get_last_error(self) -> str:
        return self._last_error

    def get_last_reply(self) -> str:
        return self._last_reply

    def get_mode(self) -> Mode:
        return self._mode

    def set_mode(self, mode: Mode) -> None:
        """切换权限模式并刷新状态栏（仅空闲状态生效）。"""
        if self._state is not SessionState.IDLE:
            self._write_notice("当前任务尚未结束，暂不能切换权限模式。")
            return
        self._mode = mode
        self._write_notice(f"已切换到 {MODE_LABELS.get(mode, str(mode))} 模式")
        self._update_status()

    # ---- 模型与输出样式：命令层经此访问切换能力，均只在空闲态生效 ----

    def get_model_options(self) -> list[ModelOption]:
        current = self._provider.name if self._provider else ""
        return [
            ModelOption(index, cfg.name, cfg.model, cfg.name == current)
            for index, cfg in enumerate(self._providers, 1)
        ]

    def switch_model(self, selector: str) -> SwitchResult:
        if self._state is not SessionState.IDLE:
            return SwitchResult(False, "当前任务尚未结束，暂不能切换模型。")
        cfg = self._resolve_model_selector(selector)
        if cfg is None:
            return SwitchResult(
                False, f"未找到模型：{selector}。输入 /model 查看可用列表。"
            )
        return self._switch_provider(cfg)

    def get_style_options(self) -> list[StyleOption]:
        return [
            StyleOption(
                item.name, item.label, item.description, item.name == self._output_style
            )
            for item in all_styles()
        ]

    def set_output_style(self, name: str) -> SwitchResult:
        if self._state is not SessionState.IDLE:
            return SwitchResult(False, "当前任务尚未结束，暂不能切换输出样式。")
        return self._command_set_style(name)

    def get_session_info(self) -> SessionInfo:
        return SessionInfo(
            version=self._version,
            provider=self._provider.name if self._provider else "--",
            model=self._provider.model if self._provider else "--",
            mode=MODE_LABELS.get(self._mode, str(self._mode)),
            tokens_in=self._usage_in,
            tokens_out=self._usage_out,
            session_id=(self._runtime.session.session_id if self._runtime else "--"),
            message_count=self._conv.length(),
            output_style=style_label(self._output_style),
            context_window=(self._runtime.context_window if self._runtime else 0),
        )

    def get_memory_index(self) -> str:
        if self._memory_manager is None:
            return ""
        return self._memory_manager.load_index()

    def show_audit(self) -> None:
        self._command_audit()

    def start_compact(self) -> None:
        self._command_compact()

    def start_resume(self) -> None:
        self._command_resume()

    def start_rewind(self) -> None:
        self._command_rewind()

    def rewind_available(self) -> bool:
        return self._checkpoint is not None and self._checkpoint.available()

    def get_checkpoint_mode(self) -> str:
        return self._checkpoint.mode_label() if self._checkpoint else "--"

    def _command_rewind(self) -> None:
        if self._state is not SessionState.IDLE:
            self._write_notice("请等待当前任务完成后再回滚。")
            return
        if self._checkpoint is None:
            self._write_notice("当前会话没有可用的 checkpoint。")
            return
        metas = self._checkpoint.list()
        if not metas:
            self._write_notice(
                f"本会话尚无 checkpoint（模式：{self._checkpoint.mode_label()}）。"
            )
            return
        self._rewind_metas = metas
        self._rewind_phase = "list"
        self._rewind_selected = None
        self._state = SessionState.REWINDING
        self._chat.add_class("hidden")
        self._resume_list.remove_class("hidden")
        self._input.placeholder = "↑↓ 选择检查点，Enter 确认，Esc 取消"
        self._render_rewind_list()
        self._input.focus()

    def _render_rewind_list(self) -> None:
        options = []
        for meta in self._rewind_metas:
            time_str = (
                datetime.fromtimestamp(meta.timestamp, tz=UTC)
                .astimezone()
                .strftime("%m-%d %H:%M:%S")
            )
            target = self._safe(meta.target)
            options.append(
                Option(
                    f"#{meta.index} · {time_str} · {meta.tool} · {target}\n"
                    f"        对话位置：{meta.conv_len} 条消息"
                )
            )
        options.append(Option("清理本会话全部 checkpoint"))
        self._resume_list.clear_options()
        self._resume_list.add_options(options)
        self._resume_list.highlighted = 0

    def _render_rewind_scope(self) -> None:
        meta = self._rewind_selected
        options = [
            Option("仅恢复文件（对话保留，AI 会收到回滚通知）"),
            Option("仅恢复对话历史（文件保持当前状态）"),
            Option("文件与对话都恢复"),
            Option("取消"),
        ]
        self._resume_list.clear_options()
        self._resume_list.add_options(options)
        self._resume_list.highlighted = 0
        if meta is not None:
            self._write_notice(
                f"已选择 checkpoint #{meta.index}（{meta.tool} · {self._safe(meta.target)}），选择恢复范围："
            )

    def _rewind_select(self) -> None:
        index = self._resume_list.highlighted
        if index is None or index < 0:
            return
        if self._rewind_phase == "list":
            if index >= len(self._rewind_metas):
                self._stream_task = asyncio.create_task(self._run_rewind_cleanup())
                return
            self._rewind_selected = self._rewind_metas[index]
            self._rewind_phase = "scope"
            self._render_rewind_scope()
            return
        meta = self._rewind_selected
        scope_by_index = [
            RestoreScope.FILES_ONLY,
            RestoreScope.CONVERSATION_ONLY,
            RestoreScope.BOTH,
            None,
        ]
        scope = scope_by_index[index] if index < len(scope_by_index) else None
        if scope is None or meta is None:
            self._close_rewind()
            return
        self._stream_task = asyncio.create_task(self._execute_rewind(meta, scope))

    async def _run_rewind_cleanup(self) -> None:
        try:
            if self._checkpoint is not None:
                await self._checkpoint.clear()
            self._write_notice("已清理本会话全部 checkpoint。")
        except Exception as exc:  # noqa: BLE001
            self._write_error(f"清理失败: {type(exc).__name__}: {exc}")
        finally:
            self._close_rewind()

    async def _execute_rewind(self, meta, scope: RestoreScope) -> None:
        try:
            self._input.disabled = True
            self._streaming.remove_class("hidden")
            self._streaming.update("正在回滚...")
            if self._checkpoint is None:
                self._write_error("checkpoint 不可用。")
                return
            report = await self._checkpoint.restore(meta, scope)
            if not report.ok:
                self._write_error(f"回滚失败: {report.error}")
                return
            if scope is RestoreScope.CONVERSATION_ONLY:
                self._truncate_conversation(meta)
                self._write_notice(
                    f"对话已回滚到 checkpoint #{meta.index}（文件保持当前状态）。"
                )
            elif scope is RestoreScope.BOTH:
                self._truncate_conversation(meta)
                self._write_notice(
                    f"文件与对话已回滚到 checkpoint #{meta.index}"
                    f"（覆盖 {len(report.restored_files)} 个文件、删除 {len(report.deleted_files)} 个）。"
                )
            else:
                self._conv.add_user(
                    f"[系统提示] 工作区文件已回滚到 checkpoint #{meta.index}，"
                    "相关文件内容可能已变化，请重新读取后再修改。"
                )
                self._write_notice(
                    f"文件已回滚到 checkpoint #{meta.index}"
                    f"（覆盖 {len(report.restored_files)} 个文件、删除 {len(report.deleted_files)} 个）。"
                    "AI 已收到回滚通知。"
                )
        except Exception as exc:  # noqa: BLE001
            self._write_error(f"回滚失败: {type(exc).__name__}: {exc}")
        finally:
            self._close_rewind()

    def _truncate_conversation(self, meta) -> None:
        messages = self._conv.messages()[: meta.conv_len]
        self._conv.replace_history(messages)

    def _close_rewind(self) -> None:
        self._resume_list.add_class("hidden")
        self._chat.remove_class("hidden")
        self._input.placeholder = "输入消息…"
        self._input.disabled = False
        self._input.focus()
        self._streaming.add_class("hidden")
        self._streaming.update("")
        self._state = SessionState.IDLE
        self._rewind_phase = "list"
        self._rewind_selected = None
        self._rewind_metas = []
        self._stream_task = None

    def enter_plan(self) -> None:
        self._command_plan()

    def start_execute(self) -> None:
        self._command_do()

    def clear_session(self) -> None:
        """清空当前会话：重置内存对话并截断会话持久化文件。"""
        if self._state is not SessionState.IDLE:
            self._write_notice("当前任务尚未结束，暂不能清空会话。")
            return
        if self._writer is None:
            self._conv = Conversation()
            self._write_notice("已清空当前会话。")
            return
        self._writer.close()
        with self._writer.path.open("w", encoding="utf-8"):
            pass
        model = self._provider.model if self._provider else ""
        self._writer = Writer(str(self._writer.session_dir), model)
        self._conv = Conversation(self._writer.append, self._writer.replace)
        self._write_notice("已清空当前会话。")

    def quit_app(self) -> None:
        self.exit()

    def action_complete_command(self) -> None:
        """Tab 补全：唯一匹配写回输入框，多匹配展示候选。"""
        value = self._input.value
        if self._state in (SessionState.SELECTING, SessionState.RESUMING):
            return
        if not value.startswith("/"):
            return
        text, candidates = self._dispatcher.complete(value)
        if candidates:
            self._write_notice("候选命令：" + " ".join(candidates))
        if text != value:
            self._input.value = text
            self._input.cursor_position = len(text)

    def _update_suggestions(self, value: str) -> None:
        """根据输入实时刷新命令建议面板。"""
        if self._state is not SessionState.IDLE or not value.startswith("/"):
            self._hide_suggestions()
            return
        parsed = parse_command(value)
        if parsed is None or parsed.args:
            self._hide_suggestions()
            return
        specs = self._command_registry.suggest(parsed.name)
        if not specs:
            self._hide_suggestions()
            return
        self._suggest_specs = specs
        options = [
            Option(
                f"{spec.name}  {' '.join(spec.aliases)}  — {spec.description}"
                if spec.aliases
                else f"{spec.name}  — {spec.description}"
            )
            for spec in specs
        ]
        self._suggest_list.clear_options()
        self._suggest_list.add_options(options)
        self._suggest_list.highlighted = 0
        self._suggest_list.remove_class("hidden")

    def _hide_suggestions(self) -> None:
        self._suggest_specs = []
        self._suggest_list.add_class("hidden")
        self._suggest_list.clear_options()

    def _accept_suggestion(self) -> None:
        """执行高亮候选命令并收起建议面板。"""
        if not self._suggest_specs:
            return
        index = self._suggest_list.highlighted
        if index is None or index < 0 or index >= len(self._suggest_specs):
            self._hide_suggestions()
            return
        spec = self._suggest_specs[index]
        self._hide_suggestions()
        self._input.value = ""
        self._dispatcher.try_dispatch(spec.name)

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._state is SessionState.RESUMING:
            self._render_resume_options(event.value.strip())
        else:
            self._update_suggestions(event.value)

    def _render_resume_options(self, query: str) -> None:
        needle = query.lower()
        self._visible_resume_sessions = [
            item
            for item in self._resume_sessions
            if needle in item.title.lower() or needle in item.model.lower()
        ]
        self._resume_list.clear_options()
        self._resume_list.add_options(
            Option(
                f"{item.title}\n{_relative_time(item.modified_at)} | {item.model} | {_format_size(item.size)}"
            )
            for item in self._visible_resume_sessions
        )
        if self._visible_resume_sessions:
            self._resume_list.highlighted = 0
        else:
            self._write_notice("没有匹配的历史会话。")

    def _resume_selected(self) -> None:
        index = self._resume_list.highlighted
        if index is None or index < 0 or index >= len(self._visible_resume_sessions):
            self._write_notice("请先选择一个会话。")
            return
        self._input.disabled = True
        self._streaming.remove_class("hidden")
        self._streaming.update("正在恢复会话...")
        self._stream_task = asyncio.create_task(
            self._restore_session(self._visible_resume_sessions[index])
        )

    async def _restore_session(self, info) -> None:
        try:
            loaded = load_session(info.dir)
            old_writer = self._writer
            if old_writer is not None:
                old_writer.close()
            if self._audit_writer is not None:
                self._audit_writer.close()
            self._writer = Writer.open_existing(
                info.dir, self._provider.model if self._provider else ""
            )
            self._conv = Conversation.from_messages(
                loaded.messages, self._writer.append, self._writer.replace
            )
            if self._runtime is not None:
                self._runtime.session = open_session_context(
                    str(Path.cwd().resolve()), info.id
                )
                self._audit_writer = AuditWriter(
                    self._runtime.session.session_dir,
                    self._runtime.session.session_id,
                    secrets=self._secrets,
                )
                self._checkpoint = CheckpointManager(
                    str(Path.cwd().resolve()),
                    str(self._runtime.session.session_dir),
                    self._runtime.session.session_id,
                    audit=self._audit_writer,
                )
                if self._agent is not None:
                    self._agent._audit_writer = self._audit_writer
                    self._agent._checkpoint = self._checkpoint
                threshold = (
                    self._runtime.context_window - SUMMARY_RESERVE - AUTO_SAFETY_MARGIN
                )
                if (
                    self._agent is not None
                    and estimate_tokens(0, loaded.messages, 0) >= threshold
                ):
                    try:
                        await self._agent.run_force_compact(self._conv, self._mode)
                    except Exception:  # noqa: BLE001
                        self._write_notice("恢复会话时压缩失败，已保留原始历史。")
            if loaded.last_timestamp is not None:
                elapsed = int(time.time()) - loaded.last_timestamp
                if elapsed > 6 * 3600:
                    self._conv.add_user(
                        "[系统提示] 本会话已暂停 "
                        f"{_format_duration(elapsed)}。部分上下文可能已过时，如需最新信息请重新读取相关文件。"
                    )
            self._write_notice(f"已恢复会话 {info.id}，共 {self._conv.length()} 条消息")
        except Exception as exc:  # noqa: BLE001
            self._write_error(f"恢复会话失败: {type(exc).__name__}: {exc}")
        finally:
            self._close_resume()

    def _close_resume(self) -> None:
        self._resume_list.add_class("hidden")
        self._chat.remove_class("hidden")
        self._input.placeholder = "输入消息…"
        self._input.disabled = False
        self._input.focus()
        self._streaming.add_class("hidden")
        self._streaming.update("")
        self._state = SessionState.IDLE
        self._stream_task = None

    def _cancel_resume(self) -> None:
        if self._state is SessionState.RESUMING:
            self._close_resume()

    def _start_turn(self, text: str, *, display_user: bool = True) -> None:
        self._hide_suggestions()
        if self._provider is None:
            self._write_error("尚未选择可用的 Provider。")
            return
        self._conv.add_user(text)
        if display_user:
            self._write_user_message(text)

        self._cur_reply = ""
        self._cur_tools.clear()
        self._turn_start = time.monotonic()
        self._iteration = 0
        self._state = SessionState.STREAMING
        self._input.disabled = True
        self._streaming.remove_class("hidden")
        self._streaming.update("  Imagining…")
        self._timer = self.set_interval(0.25, self._tick)
        self._turn_cancel = asyncio.Event()
        self._stream_task = asyncio.create_task(self._consume_agent_events())

    def _tick(self) -> None:
        elapsed = time.monotonic() - self._turn_start
        if self._state is SessionState.APPROVING:
            return
        if self._cur_tools:
            lines = [f"● {tool.name}({tool.args}) Running…" for tool in self._cur_tools]
            self._streaming.update(
                "\n".join(lines) + f"\n  ({elapsed:.1f}s · 第 {self._iteration} 轮)"
            )
        else:
            iteration = f" · 第 {self._iteration} 轮" if self._iteration else ""
            self._streaming.update(f"  Imagining… ({elapsed:.1f}s{iteration})")

    def _flush_reply(self, *, final: bool = False) -> None:
        if not self._cur_reply:
            return
        text = self._cur_reply
        self._cur_reply = ""
        if final:
            self._last_reply = text
            self._write_assistant_message(text)
        else:
            # Use plain text with ANSI formatting for selectable output
            self._chat.write(RichText.from_markup(f"[green]{self._safe(text)}[/]"))

    def _styled_diff(self, diff: str, max_lines: int = 60) -> RichText:
        """diff 逐行着色（保持文本可选中复制）。"""
        text = RichText()
        for line in diff.splitlines()[:max_lines]:
            if line.startswith(("+++", "---")):
                text.append(line + "\n", style="bold")
            elif line.startswith("@@"):
                text.append(line + "\n", style="yellow")
            elif line.startswith("+"):
                text.append(line + "\n", style="green")
            elif line.startswith("-"):
                text.append(line + "\n", style="red")
            else:
                text.append(line + "\n")
        return text

    def _render_approval(self) -> None:
        req = self.pending
        if req is None:
            return
        args = summarize_tool_args(req.name, req.args, self._secrets)
        lines = [
            f"● {req.name}({args})",
            f"  原因：{req.reason}",
        ]
        explanation = req.explanation
        if explanation is not None:
            lines.extend(
                [
                    f"  风险：{explanation.risk_level.value}",
                    f"  目标：{self._safe(explanation.target)}",
                    f"  规则来源：{self._safe(explanation.rule_source)}",
                    f"  影响：{self._safe(explanation.impact)}",
                ]
            )
        lines.append("  是否继续？")
        for idx, label in enumerate(
            ["允许本次", "永久允许（写入本地配置）", "拒绝本次"]
        ):
            prefix = "> " if idx == self.approve_cursor else "  "
            lines.append(f"{prefix}{idx + 1}. {label}")
        lines.append("  ↑↓ 选择 · Enter 确认 · Esc 取消")
        parts: list = [RichText("\n".join(lines))]
        if req.diff:
            parts.append(RichText("  变更预览：", style="bold"))
            parts.append(self._styled_diff("    " + req.diff.replace("\n", "\n    ")))
        self._streaming.remove_class("hidden")
        self._streaming.update(Group(*parts))

    def _update_approving(self, key: str) -> None:
        if self.pending is None or self._state is not SessionState.APPROVING:
            return
        index: int | None = None
        if key in ("up", "k"):
            self.approve_cursor = (self.approve_cursor - 1) % 3
            self._render_approval()
            return
        if key in ("down", "j"):
            self.approve_cursor = (self.approve_cursor + 1) % 3
            self._render_approval()
            return
        if key in ("enter", "space"):
            index = self.approve_cursor
        elif key in ("1", "2", "3"):
            index = int(key) - 1
        elif key == "y":
            index = 0
        elif key in ("n", "d"):
            index = 2
        if index is None:
            return
        self._submit_approval(index)

    def _submit_approval(self, index: int) -> None:
        req = self.pending
        if req is None:
            return
        outcome = OUTCOME_BY_INDEX[index]
        self._state = SessionState.STREAMING
        self.pending = None
        self.approve_cursor = 0
        if not req.respond.done():
            req.respond.set_result(outcome)
        self._tick()

    def update_approving(self, key: str) -> None:
        """对外暴露的待批准按键入口，转发给内部实现。"""
        self._update_approving(key)

    def _resolve_pending_deny(self) -> None:
        req = self.pending
        if req is None:
            return
        self.pending = None
        self.approve_cursor = 0
        if not req.respond.done():
            req.respond.set_result(Outcome.DENY_ONCE)

    async def _consume_agent_events(self) -> None:
        provider = self._provider
        cancel = self._turn_cancel
        agent = self._agent
        if provider is None or cancel is None or agent is None:
            self._end_turn()
            return
        terminal = False
        try:
            async for event in agent.run(self._conv, self._mode, cancel):
                if event.compact is not None:
                    self._flush_reply()
                    self._write_notice(format_compact_notice(event.compact))
                elif event.approval is not None:
                    self._flush_reply()
                    self.pending = event.approval
                    self.approve_cursor = 0
                    self._state = SessionState.APPROVING
                    self._render_approval()
                    continue
                if event.text:
                    self._cur_reply += event.text
                elif event.tool is not None:
                    self._handle_tool_event(event.tool)
                elif event.usage is not None:
                    self._usage_in += event.usage.input_tokens
                    self._usage_out += event.usage.output_tokens
                    self._update_status()
                elif event.iteration is not None:
                    self._iteration = event.iteration
                    self._tick()
                elif event.notice:
                    self._flush_reply()
                    self._write_notice(event.notice)
                elif event.err is not None:
                    self._flush_reply()
                    self._write_error(f"{type(event.err).__name__}: {event.err}")
                elif event.done:
                    terminal = True
                    self._flush_reply(final=True)
                    self._end_turn()
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._flush_reply()
            self._write_error(f"{type(exc).__name__}: {exc}")
        finally:
            if not terminal and self._state in (
                SessionState.STREAMING,
                SessionState.APPROVING,
            ):
                self._end_turn()

    async def _consume_force_compact(self) -> None:
        agent = self._agent
        if agent is None:
            self._end_turn()
            return
        try:
            before, after = await agent.run_force_compact(self._conv, self._mode)
            self._write_notice(
                format_compact_notice(
                    CompactEvent(CompactPhase.AFTER_AUTO, before, after)
                )
            )
        except Exception as exc:  # noqa: BLE001
            self._write_error(f"压缩失败: {type(exc).__name__}: {exc}")
        finally:
            self._end_turn()

    def _handle_tool_event(self, event) -> None:
        args = summarize_tool_args(event.name, event.args, self._secrets)
        if event.phase is Phase.START:
            self._flush_reply()
            self._cur_tools.append(ToolDisplay(event.call_id, event.name, args))
            self._tick()
            return

        self._cur_tools = [
            tool for tool in self._cur_tools if tool.call_id != event.call_id
        ]
        self._chat.write(RichText(f"● {event.name}({args})", style="bold cyan"))
        summary = self._safe(event.result)
        lines = summary.splitlines()
        if len(lines) > 8:
            summary = "\n".join(lines[:8]) + "\n[truncated]"
        style = "red" if event.is_error else "dim"
        self._chat.write(RichText(f"  ⎿ {summary}", style=style))
        if event.diff and not event.is_error:
            self._chat.write(RichText(f"  ⎿ {event.stat}", style="cyan"))
            self._chat.write(
                self._styled_diff("    " + event.diff.replace("\n", "\n    "))
            )
        self._tick()

    def _end_turn(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._streaming.add_class("hidden")
        self._streaming.update("")
        self._cur_reply = ""
        self._cur_tools.clear()
        self._iteration = 0
        if self._state in (SessionState.STREAMING, SessionState.APPROVING):
            self._resolve_pending_deny()
        self._state = SessionState.IDLE
        self._input.disabled = False
        self._input.focus()
        self._turn_cancel = None
        self._stream_task = None

    def action_cancel_or_quit(self) -> None:
        if self._state is SessionState.RESUMING:
            self._cancel_resume()
            return
        if self._state is SessionState.REWINDING:
            self._close_rewind()
            return
        if (
            self._state in (SessionState.STREAMING, SessionState.APPROVING)
            and self._turn_cancel is not None
        ):
            self._resolve_pending_deny()
            self._turn_cancel.set()
        else:
            self.exit()

    def action_cancel_turn(self) -> None:
        if self._state is SessionState.RESUMING:
            self._cancel_resume()
            return
        if self._state is SessionState.REWINDING:
            self._close_rewind()
            return
        if (
            self._state in (SessionState.STREAMING, SessionState.APPROVING)
            and self._turn_cancel is not None
        ):
            self._resolve_pending_deny()
            self._turn_cancel.set()

    def action_cycle_mode(self) -> None:
        if self._state is not SessionState.IDLE:
            return
        self._mode = next_mode(self._mode)
        self._write_notice(f"已切换到 {MODE_LABELS.get(self._mode)} 模式")
        self._update_status()

    async def _on_key(self, event) -> None:
        if self._state is SessionState.RESUMING:
            if event.key == "escape":
                self._cancel_resume()
                event.stop()
                return
            if event.key in ("up", "k"):
                self._resume_list.action_cursor_up()
                event.stop()
                return
            if event.key in ("down", "j"):
                self._resume_list.action_cursor_down()
                event.stop()
                return
        if self._state is SessionState.REWINDING:
            if event.key == "escape":
                self._close_rewind()
                event.stop()
                return
            if event.key in ("up", "k"):
                self._resume_list.action_cursor_up()
                event.stop()
                return
            if event.key in ("down", "j"):
                self._resume_list.action_cursor_down()
                event.stop()
                return
            # 注意：Enter 不在这里处理——Input 会先消费 Enter 并发出 Submitted，
            # 由 on_input_submitted 的 REWINDING 分支调用 _rewind_select，
            # 与 RESUMING 的单路分发模式保持一致，避免双重选择。
        if self._state is SessionState.APPROVING:
            key = event.key
            if key in (
                "up",
                "down",
                "k",
                "j",
                "enter",
                "space",
                "1",
                "2",
                "3",
                "y",
                "n",
                "d",
                "escape",
                "ctrl+c",
            ):
                if key in ("escape", "ctrl+c"):
                    self._resolve_pending_deny()
                    if self._turn_cancel is not None:
                        self._turn_cancel.set()
                else:
                    self._update_approving(key)
                event.stop()
                return
        if self._suggest_specs and self._state is SessionState.IDLE:
            if event.key in ("up", "down"):
                if event.key == "up":
                    self._suggest_list.action_cursor_up()
                else:
                    self._suggest_list.action_cursor_down()
                event.stop()
                return
            if event.key == "escape":
                self._hide_suggestions()
                event.stop()
                return
        # 注意：这里不能调用 super()._on_key(event)。Textual 的 _get_dispatch_methods
        # 会沿 MRO 单独分派基类 App._on_key（负责 _check_bindings 绑定与 dispatch_key），
        # 子类再调一次会让绑定动作执行两遍（例如退格一次删两个字符）。
        # 审批键已在上方处理并 stop；其余键交给基类 App._on_key 正常处理。

    async def action_quit(self) -> None:
        task = self._stream_task
        if task is not None and not task.done():
            if self._turn_cancel is not None:
                self._turn_cancel.set()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        if self._writer is not None:
            self._writer.close()
        if self._audit_writer is not None:
            self._audit_writer.close()
        self.exit()

    def on_unmount(self) -> None:
        if self._writer is not None:
            self._writer.close()
        if self._audit_writer is not None:
            self._audit_writer.close()


def format_compact_notice(event: CompactEvent) -> str:
    """统一格式化自动、紧急与手动压缩提示。"""
    if event.phase is CompactPhase.BEFORE_AUTO:
        return "正在压缩上下文..."
    if event.phase is CompactPhase.BEFORE_EMERGENCY:
        return "上下文撞墙，自动压缩中..."
    if event.err is not None:
        return f"压缩失败: {type(event.err).__name__}: {event.err}"
    return f"已压缩，token 从 {event.before_tokens} 降至 {event.after_tokens}"


def _relative_time(value: datetime) -> str:
    seconds = max(0, int((datetime.now().astimezone() - value).total_seconds()))
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60} 分钟前"
    if seconds < 86400:
        return f"{seconds // 3600} 小时前"
    return f"{seconds // 86400} 天前"


def _format_duration(seconds: int) -> str:
    if seconds < 86400:
        return f"{seconds // 3600} 小时"
    return f"{seconds // 86400} 天"


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"
