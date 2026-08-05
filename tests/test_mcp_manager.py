"""MCP 连接管理器测试：连接/失败隔离/超时/close 兜底。"""

import asyncio

import mcp.types as mtypes
import pytest

import endless_code.mcp.manager as manager_mod
from endless_code.mcp.config import Config, ServerConfig
from endless_code.mcp.manager import Manager, new_manager


def _cfg(servers: dict) -> Config:
    return Config(servers=servers)


def _stdio(name: str = "demo", command: str = "npx") -> ServerConfig:
    return ServerConfig(type="stdio", command=command)


async def _stub_do_connect(
    mgr: Manager, name: str, srv: ServerConfig, version: str, conn
) -> None:
    """注入版 _do_connect：注册工具后置位 established 并保持活跃。"""
    from endless_code.mcp.tool import McpTool

    class StubSession:
        async def call_tool(self, name, arguments):
            return mtypes.CallToolResult(
                content=[mtypes.TextContent(type="text", text="stub")],
                is_error=False,
            )

    tool = McpTool(
        full_name=f"mcp__{name}__tool1",
        remote_name="tool1",
        description="stub",
        schema={"type": "object"},
        read_only=False,
        caller=StubSession(),
    )
    async with mgr._lock:
        mgr._tools.append(tool)
    conn.established.set()
    await conn.shutdown.wait()


async def _stub_fail_connect(mgr, name, srv, version, conn) -> None:
    """注入版 _do_connect：立即失败并告警。"""
    conn.established.set()
    raise RuntimeError("connection refused")


@pytest.mark.asyncio
async def test_empty_config(monkeypatch) -> None:
    monkeypatch.setattr(manager_mod, "connect_timeout", 1.0)
    mgr = await new_manager(_cfg({}), version="0.1.0")
    assert mgr.tools() == []
    await mgr.close()


@pytest.mark.asyncio
async def test_connect_success_sorted(monkeypatch) -> None:
    """多个 server 连接成功，工具按 full_name 稳定排序。"""
    monkeypatch.setattr(manager_mod, "_do_connect", _stub_do_connect)
    monkeypatch.setattr(manager_mod, "connect_timeout", 2.0)
    cfg = _cfg({"beta": _stdio("beta"), "alpha": _stdio("alpha")})
    mgr = await new_manager(cfg, version="0.1.0")
    names = [t.full_name for t in mgr.tools()]
    assert names == ["mcp__alpha__tool1", "mcp__beta__tool1"]
    assert names == sorted(names)
    await mgr.close()


@pytest.mark.asyncio
async def test_connect_failure_isolated(monkeypatch, capsys) -> None:
    """一个 server 连接失败，另一个成功；失败只告警不影响成功者。"""

    async def flaky_connect(mgr, name, srv, version, conn):
        if name == "bad":
            await _stub_fail_connect(mgr, name, srv, version, conn)
        else:
            await _stub_do_connect(mgr, name, srv, version, conn)

    monkeypatch.setattr(manager_mod, "_do_connect", flaky_connect)
    monkeypatch.setattr(manager_mod, "connect_timeout", 2.0)
    cfg = _cfg(
        {
            "bad": _stdio("bad"),
            "good": _stdio("good"),
        }
    )
    mgr = await new_manager(cfg, version="0.1.0")
    names = [t.full_name for t in mgr.tools()]
    assert names == ["mcp__good__tool1"]
    assert "connect server bad failed" in capsys.readouterr().err
    await mgr.close()


@pytest.mark.asyncio
async def test_connect_timeout(monkeypatch, capsys) -> None:
    """卡住的连接在超时窗口内被跳过。"""

    async def stuck_connect(mgr, name, srv, version, conn):
        await asyncio.Event().wait()  # 永不 set established

    monkeypatch.setattr(manager_mod, "_do_connect", stuck_connect)
    monkeypatch.setattr(manager_mod, "connect_timeout", 0.2)
    cfg = _cfg({"slow": _stdio("slow")})
    mgr = await new_manager(cfg, version="0.1.0")
    assert mgr.tools() == []
    assert "connect timeout" in capsys.readouterr().err
    await mgr.close()


@pytest.mark.asyncio
async def test_close_immediately_returns(monkeypatch) -> None:
    """空 manager 的 close 立即返回。"""
    monkeypatch.setattr(manager_mod, "connect_timeout", 1.0)
    mgr = await new_manager(_cfg({}), version="0.1.0")
    await mgr.close()  # 不抛异常


@pytest.mark.asyncio
async def test_close_signals_connections(monkeypatch) -> None:
    """close 触发已连接 server 的 shutdown，worker 干净退出。"""
    monkeypatch.setattr(manager_mod, "_do_connect", _stub_do_connect)
    monkeypatch.setattr(manager_mod, "connect_timeout", 2.0)
    cfg = _cfg({"demo": _stdio()})
    mgr = await new_manager(cfg, version="0.1.0")
    assert len(mgr.tools()) == 1
    await mgr.close()
    # 连接 task 应在 close 后完成
    for conn in mgr._connections:
        assert conn.task.done()


@pytest.mark.asyncio
async def test_manager_tools_returns_copy(monkeypatch) -> None:
    monkeypatch.setattr(manager_mod, "_do_connect", _stub_do_connect)
    monkeypatch.setattr(manager_mod, "connect_timeout", 2.0)
    cfg = _cfg({"demo": _stdio()})
    mgr = await new_manager(cfg, version="0.1.0")
    tools = mgr.tools()
    assert len(tools) == 1
    tools.clear()
    assert len(mgr.tools()) == 1  # 原对象不受影响
    await mgr.close()
