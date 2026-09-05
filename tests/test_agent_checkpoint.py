"""Agent 层 checkpoint 切入与 diff 事件字段测试。"""

import json
from pathlib import Path

import pytest

from endless_code.agent import Agent, Outcome, Phase
from endless_code.checkpoint import CheckpointManager
from endless_code.conversation import Conversation
from endless_code.llm import StreamEvent, ToolCall
from endless_code.permission import Mode, new_engine
from endless_code.tool import Registry
from endless_code.tool.edit_file import EditFileTool
from endless_code.tool.write_file import WriteFileTool
from tests.test_agent import FakeProvider


def _registry(*tools) -> Registry:
    registry = Registry()
    for tool in tools:
        registry.register(tool)
    return registry


def _mk_conv() -> Conversation:
    conv = Conversation()
    conv.add_user("go")
    return conv


def _checkpoint(tmp_path: Path) -> CheckpointManager:
    session_dir = tmp_path / ".endless-code" / "sessions" / "s1"
    return CheckpointManager(str(tmp_path), str(session_dir), "s1")


def _write_call(path: Path, content: str) -> ToolCall:
    return ToolCall(
        id="w1",
        name="write_file",
        input=json.dumps({"path": str(path), "content": content}),
    )


async def _collect(agent: Agent, conv: Conversation, mode: Mode) -> list:
    events = []
    async for event in agent.run(conv, mode=mode):
        events.append(event)
        if event.approval is not None:
            event.approval.respond.set_result(Outcome.ALLOW_ONCE)
    return events


