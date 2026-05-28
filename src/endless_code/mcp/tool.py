"""MCP 工具适配：把远端工具映射为 endless_code 的 Tool 协议。"""

import asyncio
import json
import re
import sys
from typing import Any, Protocol

import mcp.types as mtypes

from endless_code.tool import Result

_VALID_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_EXECUTE_TIMEOUT: float = 30.0

# 记录已告警过非 text 内容块的 full_name（每工具限一次）。
_non_text_warn_once: set[str] = set()


class CallerSession(Protocol):
    """远端正则会话最小接口，便于单测注入 stub。"""

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None
    ) -> mtypes.CallToolResult: ...


class McpTool:
    """适配 endless_code ``Tool`` 协议的 MCP 工具。"""

    def __init__(
        self,
        full_name: str,
        remote_name: str,
        description: str,
        schema: dict[str, Any],
        read_only: bool,
        caller: CallerSession,
    ) -> None:
        self.full_name = full_name  # "mcp__<server>__<tool>"
        self.remote_name = remote_name  # server 上的原始工具名
        self.read_only = read_only  # 仅来自远端 annotations.read_only_hint==True
        self._description = description
        self._schema = schema
        self.caller = caller  # 协议形式持有，便于单测注入 stub

    # --- endless_code Tool 协议实现 ---

    def name(self) -> str:
        return self.full_name

    def description(self) -> str:
        return self._description

    def parameters(self) -> dict[str, Any]:
        return self._schema

    async def execute(self, args: str) -> Result:
        """解析 JSON 参数串并转发给远端；超时/协议错转 is_error。"""
        arg_map = _parse_args(args)
        if arg_map is None:
            return Result(content="MCP 工具参数 JSON 解析失败", is_error=True)

        try:
            result = await asyncio.wait_for(
                self.caller.call_tool(self.remote_name, arg_map),
                timeout=_EXECUTE_TIMEOUT,
            )
        except TimeoutError:
            return Result(
                content=f"MCP 工具调用超时 ({int(_EXECUTE_TIMEOUT)}s)", is_error=True
            )
        except Exception as exc:  # noqa: BLE001
            return Result(content=f"MCP 工具调用失败: {exc}", is_error=True)

        texts: list[str] = []
        dropped = False
        for block in result.content:
            if isinstance(block, mtypes.TextContent):
                texts.append(block.text)
            else:
                dropped = True

        if dropped and self.full_name not in _non_text_warn_once:
            _non_text_warn_once.add(self.full_name)
            print(
                f"[mcp] warn: tool {self.full_name} returned "
                "non-text content blocks (dropped)",
                file=sys.stderr,
            )

        return Result(content="\n".join(texts), is_error=bool(result.is_error))


def _parse_args(raw: str) -> dict[str, Any] | None:
    """把 agent 传来的 JSON 字符串解析为 dict；空/非法返回 None。"""
    s = raw.strip() or "{}"
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def adapt_tool(
    server_name: str, t: mtypes.Tool, session: CallerSession
) -> McpTool | None:
    """把远端 ``Tool`` 适配为 ``McpTool``；非法命名返回 None。"""
    full_name = f"mcp__{server_name}__{t.name}"
    if not _VALID_NAME.fullmatch(full_name):
        print(
            f"[mcp] warn: skip tool {full_name}: name contains illegal characters",
            file=sys.stderr,
        )
        return None

    description = t.description or f"来自 MCP server {server_name} 的工具 {t.name}"

    raw_schema: Any = t.input_schema
    if isinstance(raw_schema, dict) and raw_schema:
        parameters = dict(raw_schema)
    else:
        parameters = {"type": "object"}

    read_only = bool(
        t.annotations is not None and getattr(t.annotations, "read_only_hint", False)
    )

    return McpTool(
        full_name=full_name,
        remote_name=t.name,
        description=description,
        schema=parameters,
        read_only=read_only,
        caller=session,
    )
