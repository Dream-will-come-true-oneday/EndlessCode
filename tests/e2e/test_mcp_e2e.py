"""E2E MCP 场景：真实 stdio 子进程连接、工具调用、失败隔离与断连兜底。

判定标准：
- 成功连接的 server 工具可执行并返回正确结果；
- 连接失败的 server 只跳过自身并告警，不影响其余工具；
- 运行中断连后工具调用返回 is_error 结果，绝不抛异常进 Agent。
"""

import json
import sys
from pathlib import Path

import pytest

import endless_code.mcp.manager as manager_mod
from endless_code.mcp.config import Config, ServerConfig
from endless_code.mcp.manager import new_manager

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

DEMO_SERVER = Path(__file__).resolve().parent.parent / "_demo_mcp_server.py"


def _demo_config() -> dict[str, ServerConfig]:
    return {
        "demo": ServerConfig(
            type="stdio", command=sys.executable, args=[str(DEMO_SERVER)]
        )
    }


async def test_stdio_demo_server_connect_and_call(monkeypatch) -> None:
    monkeypatch.setattr(manager_mod, "connect_timeout", 15.0)
    monkeypatch.setattr(manager_mod, "close_timeout", 5.0)
    mgr = await new_manager(Config(servers=_demo_config()), version="0.1.0")
    try:
        names = [tool.full_name for tool in mgr.tools()]
        assert "mcp__demo__echo" in names
        assert "mcp__demo__add" in names

        tools = {tool.full_name: tool for tool in mgr.tools()}
        echo_result = await tools["mcp__demo__echo"].execute(json.dumps({"text": "hi"}))
        assert echo_result.is_error is False
        assert echo_result.content == "echo: hi"

        add_result = await tools["mcp__demo__add"].execute(json.dumps({"a": 1, "b": 2}))
        assert add_result.is_error is False
        assert add_result.content == "3"
    finally:
        await mgr.close()


async def test_disconnected_tool_returns_error_result(monkeypatch) -> None:
    """断连后调用必须转为 is_error 结果，而不是把异常抛给 Agent。"""
    monkeypatch.setattr(manager_mod, "connect_timeout", 15.0)
    monkeypatch.setattr(manager_mod, "close_timeout", 5.0)
    mgr = await new_manager(Config(servers=_demo_config()), version="0.1.0")
    echo = next(tool for tool in mgr.tools() if tool.full_name == "mcp__demo__echo")
    await mgr.close()

    result = await echo.execute(json.dumps({"text": "after-close"}))
    assert result.is_error is True
    assert result.content  # 模型可读到可理解的错误文本


async def test_failed_server_isolated(monkeypatch, capsys) -> None:
    """坏 server 启动即失败：只跳过自身并告警，好 server 的工具仍可用。"""
    monkeypatch.setattr(manager_mod, "connect_timeout", 15.0)
    monkeypatch.setattr(manager_mod, "close_timeout", 5.0)
    servers = _demo_config()
    servers["bad"] = ServerConfig(
        type="stdio",
        command=sys.executable,
        args=[str(DEMO_SERVER.parent / "__no_such_server__.py")],
    )
    mgr = await new_manager(Config(servers=servers), version="0.1.0")
    try:
        names = [tool.full_name for tool in mgr.tools()]
        assert "mcp__demo__echo" in names
        assert not any(name.startswith("mcp__bad__") for name in names)
        assert "bad" in capsys.readouterr().err
    finally:
        await mgr.close()
