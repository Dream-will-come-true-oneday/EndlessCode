"""会话级 MCP 工具延迟加载测试。"""

import asyncio
import json
import math
from dataclasses import asdict

import pytest

from endless_code.permission import Mode
from endless_code.prompt import deferred_tools_reminder
from endless_code.tool import Registry, Result
from endless_code.tool.deferred import TOOL_SEARCH_NAME, SessionToolSet


class FakeTool:
    def __init__(
        self, name: str, *, read_only: bool = True, rich: bool = False
    ) -> None:
        self._name = name
        self.read_only = read_only
        self.calls: list[str] = []
        self._rich = rich

    def name(self) -> str:
        return self._name

    def description(self) -> str:
        if not self._rich:
            return f"Use {self._name} for a remote operation."
        return (
            f"Use {self._name} for repository analysis and automation. " * 20
        ).strip()

    def parameters(self) -> dict:
        if not self._rich:
            return {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            }
        properties = {
            f"field_{index}": {
                "type": "string",
                "description": (
                    f"Detailed input field {index} for {self._name}. " * 6
                ).strip(),
            }
            for index in range(12)
        }
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties)[:6],
            "additionalProperties": False,
        }

    async def execute(self, args: str) -> Result:
        self.calls.append(args)
        return Result(content=f"executed:{self._name}")


def _registry() -> tuple[Registry, FakeTool, FakeTool]:
    registry = Registry()
    registry.register(FakeTool("read_file"))
    read = FakeTool("mcp__demo__read", read_only=True)
    write = FakeTool("mcp__demo__write", read_only=False)
    registry.register(read, deferred=True)
    registry.register(write, deferred=True)
    return registry, read, write


def _names(toolset: SessionToolSet, mode: Mode = Mode.DEFAULT) -> list[str]:
    return [item.name for item in toolset.definitions(mode)]


def test_deferred_first_request_definitions_and_catalog() -> None:
    registry, _, _ = _registry()
    toolset = SessionToolSet(registry)

    assert _names(toolset) == ["read_file", TOOL_SEARCH_NAME]
    assert toolset.inactive_names(Mode.DEFAULT) == [
        "mcp__demo__read",
        "mcp__demo__write",
    ]
    assert toolset.active_snapshot(Mode.DEFAULT) == frozenset()
    assert toolset.known(TOOL_SEARCH_NAME)
    assert toolset.is_read_only(TOOL_SEARCH_NAME)


@pytest.mark.asyncio
async def test_tool_search_batch_idempotent_and_not_found() -> None:
    registry, _, _ = _registry()
    toolset = SessionToolSet(registry)
    result = await toolset.execute(
        TOOL_SEARCH_NAME,
        json.dumps(
            {
                "names": [
                    "mcp__demo__read",
                    "mcp__missing__tool",
                    "mcp__demo__write",
                    "mcp__demo__read",
                ]
            }
        ),
        Mode.DEFAULT,
        frozenset(),
    )
    assert not result.is_error
    assert json.loads(result.content) == {
        "activated": ["mcp__demo__read", "mcp__demo__write"],
        "already_active": [],
        "not_found": ["mcp__missing__tool"],
    }
    assert _names(toolset) == [
        "read_file",
        TOOL_SEARCH_NAME,
        "mcp__demo__read",
        "mcp__demo__write",
    ]

    repeated = await toolset.execute(
        TOOL_SEARCH_NAME,
        '{"names":["mcp__demo__write"]}',
        Mode.DEFAULT,
        frozenset(),
    )
    assert json.loads(repeated.content)["already_active"] == ["mcp__demo__write"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        "{}",
        '{"names":[]}',
        '{"names":[1]}',
        '{"names":[""]}',
        '{"names":["mcp__demo__read"],"extra":true}',
    ],
)
async def test_tool_search_invalid_input_does_not_change_state(raw: str) -> None:
    registry, _, _ = _registry()
    toolset = SessionToolSet(registry)
    result = await toolset.execute(TOOL_SEARCH_NAME, raw, Mode.DEFAULT, frozenset())
    assert result.is_error
    assert toolset.active_snapshot(Mode.DEFAULT) == frozenset()


@pytest.mark.asyncio
async def test_deferred_plan_mode_filters_catalog_activation_and_definitions() -> None:
    registry, _, _ = _registry()
    toolset = SessionToolSet(registry)
    default_result = await toolset.execute(
        TOOL_SEARCH_NAME,
        '{"names":["mcp__demo__write"]}',
        Mode.DEFAULT,
        frozenset(),
    )
    assert not default_result.is_error

    assert toolset.inactive_names(Mode.PLAN) == ["mcp__demo__read"]
    assert "mcp__demo__write" not in _names(toolset, Mode.PLAN)
    plan_result = await toolset.execute(
        TOOL_SEARCH_NAME,
        '{"names":["mcp__demo__write","mcp__demo__read"]}',
        Mode.PLAN,
        toolset.active_snapshot(Mode.PLAN),
    )
    assert json.loads(plan_result.content) == {
        "activated": ["mcp__demo__read"],
        "already_active": [],
        "not_found": ["mcp__demo__write"],
    }
    assert _names(toolset, Mode.PLAN) == [
        "read_file",
        TOOL_SEARCH_NAME,
        "mcp__demo__read",
    ]


