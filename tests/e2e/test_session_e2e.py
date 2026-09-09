"""E2E 会话场景：恢复后续写、压缩后续写、清空后持久化。

判定标准：用户操作后可观测的持久化产物（conversation.jsonl）与内存对话自洽。
"""

import json
from unittest.mock import patch

import pytest
from harness.fake_provider import FakeProvider, text_turn
from harness.tui_helpers import make_config, make_engine, wait_for_state

from endless_code.llm import Message, StreamEvent
from endless_code.session import Writer
from endless_code.tool import new_default_registry
from endless_code.tui.app import EndlessCodeApp, SessionState

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

SESSION_ID = "20260805-120000-abcd"


def _read_jsonl(path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


async def test_resume_then_continues_in_same_session_file(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / ".endless-code" / "sessions" / SESSION_ID
    writer = Writer(str(directory), "fake-model")
    writer.append(Message(role="user", content="恢复这条历史"))
    writer.append(Message(role="assistant", content="历史回复"))
    writer.close()

    provider = FakeProvider([text_turn("continued")])
    with patch("endless_code.tui.app.new_provider", return_value=provider):
        app = EndlessCodeApp(
            [make_config()], new_default_registry(), engine=make_engine(str(tmp_path))
        )
        async with app.run_test() as pilot:
            await wait_for_state(app, pilot, SessionState.IDLE)
            app._command_resume()
            app._render_resume_options("恢复")
            assert app._visible_resume_sessions[0].id == SESSION_ID
            app._resume_selected()
            await wait_for_state(app, pilot, SessionState.IDLE)
            assert [m.content for m in app._conv.messages()] == [
                "恢复这条历史",
                "历史回复",
            ]

            # 恢复后的新消息继续追加到原会话文件
            app._start_turn("继续任务")
            await wait_for_state(app, pilot, SessionState.IDLE)
            assert app._writer.path == directory / "conversation.jsonl"
            rows = _read_jsonl(app._writer.path)
            contents = [row.get("content", "") for row in rows]
            assert "继续任务" in contents
            assert "continued" in contents


async def test_compact_then_continue_keeps_summary(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    provider = FakeProvider(
        [
            text_turn("old-reply"),
            # /compact 走摘要路径（不带工具定义）
            [
                StreamEvent(text="<summary>## 1 主要请求和意图\n继续任务</summary>"),
                StreamEvent(done=True),
            ],
            text_turn("continued"),
        ]
    )
    with patch("endless_code.tui.app.new_provider", return_value=provider):
        app = EndlessCodeApp(
            [make_config()], new_default_registry(), engine=make_engine(str(tmp_path))
        )
        async with app.run_test() as pilot:
            await wait_for_state(app, pilot, SessionState.IDLE)
            app._start_turn("旧历史")
            await wait_for_state(app, pilot, SessionState.IDLE)

            app._handle_idle_input("/compact")
            await wait_for_state(app, pilot, SessionState.IDLE)
            assert "历史会话摘要" in app._conv.messages()[0].content

            # 压缩后继续原任务：模型请求以摘要开头（尾部可能保留最近回合）
            app._start_turn("继续任务")
            await wait_for_state(app, pilot, SessionState.IDLE)
            last_request_msgs = provider.requests[2][0]
            assert "历史会话摘要" in last_request_msgs[0].content

            # 持久化文件含压缩标记
            rows = _read_jsonl(app._writer.path)
            assert any(row.get("type") == "compact" for row in rows)


async def test_clear_then_new_turn_persists(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    provider = FakeProvider([text_turn("old-turn-reply"), text_turn("fresh")])
    with patch("endless_code.tui.app.new_provider", return_value=provider):
        app = EndlessCodeApp(
            [make_config()], new_default_registry(), engine=make_engine(str(tmp_path))
        )
        async with app.run_test() as pilot:
            await wait_for_state(app, pilot, SessionState.IDLE)
            app._start_turn("旧对话")
            await wait_for_state(app, pilot, SessionState.IDLE)

            app._handle_idle_input("/clear yes")
            await pilot.pause()
            assert app._conv.length() == 0
            assert app._writer.path.stat().st_size == 0

            app._start_turn("新消息")
            await wait_for_state(app, pilot, SessionState.IDLE)
            rows = _read_jsonl(app._writer.path)
            contents = [row.get("content", "") for row in rows]
            assert "新消息" in contents
            assert "fresh" in contents
            assert all("旧对话" not in content for content in contents)
