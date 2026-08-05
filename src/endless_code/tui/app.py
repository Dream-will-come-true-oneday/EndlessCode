"""Textual TUI：Agent 事件渲染、四档权限模式、审批交互与取消。"""

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import ClassVar

from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text as RichText
from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll
from textual.timer import Timer
from textual.widgets import Footer, Header, Input, RichLog, Static

from endless_code import __version__
from endless_code.agent import Agent, ApprovalRequest, Phase
from endless_code.config import ConfigError, ProviderConfig
from endless_code.conversation import Conversation
from endless_code.llm import Provider, new_provider
from endless_code.permission import Mode, Outcome
from endless_code.permission.engine import Engine, new_engine
from endless_code.prompt import EXECUTE_DIRECTIVE
from endless_code.security import redact_sensitive, summarize_tool_args
from endless_code.tool import Registry, new_default_registry

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
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("ctrl+d", "quit", "退出"),
        ("ctrl+c", "cancel_or_quit", "取消/退出"),
        ("escape", "cancel_turn", "取消本轮"),
        ("shift+tab", "cycle_mode", "切换权限模式"),
    ]

    def __init__(
        self,
        providers: list[ProviderConfig],
        registry: Registry | None = None,
        version: str = __version__,
        engine: Engine | None = None,
    ) -> None:
        super().__init__()
        self._providers = providers
        self._version = version
        self._provider: Provider | None = None
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
        yield Static("", id="streaming-box", classes="hidden")
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

    def _safe(self, text: object) -> str:
        return redact_sensitive(text, self._secrets)

    def _activate_provider(self, cfg: ProviderConfig) -> bool:
        try:
            self._provider = new_provider(cfg)
            secret = cfg.resolve_api_key()
            if secret:
                self._secrets.add(secret)
            return True
        except Exception as exc:  # noqa: BLE001
            self._provider = None
            self._write_error(f"{type(exc).__name__}: {exc}")
            return False

    def _update_status(self) -> None:
        provider_name = self._provider.name if self._provider else "--"
        model = self._provider.model if self._provider else "--"
        mode_label = MODE_LABELS.get(self._mode, str(self._mode))
        self.sub_title = (
            f"{mode_label} | {provider_name} | {model} | "
            f"↑{self._usage_in} ↓{self._usage_out} tok"
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
        self._chat.write(
            Panel(Markdown(self._safe(text)), title="Assistant", border_style="green")
        )

    def _write_error(self, err_msg: str) -> None:
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
        text = event.value.strip()
        self._input.clear()
        if not text:
            return
        if text in ("/exit", "/quit"):
            self.exit()
            return
        if self._state is SessionState.SELECTING:
            self._handle_select_input(text)
            return
        if self._state is not SessionState.IDLE:
            return
        self._handle_idle_input(text)

    def _handle_idle_input(self, text: str) -> None:
        if text == "/plan":
            self._mode = Mode.PLAN
            self._write_notice("已进入计划模式（只读工具）。")
            self._update_status()
            return
        if text == "/do":
            self._mode = Mode.DEFAULT
            self._write_notice("已退出计划模式，开始执行上文计划。")
            self._update_status()
            self._start_turn(EXECUTE_DIRECTIVE, display_user=False)
            return
        self._start_turn(text)

    def _start_turn(self, text: str, *, display_user: bool = True) -> None:
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
            self._write_assistant_message(text)
        else:
            self._chat.write(Markdown(self._safe(text)))

    def _render_approval(self) -> None:
        req = self.pending
        if req is None:
            return
        args = summarize_tool_args(req.name, req.args, self._secrets)
        lines = [
            f"● {req.name}({args})",
            f"  原因：{req.reason}",
            "  是否继续？",
        ]
        for idx, label in enumerate(
            ["允许本次", "永久允许（写入本地配置）", "拒绝本次"]
        ):
            prefix = "> " if idx == self.approve_cursor else "  "
            lines.append(f"{prefix}{idx + 1}. {label}")
        lines.append("  ↑↓ 选择 · Enter 确认 · Esc 取消")
        self._streaming.remove_class("hidden")
        self._streaming.update("\n".join(lines))

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
        if provider is None or cancel is None:
            self._end_turn()
            return
        agent = Agent(provider, self._tool_registry, self._version, self._engine)
        terminal = False
        try:
            async for event in agent.run(self._conv, self._mode, cancel):
                if event.approval is not None:
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
        if (
            self._state in (SessionState.STREAMING, SessionState.APPROVING)
            and self._turn_cancel is not None
        ):
            self._resolve_pending_deny()
            self._turn_cancel.set()
        else:
            self.exit()

    def action_cancel_turn(self) -> None:
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
        await super()._on_key(event)

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
        self.exit()
