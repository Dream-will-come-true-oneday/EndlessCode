"""MCP catalog, pagination, lazy exposure, and Agent integration tests."""

import json

import mcp.types as mtypes
import pytest

from endless_code.agent import Agent
from endless_code.conversation import Conversation
from endless_code.llm import StreamEvent, ToolCall
from endless_code.mcp.catalog import McpCatalog
from endless_code.mcp.manager import _list_tool_pages
from endless_code.mcp.tool import McpSearchTool, McpTool
from endless_code.permission import Mode, new_engine
from endless_code.tool import Registry


class _Caller:
    async def call_tool(self, name, arguments):
        return mtypes.CallToolResult(
            content=[mtypes.TextContent(type="text", text=f"called {name}")]
        )


def _mcp_tool(name: str, description: str = "") -> McpTool:
    return McpTool(
        full_name=f"mcp__demo__{name}",
        remote_name=name,
        description=description or name,
        schema={"type": "object", "properties": {"q": {"type": "string"}}},
        read_only=False,
        caller=_Caller(),
    )


@pytest.mark.asyncio
async def test_tools_list_pagination_consumes_next_cursor() -> None:
    class Session:
        def __init__(self):
            self.cursors = []

        async def list_tools(self, *, params=None, cursor=None):
            value = params.cursor if params is not None else cursor
            self.cursors.append(value)
            if value is None:
                return mtypes.ListToolsResult(
                    tools=[mtypes.Tool(name="one", inputSchema={})], nextCursor="p2"
                )
            return mtypes.ListToolsResult(
                tools=[mtypes.Tool(name="two", inputSchema={})], nextCursor=None
            )

    session = Session()
    pages = [page async for page in _list_tool_pages(session)]
    assert [tool.name for page in pages for tool in page.tools] == ["one", "two"]
    assert session.cursors == [None, "p2"]


@pytest.mark.asyncio
async def test_search_activates_hidden_tools_and_returns_no_schema() -> None:
    catalog = McpCatalog()
    hidden = _mcp_tool("search", "search remote repository")
    catalog.add(hidden)
    registry = Registry()
    registry.register(hidden)
    search = McpSearchTool(catalog)
    registry.register(search)

    initial = [item.name for item in registry.definitions()]
    assert initial == ["mcp_search_tools"]
    result = await search.execute(json.dumps({"query": "repository", "limit": 5}))
    payload = json.loads(result.content)
    assert payload["tools"][0]["name"] == "mcp__demo__search"
    assert "schema" not in result.content
    assert "input_schema" not in result.content
    assert [item.name for item in registry.definitions()] == [
        "mcp__demo__search",
        "mcp_search_tools",
    ]
    await search.execute(json.dumps({"query": "repository", "limit": 5}))
    assert [item.name for item in registry.definitions()] == [
        "mcp__demo__search",
        "mcp_search_tools",
    ]

    hidden.exposed = False
    guidance = await registry.execute(hidden.name(), "{}")
    assert guidance.is_error
    assert "mcp_search_tools" in guidance.content


class _AgentProvider:
    model = "mcp-model"
    name = "mcp"

    def __init__(self):
        self.requests = []
        self.messages = []

    async def stream(self, messages, tools, system_suffix=""):
        self.requests.append(list(tools))
        self.messages.append(list(messages))
        if len(self.requests) == 1:
            yield StreamEvent(
                tool_calls=[
                    ToolCall(
                        id="search",
                        name="mcp_search_tools",
                        input=json.dumps({"query": "repository", "limit": 5}),
                    )
                ]
            )
            yield StreamEvent(done=True)
        elif len(self.requests) == 2:
            yield StreamEvent(
                tool_calls=[
                    ToolCall(
                        id="call",
                        name="mcp__demo__search",
                        input=json.dumps({"q": "x"}),
                    )
                ]
            )
            yield StreamEvent(done=True)
        else:
            yield StreamEvent(text="complete")
            yield StreamEvent(done=True)


@pytest.mark.asyncio
async def test_agent_search_then_call_exposes_schema_next_iteration(tmp_path) -> None:
    catalog = McpCatalog()
    hidden = _mcp_tool("search", "search remote repository")
    catalog.add(hidden)
    registry = Registry()
    registry.register(hidden)
    registry.register(McpSearchTool(catalog))
    provider = _AgentProvider()
    engine, _ = new_engine(str(tmp_path))
    conv = Conversation()
    conv.add_user("find")
    agent = Agent(
        provider,
        registry,
        engine=engine,
        runtime=__import__(
            "endless_code.agent", fromlist=["new_session_runtime"]
        ).new_session_runtime(str(tmp_path)),
    )

    [event async for event in agent.run(conv, Mode.BYPASS)]
    assert [tool.name for tool in provider.requests[0]] == ["mcp_search_tools"]
    assert [tool.name for tool in provider.requests[1]] == [
        "mcp__demo__search",
        "mcp_search_tools",
    ]
    assert "mcp__demo__search" in provider.messages[0][-1].content
    assert provider.messages[0][-1].role == "user"
    assert not any(
        message.content.startswith("以下 MCP 工具尚未加载")
        for message in conv.messages()
    )
    assert not any(
        message.content.startswith("以下 MCP 工具尚未加载")
        for message in provider.messages[1]
    )
    assert registry.activated_tools == {"mcp__demo__search"}
