"""会话级延迟工具可见性、激活与执行拦截。"""

import json
import threading

from endless_code.llm import ToolDefinition
from endless_code.permission import Mode
from endless_code.tool import Registry, Result

TOOL_SEARCH_NAME = "ToolSearch"
_TOOL_SEARCH_DEFINITION = ToolDefinition(
    name=TOOL_SEARCH_NAME,
    description="Load one or more deferred MCP tools by exact name.",
    input_schema={
        "type": "object",
        "properties": {
            "names": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "uniqueItems": True,
            }
        },
        "required": ["names"],
        "additionalProperties": False,
    },
)


class SessionToolSet:
    """基于全量 Registry 构建当前 Agent 会话的可见工具集。"""

    def __init__(self, registry: Registry) -> None:
        if (
            registry.is_deferred(TOOL_SEARCH_NAME)
            or registry.get(TOOL_SEARCH_NAME) is not None
        ):
            raise ValueError(f"工具名与内置 {TOOL_SEARCH_NAME} 冲突")
        self._registry = registry
        self._active: set[str] = set()
        self._lock = threading.RLock()

    def has_deferred(self) -> bool:
        return bool(self._registry.deferred_names())

    def definitions(self, mode: Mode) -> list[ToolDefinition]:
        with self._lock:
            active = set(self._active)
        definitions: list[ToolDefinition] = []
        for name in self._registry.names():
            if self._registry.is_deferred(name):
                continue
            if mode is Mode.PLAN and not self._registry.is_read_only(name):
                continue
            definition = self._registry.definition(name)
            if definition is not None:
                definitions.append(definition)
        if self.has_deferred():
            definitions.append(_TOOL_SEARCH_DEFINITION)
        for name in self._registry.deferred_names(read_only_only=mode is Mode.PLAN):
            if name not in active:
                continue
            definition = self._registry.definition(name)
            if definition is not None:
                definitions.append(definition)
        return definitions

    def inactive_names(self, mode: Mode) -> list[str]:
        with self._lock:
            active = set(self._active)
        return [
            name
            for name in self._registry.deferred_names(read_only_only=mode is Mode.PLAN)
            if name not in active
        ]

    def active_snapshot(self, mode: Mode) -> frozenset[str]:
        allowed = set(self._registry.deferred_names(read_only_only=mode is Mode.PLAN))
        with self._lock:
            return frozenset(self._active & allowed)

    def known(self, name: str) -> bool:
        return (name == TOOL_SEARCH_NAME and self.has_deferred()) or self._registry.get(
            name
        ) is not None

    def is_read_only(self, name: str) -> bool:
        if name == TOOL_SEARCH_NAME and self.has_deferred():
            return True
        return self._registry.is_read_only(name)

    def blocked_result(
        self,
        name: str,
        mode: Mode,
        active_at_round_start: frozenset[str],
    ) -> Result | None:
        if not self._registry.is_deferred(name):
            return None
        if mode is Mode.PLAN and not self._registry.is_read_only(name):
            return Result(
                content=f"工具 {name} 在 Plan Mode 中不可用",
                is_error=True,
            )
        if name not in active_at_round_start:
            return Result(
                content=f"工具 {name} 尚未加载；请先使用 {TOOL_SEARCH_NAME} 激活",
                is_error=True,
            )
        return None

    async def execute(
        self,
        name: str,
        args: str,
        mode: Mode,
        active_at_round_start: frozenset[str],
    ) -> Result:
        if name == TOOL_SEARCH_NAME and self.has_deferred():
            return self._search(args, mode)
        blocked = self.blocked_result(name, mode, active_at_round_start)
        if blocked is not None:
            return blocked
        return await self._registry.execute(name, args)

    def reset(self) -> None:
        with self._lock:
            self._active.clear()

    def _search(self, args: str, mode: Mode) -> Result:
        names = _parse_names(args)
        if names is None:
            return Result(
                content=(
                    f"{TOOL_SEARCH_NAME} 参数无效：names 必须是至少包含"
                    "一个非空字符串的数组"
                ),
                is_error=True,
            )
        allowed = set(self._registry.deferred_names(read_only_only=mode is Mode.PLAN))
        activated: list[str] = []
        already_active: list[str] = []
        not_found: list[str] = []
        with self._lock:
            for name in names:
                if name not in allowed:
                    not_found.append(name)
                elif name in self._active:
                    already_active.append(name)
                else:
                    self._active.add(name)
                    activated.append(name)
        content = json.dumps(
            {
                "activated": activated,
                "already_active": already_active,
                "not_found": not_found,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return Result(content=content)


def _parse_names(raw: str) -> list[str] | None:
    try:
        data = json.loads(raw.strip() or "{}")
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict) or set(data) != {"names"}:
        return None
    raw_names = data["names"]
    if not isinstance(raw_names, list) or not raw_names:
        return None
    names: list[str] = []
    seen: set[str] = set()
    for name in raw_names:
        if not isinstance(name, str) or not name:
            return None
        if name not in seen:
            names.append(name)
            seen.add(name)
    return names
