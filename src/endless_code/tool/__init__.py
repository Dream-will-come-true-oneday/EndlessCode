"""工具层：统一抽象、注册中心、执行与结果。"""

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from endless_code.llm import ToolDefinition

DEFAULT_TIMEOUT: float = 30.0


@dataclass
class Result:
    """工具执行结果——永远以值类型返回，从不抛异常给上层。"""

    content: str
    is_error: bool = False


@runtime_checkable
class Tool(Protocol):
    """统一工具抽象。"""

    read_only: bool

    def name(self) -> str: ...
    def description(self) -> str: ...
    def parameters(self) -> dict[str, Any]: ...
    async def execute(self, args: str) -> Result: ...


def _truncate(s: str, max_lines: int = 2000, max_chars: int = 256_000) -> str:
    """超出上限时截断并标注。"""
    truncated = False
    if len(s) > max_chars:
        s = s[:max_chars]
        truncated = True
    lines = s.splitlines(keepends=True)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True
    result = "".join(lines)
    if truncated:
        result += "\n[truncated]"
    return result


class Registry:
    """集中登记、按名查找、导出定义、按名执行。"""

    def __init__(self) -> None:
        self._order: list[str] = []
        self._tools: dict[str, Tool] = {}
        self._deferred: set[str] = set()

    def register(self, t: Tool, *, deferred: bool = False) -> None:
        n = t.name()
        if n in self._tools:
            raise ValueError(f"工具名重复: {n}")
        self._order.append(n)
        self._tools[n] = t
        if deferred:
            self._deferred.add(n)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        """按注册顺序返回工具名称副本。"""
        return list(self._order)

    def definition(self, name: str) -> ToolDefinition | None:
        """返回单个工具完整定义；未知名称返回 None。"""
        tool = self.get(name)
        if tool is None:
            return None
        return ToolDefinition(
            name=tool.name(),
            description=tool.description(),
            input_schema=tool.parameters(),
        )

    def is_deferred(self, name: str) -> bool:
        """已注册且标记为延迟工具时返回 True。"""
        return name in self._deferred

    def deferred_names(self, *, read_only_only: bool = False) -> list[str]:
        """按注册顺序返回延迟工具名称。"""
        return [
            name
            for name in self._order
            if name in self._deferred
            and (not read_only_only or self._tools[name].read_only)
        ]

    def definitions(self) -> list[ToolDefinition]:
        return [definition for n in self._order if (definition := self.definition(n))]

    def read_only_definitions(self) -> list[ToolDefinition]:
        """按注册顺序导出只读工具定义。"""
        return [
            definition
            for n in self._order
            if self._tools[n].read_only and (definition := self.definition(n))
        ]

    def is_read_only(self, name: str) -> bool:
        """已注册且标记为只读时返回 True。"""
        tool = self.get(name)
        return tool is not None and tool.read_only

    async def execute(
        self, name: str, args: str, timeout: float = DEFAULT_TIMEOUT
    ) -> Result:
        tool = self.get(name)
        if tool is None:
            return Result(content=f"未知工具: {name}", is_error=True)
        try:
            return await asyncio.wait_for(tool.execute(args), timeout)
        except TimeoutError:
            return Result(content=f"工具 {name} 执行超时（{timeout}s）", is_error=True)
        except Exception as e:  # noqa: BLE001
            return Result(content=f"工具 {name} 异常: {e}", is_error=True)


def new_default_registry() -> Registry:
    """构造并注册 6 个核心工具。"""
    from endless_code.tool.bash import BashTool
    from endless_code.tool.edit_file import EditFileTool
    from endless_code.tool.glob_tool import GlobTool
    from endless_code.tool.grep_tool import GrepTool
    from endless_code.tool.read_file import ReadFileTool
    from endless_code.tool.write_file import WriteFileTool

    r = Registry()
    r.register(ReadFileTool())
    r.register(WriteFileTool())
    r.register(EditFileTool())
    r.register(BashTool())
    r.register(GlobTool())
    r.register(GrepTool())
    return r
