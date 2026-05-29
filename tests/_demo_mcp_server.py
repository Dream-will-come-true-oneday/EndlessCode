"""最小 MCP stdio server：用于端到端验证 endless-code 的 MCP 客户端。

提供两个工具：
- echo: 原样返回传入的 text
- add:  两个整数相加
"""

import asyncio

import mcp.types as mtypes
from mcp.server import Server
from mcp.server.stdio import stdio_server

server = Server("demo-server")

_TOOLS = [
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
            "properties": {
                "a": {"type": "integer"},
                "b": {"type": "integer"},
            },
            "required": ["a", "b"],
        },
    ),
]


async def _handle_list_tools(context, params) -> mtypes.ListToolsResult:
    return mtypes.ListToolsResult(tools=_TOOLS)


async def _handle_call_tool(context, params) -> mtypes.CallToolResult:
    name = params.name
    arguments = params.arguments or {}
    if name == "echo":
        text = str(arguments.get("text", ""))
        return mtypes.CallToolResult(
            content=[mtypes.TextContent(type="text", text=f"echo: {text}")],
            is_error=False,
        )
    if name == "add":
        a = int(arguments.get("a", 0))
        b = int(arguments.get("b", 0))
        return mtypes.CallToolResult(
            content=[mtypes.TextContent(type="text", text=str(a + b))],
            is_error=False,
        )
    return mtypes.CallToolResult(
        content=[mtypes.TextContent(type="text", text=f"未知工具: {name}")],
        is_error=True,
    )


server.add_request_handler(
    "tools/list", mtypes.PaginatedRequestParams, _handle_list_tools
)
server.add_request_handler(
    "tools/call", mtypes.CallToolRequestParams, _handle_call_tool
)


async def _main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(_main())
