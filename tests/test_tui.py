"""Textual TUI 挂载、模式、渲染、脱敏与取消测试。"""

import asyncio
import json
import os
from unittest.mock import patch

import pytest
from textual.widgets import Footer, Header, Input, RichLog

from endless_code.config import ProviderConfig
from endless_code.llm import Message, StreamEvent, ToolCall, ToolDefinition, Usage
from endless_code.permission import Mode, new_engine
from endless_code.prompt import EXECUTE_DIRECTIVE, PLAN_MODE_REMINDER
from endless_code.session import Writer
from endless_code.tool import Registry, Result, new_default_registry
from endless_code.tui.app import EndlessCodeApp, SessionState

TEST_SECRET = "sk-tui-secret-123456789"


class FakeProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self, scripts: list[list[StreamEvent]]) -> None:
        self.scripts = scripts
        self.call_count = 0
        self.requests: list[tuple[list[Message], list[ToolDefinition], str]] = []

    async def stream(self, msgs, tools, system_suffix=""):
        self.requests.append((msgs, tools, system_suffix))
        script = self.scripts[self.call_count]
        self.call_count += 1
        for event in script:
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


class SecretTool:
    read_only = True

    def name(self) -> str:
        return "secret_tool"

    def description(self) -> str:
        return "returns a test secret"

    def parameters(self) -> dict:
        return {"type": "object"}

    async def execute(self, args: str) -> Result:
        return Result(content=f"tool-result {TEST_SECRET}")


class WriteApprovalTool(SecretTool):
    read_only = False

    def name(self) -> str:
        return "write_file"

    async def execute(self, args: str) -> Result:
        return Result(content="written")


class BlockingTool(SecretTool):
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


def _config(*, api_key: str = TEST_SECRET) -> ProviderConfig:
    return ProviderConfig(
        name="fake",
        protocol="openai",
        api_key=api_key,
        model="fake-model",
    )


def _engine() -> "object":
    engine, _ = new_engine(str(os.getcwd()))
    engine._start_mode = Mode.BYPASS
    return engine


def _chat_text(app: EndlessCodeApp) -> str:
    return "\n".join(line.text for line in app._chat.lines)


async def _wait_for_state(app, pilot, state: SessionState, attempts: int = 100) -> None:
    for _ in range(attempts):
        await pilot.pause()
        if app.state is state:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"TUI did not reach {state}")


@pytest.mark.asyncio
async def test_mount_registers_widgets_and_exits_cleanly() -> None:
    providers = [
        _config(),
        ProviderConfig("deepseek", "deepseek", TEST_SECRET, "deepseek-chat"),
    ]
    app = EndlessCodeApp(providers, new_default_registry(), engine=_engine())
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.state is SessionState.SELECTING
        assert app.query_one(Header)
        assert app.query_one(RichLog)
        assert app.query_one(Input)
        assert app.query_one(Footer)


@pytest.mark.asyncio
async def test_input_backspace_deletes_single_char() -> None:
    """回归：非审批态下 _on_key 不得重复分派基类 App._on_key，退格一次只删一个字符。"""
    app = EndlessCodeApp([_config()], new_default_registry(), engine=_engine())
    async with app.run_test() as pilot:
        await _wait_for_state(app, pilot, SessionState.IDLE)
        inp = app.query_one("#user-input", Input)
        await pilot.press("h", "e", "l", "l", "o")
        assert inp.value == "hello"
        await pilot.press("backspace")
        assert inp.value == "hell", f"退格应删一个字符，实际 {inp.value!r}"
        await pilot.press("backspace")
        assert inp.value == "hel", f"退格应删一个字符，实际 {inp.value!r}"