@pytest.mark.asyncio
async def test_approval_carries_diff_for_new_file(tmp_path) -> None:
    target = tmp_path / "new.py"
    provider = FakeProvider(
        [
            [
                StreamEvent(tool_calls=[_write_call(target, "hello\n")]),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    engine, err = new_engine(str(tmp_path))
    assert err is None
    agent = Agent(provider, _registry(WriteFileTool()), engine=engine)

    approvals = []
    async for event in agent.run(_mk_conv(), mode=Mode.DEFAULT):
        if event.approval is not None:
            approvals.append(event.approval)
            event.approval.respond.set_result(Outcome.ALLOW_ONCE)

    assert len(approvals) == 1
    assert "新建文件" in approvals[0].diff
    assert "+hello" in approvals[0].diff
    # 未批准前文件不存在（diff 只是预览）
    assert approvals[0].diff is not None


@pytest.mark.asyncio
async def test_approval_carries_diff_for_edit(tmp_path) -> None:
    target = tmp_path / "code.py"
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    call = ToolCall(
        id="e1",
        name="edit_file",
        input=json.dumps(
            {
                "path": str(target),
                "old_string": "beta",
                "new_string": "BETA2",
            }
        ),
    )
    provider = FakeProvider(
        [
            [StreamEvent(tool_calls=[call]), StreamEvent(done=True)],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    engine, err = new_engine(str(tmp_path))
    assert err is None
    agent = Agent(provider, _registry(EditFileTool()), engine=engine)

    approvals = []
    async for event in agent.run(_mk_conv(), mode=Mode.DEFAULT):
        if event.approval is not None:
            approvals.append(event.approval)
            event.approval.respond.set_result(Outcome.ALLOW_ONCE)

    assert len(approvals) == 1
    assert "-beta" in approvals[0].diff
    assert "+BETA2" in approvals[0].diff


@pytest.mark.asyncio
async def test_end_event_carries_diff_stat_and_checkpoint_created(tmp_path) -> None:
    target = tmp_path / "new.py"
    provider = FakeProvider(
        [
            [
                StreamEvent(tool_calls=[_write_call(target, "line\n")]),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    engine, err = new_engine(str(tmp_path))
    assert err is None
    checkpoint = _checkpoint(tmp_path)
    agent = Agent(
        provider,
        _registry(WriteFileTool()),
        engine=engine,
        checkpoint=checkpoint,
    )

    events = await _collect(agent, _mk_conv(), Mode.BYPASS)
    end_events = [e for e in events if e.tool is not None and e.tool.phase is Phase.END]
    assert len(end_events) == 1
    assert "新建文件" in end_events[0].tool.diff or "+line" in end_events[0].tool.diff
    assert end_events[0].tool.stat == "+1 -0"

    metas = checkpoint.list()
    assert len(metas) == 1
    assert metas[0].tool == "write_file"
    assert metas[0].target.replace("\\", "/").endswith("new.py")
    assert (tmp_path / ".endless-code" / "sessions" / "s1" / "checkpoints").exists()


@pytest.mark.asyncio
async def test_deny_creates_no_checkpoint(tmp_path) -> None:
    target = tmp_path / "new.py"
    provider = FakeProvider(
        [
            [
                StreamEvent(tool_calls=[_write_call(target, "x\n")]),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    engine, err = new_engine(str(tmp_path))
    assert err is None
    checkpoint = _checkpoint(tmp_path)
    agent = Agent(
        provider,
        _registry(WriteFileTool()),
        engine=engine,
        checkpoint=checkpoint,
    )

    async for event in agent.run(_mk_conv(), mode=Mode.DEFAULT):
        if event.approval is not None:
            event.approval.respond.set_result(Outcome.DENY_ONCE)

    assert checkpoint.list() == []
    assert not target.exists()


@pytest.mark.asyncio
async def test_side_effect_tool_creates_checkpoint_without_diff(tmp_path) -> None:
    from tests.test_agent import TimedTool, Timeline

    provider = FakeProvider(
        [
            [
                StreamEvent(
                    tool_calls=[
                        ToolCall(id="b1", name="bash", input=json.dumps({"value": "v"}))
                    ]
                ),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    engine, err = new_engine(str(tmp_path))
    assert err is None
    checkpoint = _checkpoint(tmp_path)
    agent = Agent(
        provider,
        _registry(TimedTool("bash", False, Timeline())),
        engine=engine,
        checkpoint=checkpoint,
    )

    events = await _collect(agent, _mk_conv(), Mode.BYPASS)
    end_events = [e for e in events if e.tool is not None and e.tool.phase is Phase.END]
    assert end_events[0].tool.diff == ""
    assert end_events[0].tool.stat == ""
    assert len(checkpoint.list()) == 1
    assert checkpoint.list()[0].tool == "bash"


@pytest.mark.asyncio
async def test_agent_without_checkpoint_still_emits_diff(tmp_path) -> None:
    target = tmp_path / "new.py"
    provider = FakeProvider(
        [
            [
                StreamEvent(tool_calls=[_write_call(target, "z\n")]),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    engine, err = new_engine(str(tmp_path))
    assert err is None
    agent = Agent(provider, _registry(WriteFileTool()), engine=engine)

    events = await _collect(agent, _mk_conv(), Mode.BYPASS)
    end_events = [e for e in events if e.tool is not None and e.tool.phase is Phase.END]
    assert "+z" in end_events[0].tool.diff


@pytest.mark.asyncio
async def test_binary_target_diff_placeholder(tmp_path) -> None:
    blob = tmp_path / "img.bin"
    blob.write_bytes(b"\x00\x01\x02")
    call = ToolCall(
        id="e1",
        name="edit_file",
        input=json.dumps({"path": str(blob), "old_string": "a", "new_string": "b"}),
    )
    provider = FakeProvider(
        [
            [StreamEvent(tool_calls=[call]), StreamEvent(done=True)],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    engine, err = new_engine(str(tmp_path))
    assert err is None
    agent = Agent(provider, _registry(EditFileTool()), engine=engine)

    approvals = []
    async for event in agent.run(_mk_conv(), mode=Mode.DEFAULT):
        if event.approval is not None:
            approvals.append(event.approval)
            event.approval.respond.set_result(Outcome.DENY_ONCE)

    assert len(approvals) == 1
    assert "二进制" in approvals[0].diff


@pytest.mark.asyncio
async def test_error_result_skips_diff(tmp_path) -> None:
    # 编辑不存在的文件 → 工具报错 → END 事件无 diff，checkpoint 已创建
    call = ToolCall(
        id="e1",
        name="edit_file",
        input=json.dumps(
            {"path": str(tmp_path / "ghost.py"), "old_string": "a", "new_string": "b"}
        ),
    )
    provider = FakeProvider(
        [
            [StreamEvent(tool_calls=[call]), StreamEvent(done=True)],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    engine, err = new_engine(str(tmp_path))
    assert err is None
    checkpoint = _checkpoint(tmp_path)
    agent = Agent(
        provider,
        _registry(EditFileTool()),
        engine=engine,
        checkpoint=checkpoint,
    )

    events = await _collect(agent, _mk_conv(), Mode.BYPASS)
    end_events = [e for e in events if e.tool is not None and e.tool.phase is Phase.END]
    assert end_events[0].tool.is_error is True
    assert end_events[0].tool.diff == ""
    assert len(checkpoint.list()) == 1
