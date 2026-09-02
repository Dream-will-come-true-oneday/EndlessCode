"""L7 稳定性：失败注入下系统必须不崩溃且可恢复。

覆盖：会话文件截断、磁盘写失败、审计目录不可写、记忆更新异常/非法输出、
Provider 流中抛异常、连续三次取消。
"""

import asyncio
from unittest.mock import patch

import pytest
from harness.fake_provider import FakeProvider
from harness.tools import BlockingTool, make_registry
from harness.tui_helpers import make_config, make_engine, wait_for_state

from endless_code.agent import Agent
from endless_code.conversation import Conversation
from endless_code.llm import Message, StreamEvent, ToolCall
from endless_code.memory import Manager
from endless_code.permission import AuditWriter, Mode
from endless_code.session import Writer, load_session
from endless_code.tool import new_default_registry
from endless_code.tui.app import EndlessCodeApp, SessionState

pytestmark = pytest.mark.stability


def test_truncated_jsonl_tail_is_skipped(tmp_path) -> None:
    """异常中断留下的半截行必须被跳过，最多影响最后一行。"""
    directory = tmp_path / "20260805-120000-abcd"
    directory.mkdir()
    (directory / "conversation.jsonl").write_text(
        '{"role":"user","content":"ok","model":"m","ts":1}\n'
        '{"role":"assistant","content":"reply","ts":2}\n'
        '{"role":"user","conte',
        encoding="utf-8",
    )
    loaded = load_session(str(directory))
    assert [message.content for message in loaded.messages] == ["ok", "reply"]


def test_writer_disk_full_does_not_raise(tmp_path) -> None:
    """写入失败（如磁盘满）只记录日志，绝不把异常抛给对话流。"""
    writer = Writer(str(tmp_path / "session"), "m")
    original_write = writer._file.write

    def failing_write(_data):
        raise OSError("disk full")

    writer._file.write = failing_write
    writer.append(Message(role="user", content="still alive"))  # 不抛异常
    writer._file.write = original_write
    writer.close()


def test_audit_writer_unwritable_dir_best_effort(tmp_path) -> None:
    """会话目录不可创建时审计降级为无操作，不影响调用方。"""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    writer = AuditWriter(blocker / "session", "s1")
    writer.record("permission_checked", tool="read_file")
    assert writer.recent(10) == []
    writer.close()  # 不抛异常


async def test_memory_update_invalid_json_is_ignored(tmp_path) -> None:
    """模型返回非法 JSON / 未知 action 时：不崩溃、不落脏文件。"""

    class BadProvider:
        name = "bad"
        model = "bad"

        def __init__(self, response: str) -> None:
            self.response = response

        async def stream(self, request):
            yield StreamEvent(text=self.response)
            yield StreamEvent(done=True)

    for response in ("not-json-at-all", '[{"action":"hack","level":"user"}]'):
        project = tmp_path / f"project-{abs(hash(response)) % 1000}"
        manager = Manager(str(project), str(tmp_path / "user"))
        manager.set_provider(BadProvider(response), "bad")
        await manager.update_async([Message(role="user", content="记住这个")])
        if project.exists():
            # 非法动作不得产生笔记文件；索引重建允许存在但必须为空
            notes = [p for p in project.glob("*.md") if p.name != "MEMORY.md"]
            assert not notes, f"非法动作产生了文件: {response} -> {notes}"
            index = (project / "MEMORY.md").read_text(encoding="utf-8")
            assert index.strip() == "", f"非法动作写入了索引: {response}"


async def test_memory_update_provider_error_is_swallowed(tmp_path) -> None:
    class ExplodingProvider:
        name = "boom"
        model = "boom"

        async def stream(self, request):
            raise RuntimeError("memory provider down")
            yield StreamEvent(done=True)  # pragma: no cover

    manager = Manager(str(tmp_path / "p"), str(tmp_path / "u"))
    manager.set_provider(ExplodingProvider(), "boom")
    await manager.update_async([Message(role="user", content="remember")])


async def test_agent_provider_exception_yields_terminal_error() -> None:
    """Provider 在流中直接抛异常：必须转为终止事件，历史保持可继续。"""

    class ExplodingProvider:
        name = "boom"
        model = "boom"

        async def stream(self, request):
            raise RuntimeError("connection reset")
            yield StreamEvent(done=True)  # pragma: no cover

    conv = Conversation()
    conv.add_user("work")
    events = [
        event
        async for event in Agent(ExplodingProvider(), make_registry()).run(
            conv, mode=Mode.BYPASS
        )
    ]
    assert any(event.err is not None for event in events)
    assert sum(event.done for event in events) == 1
    assert conv.last_role() == "assistant"

    # 异常之后对话仍可继续
    class RecoveredProvider:
        name = "ok"
        model = "ok"

        async def stream(self, request):
            yield StreamEvent(text="recovered")
            yield StreamEvent(done=True)

    conv.add_user("again")
    events = [
        event
        async for event in Agent(RecoveredProvider(), make_registry()).run(
            conv, mode=Mode.BYPASS
        )
    ]
    assert any(event.text == "recovered" for event in events)


@pytest.mark.asyncio
async def test_tui_triple_cancel_returns_to_idle(tmp_path, monkeypatch) -> None:
    """连续三次取消流式回合：每次都回到 IDLE 且历史自洽。"""
    monkeypatch.chdir(tmp_path)
    for _ in range(3):
        blocking = _BlockingFake()
        with patch("endless_code.tui.app.new_provider", return_value=blocking):
            app = EndlessCodeApp(
                [make_config()],
                new_default_registry(),
                engine=make_engine(str(tmp_path)),
            )
            async with app.run_test() as pilot:
                await pilot.pause()
                app._start_turn("wait")
                await blocking.started.wait()
                app.action_cancel_turn()
                await wait_for_state(app, pilot, SessionState.IDLE)
                assert blocking.closed
                assert app._conv.last_role() == "assistant"


class _BlockingFake:
    """永不自行结束的假 Provider，用于取消路径。"""

    name = "blocking-fake"
    model = "blocking-fake"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.closed = False

    async def stream(self, msgs, tools, system_suffix=""):
        self.started.set()
        try:
            await asyncio.Event().wait()
            yield StreamEvent(done=True)
        finally:
            self.closed = True


async def test_cancel_blocking_tool_repeatedly(tmp_path, monkeypatch) -> None:
    """阻塞工具被取消两次：取消标记置位、结果为 is_error、历史自洽。"""
    monkeypatch.chdir(tmp_path)
    for _ in range(2):
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
        with patch("endless_code.tui.app.new_provider", return_value=provider):
            app = EndlessCodeApp(
                [make_config()],
                make_registry(tool),
                engine=make_engine(str(tmp_path)),
            )
            async with app.run_test() as pilot:
                await pilot.pause()
                app._start_turn("block")
                await tool.started.wait()
                app.action_cancel_turn()
                await wait_for_state(app, pilot, SessionState.IDLE)
                assert tool.cancelled
                assert app._conv.messages()[-2].tool_results[0].is_error
