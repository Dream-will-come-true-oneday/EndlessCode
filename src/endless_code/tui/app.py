"""TUI App —— EndlessCodeApp 主类、状态机、对话逻辑。"""

import asyncio
import time
from enum import Enum

from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text as RichText
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import Footer, Header, Input, RichLog, Static
from textual.timer import Timer

from endless_code.config import ProviderConfig
from endless_code.conversation import Conversation
from endless_code.llm import Message, Provider, StreamEvent, new_provider


class SessionState(Enum):
    """会话状态。"""

    SELECTING = "selecting"
    IDLE = "idle"
    STREAMING = "streaming"


class EndlessCodeApp(App):
    """多协议 LLM 终端对话客户端。"""

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

    .error-text {
        color: $error;
    }
    """

    BINDINGS = [
        ("ctrl+d", "quit", "退出"),
    ]

    def __init__(
        self,
        providers: list[ProviderConfig],
    ) -> None:
        super().__init__()
        self._providers = providers
        self._provider: Provider | None = None
        self._conv = Conversation()
        self._state = SessionState.SELECTING
        self._cur_reply: str = ""
        self._turn_start: float = 0.0
        self._stream_task: asyncio.Task | None = None
        self._timer: Timer | None = None

    @property
    def state(self) -> SessionState:
        return self._state

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield VerticalScroll(RichLog(id="chat-area", highlight=True, markup=True))
        yield Static("", id="streaming-box", classes="hidden")
        yield Container(
            Input(placeholder="输入消息…", id="user-input"),
            id="input-area",
        )
        yield Footer()

    def on_mount(self) -> None:
        """启动后进入 IDLE 或 SELECTING。"""
        if len(self._providers) == 1:
            self._provider = new_provider(self._providers[0])
            self._update_footer()
            self._state = SessionState.IDLE
            self._write_welcome()
        else:
            self._state = SessionState.SELECTING
            self._show_provider_select()
        self._input.focus()

    # ─── UI helpers ──────────────────────────────────────────────

    @property
    def _chat(self) -> RichLog:
        return self.query_one("#chat-area", RichLog)

    @property
    def _input(self) -> Input:
        return self.query_one("#user-input", Input)

    @property
    def _streaming(self) -> Static:
        return self.query_one("#streaming-box", Static)

    def _update_footer(self) -> None:
        if self._provider:
            pname = self._provider.name
            pmodel = self._provider.model
            self.query_one(Footer).highlight_key = ""
        else:
            pname = "--"
            pmodel = "--"

        self.sub_title = f"{pname} — {pmodel}"

    def _write_welcome(self) -> None:
        self._chat.write(
            Panel(
                RichText.from_markup(
                    "[bold]endless-code v0.1.0[/]\n"
                    "输入消息后按 Enter 提交，输入 /exit 退出。"
                ),
                title="欢迎",
            )
        )

    def _write_user_message(self, text: str) -> None:
        self._chat.write(
            Panel(text, title="You", border_style="blue")
        )

    def _write_assistant_message(self, text: str) -> None:
        self._chat.write(
            Panel(Markdown(text), title="Assistant", border_style="green")
        )

    def _write_error(self, err_msg: str) -> None:
        self._chat.write(
            Panel(err_msg, title="错误", border_style="red")
        )

    # ─── T12: Provider select ────────────────────────────────────

    def _show_provider_select(self) -> None:
        from rich.table import Table

        table = Table(title="选择 Provider")
        table.add_column("#", style="dim")
        table.add_column("名称")
        table.add_column("模型")
        for i, p in enumerate(self._providers, 1):
            table.add_row(str(i), p.name, p.model)

        self._chat.write(table)
        self._chat.write("\n输入编号选择 provider，或输入 /exit 退出。")

    def _handle_select_input(self, text: str) -> None:
        """T12: 处理 SELECTING 状态下的输入（provider 编号选择）。"""
        try:
            idx = int(text) - 1
            if 0 <= idx < len(self._providers):
                cfg = self._providers[idx]
                self._provider = new_provider(cfg)
                self._update_footer()
                self._state = SessionState.IDLE
                self._chat.write(
                    f"\n[bold]已选择: {cfg.name} ({cfg.model})[/]\n"
                )
                return
        except ValueError:
            pass
        self._chat.write("[bold red]请输入有效的编号[/]")

    # ─── T13: Input handling ─────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """T13: 处理用户提交的文本。"""
        if not event.value:
            return

        text = event.value.strip()
        self._input.clear()

        if text in ("/exit", "/quit"):
            self.exit()
            return

        if self._state == SessionState.SELECTING:
            self._handle_select_input(text)
            return

        if self._state == SessionState.STREAMING:
            return  # 流式中忽略输入

        if self._state == SessionState.IDLE:
            self._start_turn(text)

    # ─── T13-T15: Turn lifecycle ─────────────────────────────────

    def _start_turn(self, text: str) -> None:
        """T13: 开始一轮对话。"""
        self._conv.add_user(text)
        self._write_user_message(text)

        self._cur_reply = ""
        self._turn_start = time.monotonic()
        self._state = SessionState.STREAMING
        self._input.disabled = True

        # 显示 streaming 区域占位
        self._streaming.remove_class("hidden")
        self._streaming.update("  …")

        # T15: 启动计时器
        self._timer = self.set_interval(0.25, self._tick)

        self._stream_task = asyncio.create_task(self._consume_stream())

    def _tick(self) -> None:
        """T15: 更新计时显示。"""
        elapsed = time.monotonic() - self._turn_start
        self._streaming.update(
            f"  Imagining… ({elapsed:.1f}s)\n\n  {self._cur_reply}"
        )

    @work(exclusive=True)
    async def _consume_stream(self) -> None:
        """T14: 消费 provider 流式输出。"""
        try:
            async for event in self._provider.stream(self._conv.messages()):
                if event.text:
                    self._cur_reply += event.text
                elif event.done:
                    self._finish_turn()
                    return
                elif event.err is not None:
                    self._handle_stream_error(event.err)
                    return
        except asyncio.CancelledError:
            pass

    def _finish_turn(self) -> None:
        """T14: 回复完成，定型 Markdown 渲染。"""
        self._conv.add_assistant(self._cur_reply)
        self._write_assistant_message(self._cur_reply)
        self._end_turn()

    def _handle_stream_error(self, err: Exception) -> None:
        """T14: 流式过程中的错误。"""
        self._write_error(f"{type(err).__name__}: {err}")
        self._end_turn()

    def _end_turn(self) -> None:
        """T13-T15: 清理本轮状态，回到 IDLE。"""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

        self._streaming.add_class("hidden")
        self._streaming.update("")
        self._cur_reply = ""
        self._state = SessionState.IDLE
        self._input.disabled = False
        self._input.focus()
        self._stream_task = None

    # ─── Actions ─────────────────────────────────────────────────

    def action_quit(self) -> None:
        """退出程序。"""
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
        self.exit()
