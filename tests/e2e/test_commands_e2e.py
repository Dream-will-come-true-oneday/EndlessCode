"""E2E 命令场景：/status、/review、/fix、/explain、/memory、/audit。

判定标准：命令产生的可观测行为（对话流内容、AI 请求、界面文本）符合预期；
本地命令绝不触发 AI 请求。
"""

from unittest.mock import patch

import pytest
from harness.fake_provider import FakeProvider, text_turn
from harness.tui_helpers import chat_text, make_config, make_engine, wait_for_state

from endless_code.llm import StreamEvent, Usage
from endless_code.memory import Manager
from endless_code.tool import new_default_registry
from endless_code.tui.app import EndlessCodeApp, SessionState

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


def _run_app(provider, tmp_path):
    return EndlessCodeApp(
        [make_config()], new_default_registry(), engine=make_engine(str(tmp_path))
    )


async def test_status_shows_live_usage_without_ai_request(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    provider = FakeProvider(
        [
            [
                StreamEvent(text="hi"),
                StreamEvent(usage=Usage(4, 3)),
                StreamEvent(done=True),
            ]
        ]
    )
    with patch("endless_code.tui.app.new_provider", return_value=provider):
        app = _run_app(provider, tmp_path)
        async with app.run_test() as pilot:
            await wait_for_state(app, pilot, SessionState.IDLE)
            app._start_turn("hello")
            await wait_for_state(app, pilot, SessionState.IDLE)

            app._handle_idle_input("/status")
            await pilot.pause()

            visible = chat_text(app)
            assert provider.call_count == 1  # /status 不产生 AI 请求
            assert "fake-model" in visible
            assert "↑4 ↓3" in visible
            assert "消息数：2" in visible


async def test_review_enters_conversation_flow_and_records_reply(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    provider = FakeProvider([text_turn("review-done")])
    with patch("endless_code.tui.app.new_provider", return_value=provider):
        app = _run_app(provider, tmp_path)
        async with app.run_test() as pilot:
            await wait_for_state(app, pilot, SessionState.IDLE)
            app._handle_idle_input("/review 安全性")
            await wait_for_state(app, pilot, SessionState.IDLE)

            sent = provider.requests[0][0][-1].content
            assert "请审查当前代码改动" in sent
            assert "补充要求：安全性" in sent
            assert app._last_reply == "review-done"


async def test_fix_requires_error_then_carries_it(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    provider = FakeProvider(
        [
            [StreamEvent(err=RuntimeError("boom-42"))],
            text_turn("fixed"),
        ]
    )
    with patch("endless_code.tui.app.new_provider", return_value=provider):
        app = _run_app(provider, tmp_path)
        async with app.run_test() as pilot:
            await wait_for_state(app, pilot, SessionState.IDLE)

            # 无最近错误时 /fix 只给引导，不送 AI
            app._handle_idle_input("/fix")
            await pilot.pause()
            assert "没有可修复的最近错误" in chat_text(app)
            assert provider.call_count == 0

            # 制造一次流错误，再用 /fix 携带错误送入对话流
            app._start_turn("crash")
            await wait_for_state(app, pilot, SessionState.IDLE)
            app._handle_idle_input("/fix")
            await wait_for_state(app, pilot, SessionState.IDLE)

            sent = provider.requests[1][0][-1].content
            assert "boom-42" in sent
            assert "定位原因并修复" in sent


async def test_explain_uses_last_reply(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    provider = FakeProvider([text_turn("hello-world"), text_turn("explained")])
    with patch("endless_code.tui.app.new_provider", return_value=provider):
        app = _run_app(provider, tmp_path)
        async with app.run_test() as pilot:
            await wait_for_state(app, pilot, SessionState.IDLE)
            app._start_turn("hi")
            await wait_for_state(app, pilot, SessionState.IDLE)

            app._handle_idle_input("/explain")
            await wait_for_state(app, pilot, SessionState.IDLE)

            sent = provider.requests[1][0][-1].content
            assert "hello-world" in sent
            assert "逐段解释" in sent


async def test_memory_command_shows_guide_or_index(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    provider = FakeProvider([])
    with patch("endless_code.tui.app.new_provider", return_value=provider):
        app = _run_app(provider, tmp_path)
        async with app.run_test() as pilot:
            await wait_for_state(app, pilot, SessionState.IDLE)

            app._handle_idle_input("/memory")
            await pilot.pause()
            assert "当前未配置长期记忆" in chat_text(app)

            project_dir = tmp_path / "memory-project"
            project_dir.mkdir()
            (project_dir / "MEMORY.md").write_text("- 偏好：中文回复", encoding="utf-8")
            app._memory_manager = Manager(
                str(project_dir), str(tmp_path / "memory-user")
            )
            app._handle_idle_input("/memory")
            await pilot.pause()
            assert "偏好：中文回复" in chat_text(app)
            assert provider.call_count == 0


async def test_audit_command_empty_then_shows_recorded_event(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    provider = FakeProvider([])
    with patch("endless_code.tui.app.new_provider", return_value=provider):
        app = _run_app(provider, tmp_path)
        async with app.run_test() as pilot:
            await wait_for_state(app, pilot, SessionState.IDLE)

            app._handle_idle_input("/audit")
            await pilot.pause()
            visible = chat_text(app)
            assert ("暂无权限审计记录" in visible) or (
                "尚未建立权限审计记录" in visible
            )

            assert app._audit_writer is not None
            app._audit_writer.record(
                "permission_checked",
                call_id="c1",
                tool="write_file",
                target="src/a.py",
                decision="ask",
                risk_level="medium",
                rule_source="mode",
            )
            app._handle_idle_input("/audit")
            await pilot.pause()
            visible = chat_text(app)
            assert "permission_checked" in visible
            assert "src/a.py" in visible
