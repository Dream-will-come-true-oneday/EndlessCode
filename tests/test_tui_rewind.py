"""TUI /rewind 集成测试：时间线、范围选择、文件与对话回滚。"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from endless_code.checkpoint import CheckpointManager
from endless_code.config import ProviderConfig
from endless_code.llm import Message, StreamEvent, ToolCall, ToolDefinition
from endless_code.permission import Mode, new_engine
from endless_code.tool import Registry, Result
from endless_code.tui.app import EndlessCodeApp, SessionState
from tests.test_tui import TEST_SECRET, _chat_text, _wait_for_state


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


class RealWriteTool:
    read_only = False

    def name(self) -> str:
        return "write_file"

    def description(self) -> str:
        return "write a real file"

    def parameters(self) -> dict:
        return {"type": "object"}

    async def execute(self, args: str) -> Result:
        data = json.loads(args)
        Path(data["path"]).write_text(data["content"], encoding="utf-8")
        return Result(content=f"已写入 {data['path']}")


def _config() -> ProviderConfig:
    return ProviderConfig(
        name="fake",
        protocol="openai",
        api_key=TEST_SECRET,
        model="fake-model",
    )


def _registry() -> Registry:
    registry = Registry()
    registry.register(RealWriteTool())
    return registry


def _engine(tmp_path: Path):
    engine, _ = new_engine(str(tmp_path))
    engine._start_mode = Mode.BYPASS
    return engine


def _swap_checkpoint(app: EndlessCodeApp, tmp_path: Path) -> CheckpointManager:
    session_dir = tmp_path / ".endless-code" / "sessions" / "s1"
    manager = CheckpointManager(str(tmp_path), str(session_dir), "s1")
    app._checkpoint = manager
    app._agent._checkpoint = manager
    return manager


def _write_call(path: Path, content: str) -> ToolCall:
    return ToolCall(
        id="w1",
        name="write_file",
        input=json.dumps({"path": str(path), "content": content}),
    )


async def _run_write_turn(app: EndlessCodeApp, pilot, path: Path, content: str) -> None:
    app._provider.scripts = [  # type: ignore[union-attr]
        [StreamEvent(tool_calls=[_write_call(path, content)]), StreamEvent(done=True)],
        [StreamEvent(text="done"), StreamEvent(done=True)],
    ]
    app._start_turn("go")
    await _wait_for_state(app, pilot, SessionState.IDLE)


@pytest.mark.asyncio
async def test_rewind_without_checkpoints_shows_notice() -> None:
    with patch("endless_code.tui.app.new_provider", return_value=FakeProvider([])):
        app = EndlessCodeApp([_config()], _registry(), engine=_engine(Path.cwd()))
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._dispatcher.try_dispatch("/rewind") is True
            assert app.state is SessionState.IDLE
            assert "尚无 checkpoint" in _chat_text(app)


@pytest.mark.asyncio
async def test_rewind_esc_leaves_files_untouched(tmp_path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("v1\n", encoding="utf-8")
    provider = FakeProvider([])
    with patch("endless_code.tui.app.new_provider", return_value=provider):
        app = EndlessCodeApp([_config()], _registry(), engine=_engine(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            manager = _swap_checkpoint(app, tmp_path)
            await _run_write_turn(app, pilot, target, "v2 by ai\n")
            assert len(manager.list()) == 1

            assert app._dispatcher.try_dispatch("/rewind") is True
            await _wait_for_state(app, pilot, SessionState.REWINDING)
            await pilot.press("escape")
            await _wait_for_state(app, pilot, SessionState.IDLE)
            assert target.read_text(encoding="utf-8") == "v2 by ai\n"


@pytest.mark.asyncio
async def test_rewind_files_only_keeps_conversation_and_notifies(tmp_path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("v1\n", encoding="utf-8")
    provider = FakeProvider([])
    with patch("endless_code.tui.app.new_provider", return_value=provider):
        app = EndlessCodeApp([_config()], _registry(), engine=_engine(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            _swap_checkpoint(app, tmp_path)
            conv_len_before = app._conv.length()
            await _run_write_turn(app, pilot, target, "v2 by ai\n")
            conv_len_after = app._conv.length()
            assert conv_len_after > conv_len_before

            app._dispatcher.try_dispatch("/rewind")
            await _wait_for_state(app, pilot, SessionState.REWINDING)
            await pilot.press("enter")  # 选中第一个 checkpoint
            await pilot.pause()
            assert app._rewind_phase == "scope"
            await pilot.press("enter")  # 第一项：仅恢复文件
            await _wait_for_state(app, pilot, SessionState.IDLE)

            assert target.read_text(encoding="utf-8") == "v1\n"
            assert app._conv.length() == conv_len_after + 1
            last = app._conv.messages()[-1]
            assert last.role == "user"
            assert "已回滚到 checkpoint #1" in last.content


@pytest.mark.asyncio
async def test_rewind_both_restores_conversation(tmp_path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("v1\n", encoding="utf-8")
    provider = FakeProvider([])
    with patch("endless_code.tui.app.new_provider", return_value=provider):
        app = EndlessCodeApp([_config()], _registry(), engine=_engine(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            manager = _swap_checkpoint(app, tmp_path)
            conv_len_before = app._conv.length()
            await _run_write_turn(app, pilot, target, "v2 by ai\n")

            app._dispatcher.try_dispatch("/rewind")
            await _wait_for_state(app, pilot, SessionState.REWINDING)
            await pilot.press("enter")  # 选中 checkpoint
            await pilot.pause()
            await pilot.press("down")  # 仅恢复对话
            await pilot.press("down")  # 都恢复
            await pilot.press("enter")
            await _wait_for_state(app, pilot, SessionState.IDLE)

            assert target.read_text(encoding="utf-8") == "v1\n"
            assert app._conv.length() == manager.list()[0].conv_len
            assert app._conv.length() > conv_len_before


@pytest.mark.asyncio
async def test_rewind_cleanup_clears_checkpoints(tmp_path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("v1\n", encoding="utf-8")
    provider = FakeProvider([])
    with patch("endless_code.tui.app.new_provider", return_value=provider):
        app = EndlessCodeApp([_config()], _registry(), engine=_engine(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            manager = _swap_checkpoint(app, tmp_path)
            await _run_write_turn(app, pilot, target, "v2\n")
            assert len(manager.list()) == 1

            app._dispatcher.try_dispatch("/rewind")
            await _wait_for_state(app, pilot, SessionState.REWINDING)
            await pilot.press("down")  # 跳到清理项
            await pilot.press("enter")
            await _wait_for_state(app, pilot, SessionState.IDLE)
            assert manager.list() == []