@pytest.mark.asyncio
async def test_history_usage_and_iteration_are_updated_once() -> None:
    provider = FakeProvider(
        [
            [
                StreamEvent(text="hello"),
                StreamEvent(usage=Usage(4, 3)),
                StreamEvent(done=True),
            ]
        ]
    )
    with patch("endless_code.tui.app.new_provider", return_value=provider):
        app = EndlessCodeApp([_config()], new_default_registry(), engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            seen_iterations: list[int] = []
            original_tick = app._tick

            def tracked_tick() -> None:
                seen_iterations.append(app._iteration)
                original_tick()

            app._tick = tracked_tick
            app._start_turn("hi")
            await _wait_for_state(app, pilot, SessionState.IDLE)
            assert [message.role for message in app._conv.messages()] == [
                "user",
                "assistant",
            ]
            assert app._conv.messages()[-1].content == "hello"
            assert app._usage_in == 4
            assert app._usage_out == 3
            assert "↑4 ↓3 tok" in app.sub_title
            assert 1 in seen_iterations
            assert app._iteration == 0


@pytest.mark.asyncio
async def test_plan_and_do_command_switch_tools_and_suffix() -> None:
    provider = FakeProvider(
        [
            [StreamEvent(text="plan"), StreamEvent(done=True)],
            [StreamEvent(text="executed"), StreamEvent(done=True)],
        ]
    )
    with patch("endless_code.tui.app.new_provider", return_value=provider):
        app = EndlessCodeApp([_config()], new_default_registry(), engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            app._handle_idle_input("/plan")
            assert app.mode is Mode.PLAN
            app._handle_idle_input("make a plan")
            await _wait_for_state(app, pilot, SessionState.IDLE)
            assert [tool.name for tool in provider.requests[0][1]] == [
                "read_file",
                "glob",
                "grep",
            ]
            assert provider.requests[0][2] == PLAN_MODE_REMINDER

            app._handle_idle_input("/do")
            await _wait_for_state(app, pilot, SessionState.IDLE)
            assert app.mode is Mode.DEFAULT
            assert len(provider.requests[1][1]) == 6
            assert provider.requests[1][0][-1].content == EXECUTE_DIRECTIVE
            assert all(message.content != "/do" for message in app._conv.messages())


@pytest.mark.asyncio
async def test_compact_command_uses_summary_path_only() -> None:
    provider = FakeProvider(
        [[StreamEvent(text="<summary>brief</summary>"), StreamEvent(done=True)]]
    )
    with patch("endless_code.tui.app.new_provider", return_value=provider):
        app = EndlessCodeApp([_config()], new_default_registry(), engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            app._conv.add_user("已有历史")
            app._handle_idle_input("/compact")
            await _wait_for_state(app, pilot, SessionState.IDLE)
            assert len(provider.requests) == 1
            assert provider.requests[0][1] == []
            assert all(
                message.content != "/compact" for message in app._conv.messages()
            )
            assert "已压缩，token 从" in _chat_text(app)


@pytest.mark.asyncio
async def test_resume_switches_to_selected_persisted_session(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / ".endless-code" / "sessions" / "20260805-120000-abcd"
    writer = Writer(str(directory), "fake-model")
    writer.append(Message(role="user", content="恢复这条历史"))
    writer.append(Message(role="assistant", content="历史回复"))
    writer.close()

    with patch("endless_code.tui.app.new_provider", return_value=FakeProvider([])):
        app = EndlessCodeApp([_config()], new_default_registry(), engine=_engine())
        async with app.run_test() as pilot:
            await _wait_for_state(app, pilot, SessionState.IDLE)
            app._command_resume()
            assert app.state is SessionState.RESUMING
            app._render_resume_options("恢复")
            assert app._visible_resume_sessions[0].id == directory.name
            app._resume_selected()
            await _wait_for_state(app, pilot, SessionState.IDLE)
            assert [message.content for message in app._conv.messages()] == [
                "恢复这条历史",
                "历史回复",
            ]


@pytest.mark.asyncio
async def test_render_order_history_and_redaction_e2e() -> None:
    call = ToolCall(
        id="secret-call",
        name="secret_tool",
        input=json.dumps({"value": TEST_SECRET}),
    )
    provider = FakeProvider(
        [
            [
                StreamEvent(text="preamble"),
                StreamEvent(tool_calls=[call]),
                StreamEvent(done=True),
            ],
            [StreamEvent(text=f"final {TEST_SECRET}"), StreamEvent(done=True)],
        ]
    )
    registry = Registry()
    registry.register(SecretTool())
    with patch("endless_code.tui.app.new_provider", return_value=provider):
        app = EndlessCodeApp([_config()], registry, engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            app._start_turn("render")
            await _wait_for_state(app, pilot, SessionState.IDLE)
            visible = _chat_text(app)
            assert TEST_SECRET not in visible
            assert "[REDACTED]" in visible
            assert visible.index("preamble") < visible.index("secret_tool")
            assert visible.index("secret_tool") < visible.index("tool-result")
            assert visible.index("tool-result") < visible.index("final")
            assert [message.role for message in app._conv.messages()] == [
                "user",
                "assistant",
                "tool",
                "assistant",
            ]


@pytest.mark.asyncio
async def test_escape_and_ctrl_c_cancel_stream_without_exiting() -> None:
    for action_name in ("action_cancel_turn", "action_cancel_or_quit"):
        provider = BlockingProvider()
        with patch("endless_code.tui.app.new_provider", return_value=provider):
            app = EndlessCodeApp([_config()], new_default_registry(), engine=_engine())
            async with app.run_test() as pilot:
                await pilot.pause()
                app._start_turn("wait")
                await provider.started.wait()
                getattr(app, action_name)()
                await _wait_for_state(app, pilot, SessionState.IDLE)
                assert provider.closed
                assert app.is_running
                assert app._conv.last_role() == "assistant"


@pytest.mark.asyncio
async def test_cancel_blocking_tool_cleans_task() -> None:
    tool = BlockingTool()
    provider = FakeProvider(
        [
            [
                StreamEvent(
                    tool_calls=[ToolCall(id="b1", name="blocking", input="{}")]
                ),
                StreamEvent(done=True),
            ]
        ]
    )
    registry = Registry()
    registry.register(tool)
    with patch("endless_code.tui.app.new_provider", return_value=provider):
        app = EndlessCodeApp([_config()], registry, engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            app._start_turn("block")
            await tool.started.wait()
            app.action_cancel_turn()
            await _wait_for_state(app, pilot, SessionState.IDLE)
            assert tool.cancelled
            assert app._conv.messages()[-2].tool_results[0].is_error


@pytest.mark.asyncio
async def test_stream_error_recovery() -> None:
    provider = FakeProvider(
        [
            [StreamEvent(err=RuntimeError(f"offline {TEST_SECRET}"))],
            [StreamEvent(text="recovered"), StreamEvent(done=True)],
        ]
    )
    with patch("endless_code.tui.app.new_provider", return_value=provider):
        app = EndlessCodeApp([_config()], new_default_registry(), engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            app._start_turn("first")
            await _wait_for_state(app, pilot, SessionState.IDLE)
            app._start_turn("second")
            await _wait_for_state(app, pilot, SessionState.IDLE)
            visible = _chat_text(app)
            assert "offline" in visible
            assert "recovered" in visible
            assert TEST_SECRET not in visible


@pytest.mark.asyncio
async def test_responsive_iteration_updates_while_stream_waits() -> None:
    provider = BlockingProvider()
    with patch("endless_code.tui.app.new_provider", return_value=provider):
        app = EndlessCodeApp([_config()], new_default_registry(), engine=_engine())
        async with app.run_test() as pilot:
            await pilot.pause()
            app._start_turn("slow")
            await provider.started.wait()
            await asyncio.sleep(0.3)
            await pilot.pause()
            assert app._iteration == 1
            assert "第 1 轮" in str(app._streaming.render())
            app.action_cancel_turn()
            await _wait_for_state(app, pilot, SessionState.IDLE)


@pytest.mark.asyncio
async def test_provider_error_stays_in_tui() -> None:
    env_name = "ENDLESS_CODE_TEST_MISSING_KEY"
    os.environ.pop(env_name, None)
    app = EndlessCodeApp(
        [_config(api_key=f"${env_name}")], new_default_registry(), engine=_engine()
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.state is SessionState.SELECTING
        assert "ConfigError" in _chat_text(app)
        assert app.is_running


@pytest.mark.asyncio
async def test_shift_tab_cycles_modes() -> None:
    engine = _engine()
    engine._start_mode = Mode.DEFAULT
    app = EndlessCodeApp([_config()], new_default_registry(), engine=engine)
    async with app.run_test() as pilot:
        await pilot.pause()
        for expected in (Mode.ACCEPT_EDITS, Mode.PLAN, Mode.BYPASS, Mode.DEFAULT):
            app.action_cycle_mode()
            assert app.mode is expected
        assert "DEFAULT" in app.sub_title
        assert "fake" not in app.sub_title.split("|")[0]


@pytest.mark.asyncio
async def test_approval_state_and_forever_response(tmp_path) -> None:
    call = ToolCall(
        id="w1",
        name="write_file",
        input=json.dumps({"path": "src/x.py", "content": "x"}),
    )
    provider = FakeProvider(
        [
            [StreamEvent(tool_calls=[call]), StreamEvent(done=True)],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    registry = Registry()
    registry.register(WriteApprovalTool())
    engine, _ = new_engine(str(tmp_path))
    engine._start_mode = Mode.DEFAULT
    with patch("endless_code.tui.app.new_provider", return_value=provider):
        app = EndlessCodeApp([_config()], registry, engine=engine)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._start_turn("write")
            await _wait_for_state(app, pilot, SessionState.APPROVING)
            assert app.pending is not None
            app.update_approving("2")
            await _wait_for_state(app, pilot, SessionState.IDLE)
            local = tmp_path / ".endless-code" / "settings.local.yaml"
            assert "Write(src/x.py)" in local.read_text(encoding="utf-8")
