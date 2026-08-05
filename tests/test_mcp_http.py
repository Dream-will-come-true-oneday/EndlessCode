"""MCP HTTP 连接端到端测试：Streamable HTTP 握手、列工具、调用、自定义 headers。

用 uvicorn 起一个真实本地 HTTP MCP server（ASGI），客户端经 endless_code.mcp 连接，
断言：工具被注册、调用成功、服务端每个请求都收到自定义 Authorization 头、退出干净。
"""

import asyncio
import socket
from dataclasses import dataclass, field

import mcp.types as mtypes
import pytest
import uvicorn
from mcp.server import Server
from mcp.server.streamable_http_manager import (
    StreamableHTTPASGIApp,
    StreamableHTTPSessionManager,
)

from endless_code.mcp.config import Config, ServerConfig
from endless_code.mcp.manager import new_manager

TOOLS = [
    mtypes.Tool(
        name="echo",
        description="回显文本",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    ),
    mtypes.Tool(
        name="add",
        description="两个整数相加",
        input_schema={
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    ),
]


async def _handle_list_tools(context, params) -> mtypes.ListToolsResult:
    return mtypes.ListToolsResult(tools=TOOLS)


async def _handle_call_tool(context, params) -> mtypes.CallToolResult:
    name = params.name
    arguments = params.arguments or {}
    if name == "echo":
        return mtypes.CallToolResult(
            content=[
                mtypes.TextContent(
                    type="text", text=f"echo: {arguments.get('text', '')}"
                )
            ],
            is_error=False,
        )
    if name == "add":
        return mtypes.CallToolResult(
            content=[
                mtypes.TextContent(
                    type="text",
                    text=str(int(arguments.get("a", 0)) + int(arguments.get("b", 0))),
                )
            ],
            is_error=False,
        )
    return mtypes.CallToolResult(
        content=[mtypes.TextContent(type="text", text=f"未知工具: {name}")],
        is_error=True,
    )


@dataclass
class HttpServer:
    """运行中的本地 HTTP MCP server 句柄。"""

    port: int
    _uvicorn: uvicorn.Server
    _task: asyncio.Task
    _sock: socket.socket
    received_headers: list[dict[str, str]] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    async def stop(self) -> None:
        self._uvicorn.should_exit = True
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except Exception:  # noqa: BLE001 —— 无论超时还是任务失败，都取消后继续清理
            self._task.cancel()
        self._sock.close()


async def _serve() -> HttpServer:
    """起一个本地 HTTP MCP server（预绑定 socket，端口确定后交给 uvicorn）。"""
    server = Server("http-demo")
    server.add_request_handler(
        "tools/list", mtypes.PaginatedRequestParams, _handle_list_tools
    )
    server.add_request_handler(
        "tools/call", mtypes.CallToolRequestParams, _handle_call_tool
    )

    manager = StreamableHTTPSessionManager(server)
    app = StreamableHTTPASGIApp(manager)
    ready = asyncio.Event()

    received_headers: list[dict[str, str]] = []

    async def wrapper(scope, receive, send) -> None:
        # 会话管理器需在 lifespan 内 run() 初始化 task group
        if scope["type"] == "lifespan":
            async with manager.run():
                while True:
                    message = await receive()
                    if message["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                        ready.set()
                    elif message["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        break
            return
        if scope["type"] == "http":
            headers = {
                k.decode().lower(): v.decode() for k, v in scope.get("headers", [])
            }
            received_headers.append(headers)
        await app(scope, receive, send)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]

    uvicorn_config = uvicorn.Config(
        wrapper, host="127.0.0.1", port=port, log_level="error"
    )
    uvicorn_server = uvicorn.Server(uvicorn_config)
    task = asyncio.create_task(uvicorn_server.serve(sockets=[sock]))
    # 等待 lifespan startup 完成（task group 就绪），避免客户端抢先连接
    try:
        await asyncio.wait_for(ready.wait(), timeout=5.0)
    except TimeoutError:
        task.cancel()
        sock.close()
        raise RuntimeError("HTTP server lifespan 未能在 5s 内就绪")
    return HttpServer(
        port=port,
        _uvicorn=uvicorn_server,
        _task=task,
        _sock=sock,
        received_headers=received_headers,
    )


@pytest.mark.asyncio
async def test_http_connection_headers_and_call(monkeypatch) -> None:
    http = await _serve()
    # 缩短超时，避免挂住
    import endless_code.mcp.manager as manager_mod

    monkeypatch.setattr(manager_mod, "connect_timeout", 10.0)
    monkeypatch.setattr(manager_mod, "close_timeout", 5.0)

    cfg = Config(
        servers={
            "http-demo": ServerConfig(
                type="http",
                url=http.url,
                headers={"Authorization": "Bearer test-token"},
            )
        }
    )
    mgr = await new_manager(cfg, version="0.1.0")
    try:
        names = [t.full_name for t in mgr.tools()]
        assert "mcp__http-demo__echo" in names, f"工具未注册: {names}"
        assert "mcp__http-demo__add" in names, f"工具未注册: {names}"

        echo = next(t for t in mgr.tools() if t.full_name == "mcp__http-demo__echo")
        result = await echo.execute('{"text": "over http"}')
        assert result.content.strip() == "echo: over http", result.content
        assert result.is_error is False

        add = next(t for t in mgr.tools() if t.full_name == "mcp__http-demo__add")
        result = await add.execute('{"a": 20, "b": 22}')
        assert result.content.strip() == "42", result.content
    finally:
        await mgr.close()
        await http.stop()

    # 断言服务端每个请求都带上了 Authorization 头
    auths = [
        h.get("authorization") for h in http.received_headers if h.get("authorization")
    ]
    assert auths, f"服务端未收到 Authorization 头: {http.received_headers}"
    assert all(a == "Bearer test-token" for a in auths), (
        f"Authorization 头值不符: {auths}"
    )
