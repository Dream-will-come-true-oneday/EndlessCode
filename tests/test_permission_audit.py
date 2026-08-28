"""Permission explanations and per-session audit lifecycle tests."""

import json

import pytest

from endless_code.agent import Agent
from endless_code.conversation import Conversation
from endless_code.llm import StreamEvent, ToolCall
from endless_code.permission import AuditWriter, Decision, Mode, RiskLevel, new_engine
from endless_code.tool import Registry, Result


def _call(name: str, **kwargs) -> ToolCall:
    return ToolCall(id="c1", name=name, input=json.dumps(kwargs))


def test_explanation_is_deterministic_and_legacy_check_is_compatible(tmp_path) -> None:
    engine, _ = new_engine(str(tmp_path))
    call = _call("write_file", path="src/a.py", content="x")
    explanation = engine.explain(Mode.DEFAULT, call, False)

    assert explanation.decision is Decision.ASK
    assert explanation.risk_level is RiskLevel.MEDIUM
    assert explanation.tool == "write_file"
    assert explanation.target == "src/a.py"
    assert explanation.rule_source == "mode"
    assert explanation.requires_approval is True
    assert engine.check(Mode.DEFAULT, call, False) == (
        explanation.decision,
        explanation.reason,
    )

    blocked = engine.explain(Mode.BYPASS, _call("bash", command="rm -rf /"), False)
    assert blocked.decision is Decision.DENY
    assert blocked.risk_level is RiskLevel.CRITICAL
    assert blocked.rule_source == "blacklist"


class _WriteTool:
    read_only = False

    def name(self) -> str:
        return "write_file"

    def description(self) -> str:
        return "write"

    def parameters(self) -> dict:
        return {"type": "object"}

    async def execute(self, args: str) -> Result:
        return Result(content="result supersecret")


class _Provider:
    model = "audit-model"
    name = "audit"

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, messages, tools, system_suffix=""):
        self.calls += 1
        if self.calls == 1:
            yield StreamEvent(
                tool_calls=[
                    _call(
                        "write_file",
                        path="src/a.py",
                        content="supersecret",
                    )
                ]
            )
            yield StreamEvent(done=True)
        else:
            yield StreamEvent(text="done")
            yield StreamEvent(done=True)


@pytest.mark.asyncio
async def test_agent_writes_complete_redacted_audit_lifecycle(tmp_path) -> None:
    from endless_code.agent import new_session_runtime

    runtime = new_session_runtime(str(tmp_path))
    writer = AuditWriter(
        runtime.session.session_dir, runtime.session.session_id, secrets=["supersecret"]
    )
    registry = Registry()
    registry.register(_WriteTool())
    provider = _Provider()
    engine, _ = new_engine(str(tmp_path))
    conv = Conversation()
    conv.add_user("run")
    agent = Agent(
        provider, registry, engine=engine, runtime=runtime, audit_writer=writer
    )

    [event async for event in agent.run(conv, Mode.BYPASS)]
    writer.close()
    records = writer.recent(50)

    assert [record["event"] for record in records] == [
        "permission_checked",
        "approval_responded",
        "execution_started",
        "execution_finished",
    ]
    assert all(record["session_id"] == runtime.session.session_id for record in records)
    assert all("timestamp" in record and "call_id" in record for record in records)
    assert all(record["risk_level"] == "medium" for record in records)
    assert all(
        "supersecret" not in json.dumps(record, ensure_ascii=False)
        for record in records
    )
    assert records[-1]["is_error"] is False


def test_audit_write_failure_is_best_effort(tmp_path, monkeypatch) -> None:
    writer = AuditWriter(tmp_path / "session", "s1")
    monkeypatch.setattr(
        writer._file, "write", lambda _: (_ for _ in ()).throw(OSError("disk"))
    )
    writer.record("permission_checked", tool="read_file")
    writer.close()


@pytest.mark.asyncio
async def test_tui_audit_command_renders_recent_safe_fields(
    tmp_path, monkeypatch
) -> None:
    from unittest.mock import patch

    from endless_code.config import ProviderConfig
    from endless_code.tui.app import EndlessCodeApp, SessionState

    monkeypatch.chdir(tmp_path)
    provider_config = ProviderConfig(
        name="fake", protocol="openai", api_key="sk-audit-test", model="audit"
    )

    class Provider:
        model = "audit"
        name = "fake"

        async def stream(self, messages, tools, system_suffix=""):
            yield StreamEvent(text="ok")
            yield StreamEvent(done=True)

    engine, _ = new_engine(str(tmp_path))
    with patch("endless_code.tui.app.new_provider", return_value=Provider()):
        app = EndlessCodeApp([provider_config], Registry(), engine=engine)
        async with app.run_test() as pilot:
            for _ in range(50):
                await pilot.pause()
                if app.state is SessionState.IDLE:
                    break
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
            app._command_audit()
            text = "\n".join(line.text for line in app._chat.lines)
            assert "permission_checked" in text
            assert "src/a.py" in text
            assert "risk=medium" in text
