"""MCP 工具适配测试：命名、字段透传、execute 各分支。"""

import asyncio
import json

import mcp.types as mtypes
import pytest

from endless_code.mcp.tool import McpTool, _non_text_warn_once, adapt_tool


class StubSession:
    """注入远端调用结果的 stub 会话。"""

    def __init__(self, result: mtypes.CallToolResult) -> None:
        self._result = result
        self.calls: list[tuple[str, dict | None]] = []

    async def call_tool(
        self, name: str, arguments: dict | None
    ) -> mtypes.CallToolResult:
        self.calls.append((name, arguments))
        return self._result


class StubErrorSession:
    """call_tool 抛异常的 stub。"""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def call_tool(
        self, name: str, arguments: dict | None
    ) -> mtypes.CallToolResult:
        raise self._exc


def _make_tool(name: str = "echo", **kwargs) -> mtypes.Tool:
    params = kwargs.pop(
        "params", {"type": "object", "properties": {"text": {"type": "string"}}}
    )
    annotations = kwargs.pop("annotations", None)
    return mtypes.Tool(
        name=name,
        description=kwargs.pop("description", f"{name} tool"),
        input_schema=params,
        annotations=annotations,
    )


def _text_result(text: str, is_error: bool = False) -> mtypes.CallToolResult:
    return mtypes.CallToolResult(
        content=[mtypes.TextContent(type="text", text=text)],
        is_error=is_error,
    )


def test_adapt_tool_basic() -> None:
    t = _make_tool(name="echo")
    mt = adapt_tool("demo", t, StubSession(_text_result("hi")))
    assert isinstance(mt, McpTool)
    assert mt.full_name == "mcp__demo__echo"
    assert mt.remote_name == "echo"
    assert mt.name() == "mcp__demo__echo"
    assert mt.description() == "echo tool"
    assert mt.parameters() == {
        "type": "object",
        "properties": {"text": {"type": "string"}},
    }


def test_adapt_tool_illegal_chars_in_full_name(capsys) -> None:
    t = _make_tool(name="tool@name")
    mt = adapt_tool("bad.server", t, StubSession(_text_result("x")))
    assert mt is None
    assert "skip tool" in capsys.readouterr().err


def test_adapt_tool_empty_description_fallback() -> None:
    t = _make_tool(name="echo", description="")
    mt = adapt_tool("demo", t, StubSession(_text_result("x")))
    assert "来自 MCP server demo 的工具 echo" in mt.description()


def test_adapt_tool_none_schema_fallback() -> None:
    # 远端返回空 schema 时兜底为 {"type": "object"}
    t = _make_tool(name="echo", params={})
    mt = adapt_tool("demo", t, StubSession(_text_result("x")))
    assert mt.parameters() == {"type": "object"}


def test_adapt_tool_read_only_hint() -> None:
    # read_only_hint=True
    t = mtypes.Tool(
        name="query",
        description="q",
        input_schema={"type": "object"},
        annotations=mtypes.ToolAnnotations(read_only_hint=True),
    )
    mt = adapt_tool("demo", t, StubSession(_text_result("x")))
    assert mt.read_only is True

    # annotations=None -> read_only False
    t2 = _make_tool(name="echo", annotations=None)
    mt2 = adapt_tool("demo", t2, StubSession(_text_result("x")))
    assert mt2.read_only is False


@pytest.mark.asyncio
async def test_execute_success_multiple_text_blocks() -> None:
    result = mtypes.CallToolResult(
        content=[
            mtypes.TextContent(type="text", text="first"),
            mtypes.TextContent(type="text", text="second"),
        ],
        is_error=False,
    )
    session = StubSession(result)
    mt = adapt_tool("demo", _make_tool(name="echo"), session)
    r = await mt.execute(json.dumps({"text": "hi"}))
    assert r.content == "first\nsecond"
    assert r.is_error is False
    # 参数被正确转发
    assert session.calls == [("echo", {"text": "hi"})]


@pytest.mark.asyncio
async def test_execute_remote_is_error() -> None:
    result = mtypes.CallToolResult(
        content=[mtypes.TextContent(type="text", text="boom")],
        is_error=True,
    )
    mt = adapt_tool("demo", _make_tool(name="echo"), StubSession(result))
    r = await mt.execute(json.dumps({"text": "x"}))
    assert r.is_error is True
    assert "boom" in r.content


@pytest.mark.asyncio
async def test_execute_call_tool_raises() -> None:
    session = StubErrorSession(RuntimeError("network down"))
    mt = adapt_tool("demo", _make_tool(name="echo"), session)
    r = await mt.execute(json.dumps({"text": "x"}))
    assert r.is_error is True
    assert "MCP 工具调用失败" in r.content
    assert "network down" in r.content


@pytest.mark.asyncio
async def test_execute_timeout(monkeypatch) -> None:
    class BlockingSession:
        async def call_tool(self, name, arguments):
            await asyncio.Event().wait()  # 永不返回

    monkeypatch.setattr("endless_code.mcp.tool._EXECUTE_TIMEOUT", 0.2)
    mt = adapt_tool("demo", _make_tool(name="echo"), BlockingSession())
    r = await mt.execute(json.dumps({"text": "x"}))
    assert r.is_error is True
    assert "超时" in r.content


@pytest.mark.asyncio
async def test_execute_invalid_json_args() -> None:
    mt = adapt_tool("demo", _make_tool(name="echo"), StubSession(_text_result("x")))
    r = await mt.execute("not json")
    assert r.is_error is True
    assert "参数 JSON 解析失败" in r.content


@pytest.mark.asyncio
async def test_execute_non_text_blocks_dropped(capsys) -> None:
    result = mtypes.CallToolResult(
        content=[
            mtypes.TextContent(type="text", text="keep"),
            mtypes.ImageContent(
                type="image",
                data="base64data",
                mimeType="image/png",
            ),
        ],
        is_error=False,
    )
    session = StubSession(result)
    mt = adapt_tool("demo", _make_tool(name="echo"), session)
    _non_text_warn_once.discard(mt.full_name)
    r = await mt.execute(json.dumps({"text": "x"}))
    assert r.content == "keep"
    assert "non-text content blocks" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_execute_non_text_warn_only_once(capsys) -> None:
    result = mtypes.CallToolResult(
        content=[
            mtypes.TextContent(type="text", text="keep"),
            mtypes.ImageContent(type="image", data="d", mimeType="image/png"),
        ],
        is_error=False,
    )
    session = StubSession(result)
    mt = adapt_tool("demo", _make_tool(name="echo"), session)
    _non_text_warn_once.discard(mt.full_name)
    await mt.execute(json.dumps({"text": "x"}))
    await mt.execute(json.dumps({"text": "y"}))
    err = capsys.readouterr().err
    assert err.count("non-text content blocks") == 1


def test_mcp_tool_matches_tool_protocol() -> None:
    """McpTool 实例应满足 endless_code 的 Tool 协议。"""
    from endless_code.tool import Tool

    t = _make_tool(name="echo")
    mt = adapt_tool("demo", t, StubSession(_text_result("x")))
    assert isinstance(mt, Tool)
