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
        self._exposure_overrides: dict[str, bool] = {}
        self._deferred_tools: set[str] = set()
        self._activated_tools: set[str] = set()

    def register(self, t: Tool, *, exposed: bool | None = None) -> None:
        n = t.name()
        if n in self._tools:
            raise ValueError(f"工具名重复: {n}")
        self._order.append(n)
        self._tools[n] = t
        if exposed is not None:
            self._exposure_overrides[n] = exposed
        if exposed is False or (
            exposed is None and getattr(t, "exposed", True) is False
        ):
            self._deferred_tools.add(n)
        if n == "mcp_search_tools" and hasattr(t, "set_activator"):
            t.set_activator(self._activate_from_search)

    def _activate_from_search(self, names: list[str]) -> list[str]:
        """Activate names in both Registry state and the search catalog."""
        activated = self.activate(names)
        search_tool = self.get("mcp_search_tools")
        catalog = getattr(search_tool, "catalog", None)
        if catalog is not None:
            catalog.activate(activated)
        return activated

    @property
    def activated_tools(self) -> set[str]:
        """Return a copy of the process-local deferred-tool activation set."""
        return set(self._activated_tools)

    def deferred_mcp_names(self) -> list[str]:
        """Return deferred MCP names that are not active yet, in stable order."""
        return sorted(
            name
            for name in self._deferred_tools
            if name.startswith("mcp__") and name not in self._activated_tools
        )

    def is_exposed(self, name: str) -> bool:
        """Return whether a registered tool may be sent to the model."""
        tool = self.get(name)
        if tool is None:
            return False
        if name in self._deferred_tools:
            return name in self._activated_tools and bool(
                getattr(tool, "exposed", True)
            )
        if name in self._exposure_overrides:
            return self._exposure_overrides[name]
        return bool(getattr(tool, "exposed", True))

    def activate(self, names: str | list[str]) -> list[str]:
        """Expose one or more registered tools; repeated activation is harmless."""
        values = [names] if isinstance(names, str) else names
        activated: list[str] = []
        for name in values:
            if self.get(name) is None:
                continue
            self._activated_tools.add(name)
            self._exposure_overrides[name] = True
            tool = self._tools[name]
            if hasattr(tool, "exposed"):
                tool.exposed = True
            activated.append(name)
        return activated

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def definitions(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=self._tools[n].name(),
                description=self._tools[n].description(),
                input_schema=self._tools[n].parameters(),
            )
            for n in self._order
            if self.is_exposed(n)
        ]

    def read_only_definitions(self) -> list[ToolDefinition]:
        """按注册顺序导出只读工具定义。"""
        return [
            ToolDefinition(
                name=self._tools[n].name(),
                description=self._tools[n].description(),
                input_schema=self._tools[n].parameters(),
            )
            for n in self._order
            if self._tools[n].read_only and self.is_exposed(n)
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
        if not self.is_exposed(name):
            return Result(
                content=(
                    f"MCP 工具 {name} 尚未加载，请先使用 mcp_search_tools "
                    "搜索并激活它。"
                ),
                is_error=True,
            )
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