@pytest.mark.asyncio
async def test_same_response_snapshot_blocks_newly_activated_tool() -> None:
    registry, read, _ = _registry()
    toolset = SessionToolSet(registry)
    round_start = toolset.active_snapshot(Mode.DEFAULT)
    await toolset.execute(
        TOOL_SEARCH_NAME,
        '{"names":["mcp__demo__read"]}',
        Mode.DEFAULT,
        round_start,
    )

    blocked = await toolset.execute(
        "mcp__demo__read", '{"query":"x"}', Mode.DEFAULT, round_start
    )
    assert blocked.is_error
    assert TOOL_SEARCH_NAME in blocked.content
    assert read.calls == []

    next_round = toolset.active_snapshot(Mode.DEFAULT)
    executed = await toolset.execute(
        "mcp__demo__read", '{"query":"x"}', Mode.DEFAULT, next_round
    )
    assert not executed.is_error
    assert read.calls == ['{"query":"x"}']


@pytest.mark.asyncio
async def test_reset_clears_deferred_activation() -> None:
    registry, _, _ = _registry()
    toolset = SessionToolSet(registry)
    await toolset.execute(
        TOOL_SEARCH_NAME,
        '{"names":["mcp__demo__read"]}',
        Mode.DEFAULT,
        frozenset(),
    )
    assert "mcp__demo__read" in _names(toolset)
    toolset.reset()
    assert "mcp__demo__read" not in _names(toolset)


@pytest.mark.asyncio
async def test_concurrent_activation_is_complete() -> None:
    registry = Registry()
    names = [f"mcp__demo__tool_{index:03}" for index in range(100)]
    for name in names:
        registry.register(FakeTool(name), deferred=True)
    toolset = SessionToolSet(registry)

    await asyncio.gather(
        *(
            toolset.execute(
                TOOL_SEARCH_NAME,
                json.dumps({"names": names[start : start + 25]}),
                Mode.DEFAULT,
                frozenset(),
            )
            for start in range(0, 100, 10)
        )
    )
    assert toolset.active_snapshot(Mode.DEFAULT) == frozenset(names)


def test_deterministic_order_and_scale_boundaries() -> None:
    empty = SessionToolSet(Registry())
    assert empty.definitions(Mode.DEFAULT) == []
    assert empty.inactive_names(Mode.DEFAULT) == []

    registry = Registry()
    for index in range(300):
        registry.register(FakeTool(f"mcp__scale__tool_{index:03}"), deferred=True)
    first = SessionToolSet(registry)
    second = SessionToolSet(registry)
    assert first.inactive_names(Mode.DEFAULT) == second.inactive_names(Mode.DEFAULT)
    assert _names(first) == _names(second) == [TOOL_SEARCH_NAME]


@pytest.mark.asyncio
async def test_token_benchmark_reduces_at_least_80_percent() -> None:
    registry = Registry()
    names = [f"mcp__benchmark__tool_{index:02}" for index in range(58)]
    for name in names:
        registry.register(FakeTool(name, rich=True), deferred=True)
    toolset = SessionToolSet(registry)

    eager_chars = 0
    lazy_chars = 0
    activation_rounds = {2: names[0], 4: names[20], 6: names[40]}
    eager_payload = json.dumps(
        [asdict(item) for item in registry.definitions()],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    for round_index in range(10):
        if round_index in activation_rounds:
            await toolset.execute(
                TOOL_SEARCH_NAME,
                json.dumps({"names": [activation_rounds[round_index]]}),
                Mode.DEFAULT,
                toolset.active_snapshot(Mode.DEFAULT),
            )
        eager_chars += len(eager_payload)
        lazy_payload = json.dumps(
            [asdict(item) for item in toolset.definitions(Mode.DEFAULT)],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + deferred_tools_reminder(toolset.inactive_names(Mode.DEFAULT))
        lazy_chars += len(lazy_payload)

    eager_tokens = math.ceil(eager_chars / 3.5)
    lazy_tokens = math.ceil(lazy_chars / 3.5)
    reduction = 1 - (lazy_tokens / eager_tokens)
    print(
        f"eager_tokens={eager_tokens} lazy_tokens={lazy_tokens} "
        f"reduction={reduction:.2%}"
    )
    assert reduction >= 0.80
