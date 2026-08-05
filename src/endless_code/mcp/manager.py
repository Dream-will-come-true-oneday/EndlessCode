"""MCP 连接管理器：并发连接、会话缓存、统一关闭。

mcp 2.0 的 transport/session 上下文是 anyio cancel scope，**必须在进入它的
同一协程内退出**。因此每个连接用独立的常驻 task：enter 上下文 → 初始化 →
注册工具 → 阻塞等关闭信号；close 时唤醒各 task 由它们自行退栈。

`new_manager` 只等待每个连接的“建立阶段”（established event 置位或失败），
不等待连接 task 结束（它们会一直保持活跃直到 close）。
"""

import asyncio
import os
import sys
from dataclasses import dataclass, field
from typing import Any

import mcp.types as mtypes
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from endless_code.mcp.config import Config, ServerConfig
from endless_code.mcp.tool import McpTool, adapt_tool

# 模块级变量（非常量，便于单测改小）。
connect_timeout: float = 30.0
close_timeout: float = 5.0


@dataclass
class _Connection:
    name: str
    task: asyncio.Task | None = None
    shutdown: asyncio.Event = field(default_factory=asyncio.Event)
    established: asyncio.Event = field(default_factory=asyncio.Event)


class Manager:
    """持有连接 task 与适配好的工具；close 时唤醒各连接自行退栈。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._tools: list[McpTool] = []
        self._connections: list[_Connection] = []

    def tools(self) -> list[McpTool]:
        """返回工具列表副本，防外部修改。"""
        return list(self._tools)

    async def close(self) -> None:
        """唤醒所有连接退栈；总超时兜底，绝不阻塞退出。"""
        for conn in self._connections:
            conn.shutdown.set()
        tasks = [conn.task for conn in self._connections if conn.task is not None]
        if not tasks:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=close_timeout
            )
        except TimeoutError:
            print(
                f"[mcp] warn: close timeout ({close_timeout}s), some sessions may leak",
                file=sys.stderr,
            )


def _warn(message: str) -> None:
    print(f"[mcp] warn: {message}", file=sys.stderr)


async def new_manager(cfg: Config, version: str) -> Manager:
    """并发连接所有 server；只等建立结果，不等连接 task 结束。"""
    mgr = Manager()
    for name, srv in cfg.servers.items():
        conn = _Connection(name=name)
        conn.task = asyncio.create_task(_connection_loop(mgr, name, srv, version, conn))
        async with mgr._lock:
            mgr._connections.append(conn)

    if mgr._connections:
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    *(c.established.wait() for c in mgr._connections),
                    return_exceptions=True,
                ),
                timeout=connect_timeout,
            )
        except TimeoutError:
            _warn(f"connect timeout after {connect_timeout}s")

    mgr._tools.sort(key=lambda t: t.full_name)
    return mgr


async def _connection_loop(
    mgr: Manager,
    name: str,
    srv: ServerConfig,
    version: str,
    conn: _Connection,
) -> None:
    """常驻连接协程：驱动 worker 建立会话并保持活跃直到关闭信号。"""
    worker = asyncio.create_task(_do_connect(mgr, name, srv, version, conn))
    try:
        # 只等建立阶段（established 置位 或 worker 失败）
        try:
            await asyncio.wait_for(conn.established.wait(), timeout=connect_timeout)
        except TimeoutError:
            _warn(f"connect server {name} timeout after {connect_timeout}s")
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            return

        if worker.done():
            exc = worker.exception()
            if exc is not None:
                _warn(f"connect server {name} failed: {exc}")
            return

        # 建立成功：等关闭信号，worker 内部保持活跃
        await conn.shutdown.wait()
    finally:
        if not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)


async def _do_connect(
    mgr: Manager,
    name: str,
    srv: ServerConfig,
    version: str,
    conn: _Connection,
) -> None:
    """进入 transport 上下文，建立会话、注册工具，然后保持直到关闭。"""
    try:
        if srv.type == "stdio":
            params = StdioServerParameters(
                command=srv.command,
                args=list(srv.args),
                env={**os.environ, **srv.env},
            )
            async with stdio_client(params) as transport:
                read, write = _unpack_transport(transport)
                await _serve_session(mgr, name, version, read, write, conn)
        else:  # http
            kwargs: dict[str, Any] = {}
            if srv.headers:
                kwargs["http_client"] = _httpx_client_with_headers(srv.headers)
            async with streamable_http_client(srv.url, **kwargs) as transport:
                read, write = _unpack_transport(transport)
                await _serve_session(mgr, name, version, read, write, conn)
    except asyncio.CancelledError:
        raise
    except Exception:
        # 建立阶段失败：置位 established 让 _connection_loop 感知 worker 失败
        conn.established.set()
        raise


def _unpack_transport(raw: Any) -> tuple[Any, Any]:
    """从 transport 上下文产出提取 read/write 流。"""
    if isinstance(raw, tuple):
        return raw[0], raw[1]
    return raw.read_stream, raw.write_stream


async def _serve_session(
    mgr: Manager,
    name: str,
    version: str,
    read,
    write,
    conn: _Connection,
) -> None:
    """建立会话、注册工具；然后保持活跃直到关闭信号。"""
    async with ClientSession(
        read,
        write,
        client_info=mtypes.Implementation(name="endless-code", version=version),
    ) as session:
        await session.initialize()
        listed = await session.list_tools()
        adapted = [
            tool
            for t in listed.tools
            if (tool := adapt_tool(name, t, session)) is not None
        ]
        async with mgr._lock:
            mgr._tools.extend(adapted)

        conn.established.set()

        # 保持活跃直到关闭信号
        await conn.shutdown.wait()


def _httpx_client_with_headers(headers: dict[str, str]) -> Any:
    """构造带自定义 headers 的 httpx AsyncClient。"""
    import httpx

    return httpx.AsyncClient(headers=headers)
