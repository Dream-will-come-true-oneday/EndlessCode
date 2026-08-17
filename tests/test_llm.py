"""DeepSeek/OpenAI 兼容流适配器测试。"""

from types import SimpleNamespace

import pytest

from endless_code.config import ProviderConfig
from endless_code.llm import Message, ToolDefinition
from endless_code.llm.deepseek_provider import DeepSeekProvider
from endless_code.llm.openai_provider import OpenAIProvider


def _chunk(*, content=None, tool_calls=None, usage=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choices = [SimpleNamespace(delta=delta)] if content or tool_calls else []
    return SimpleNamespace(choices=choices, usage=usage)


class FakeResponse:
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for chunk in self.chunks:
            yield chunk


class FakeCompletions:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


class FakeClient:
    def __init__(self, response):
        self.chat = SimpleNamespace(completions=FakeCompletions(response))


def _config(protocol: str, *, thinking: bool = False) -> ProviderConfig:
    return ProviderConfig(
        name=protocol,
        protocol=protocol,
        api_key="test-key",
        model="test-model",
        thinking=thinking,
    )


@pytest.mark.asyncio
async def test_openai_stream_collects_text_tools_usage_and_suffix() -> None:
    usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7)
    tool_start = SimpleNamespace(
        index=0,
        id="call-1",
        function=SimpleNamespace(name="read_file", arguments='{"pa'),
    )
    tool_tail = SimpleNamespace(
        index=0,
        id=None,
        function=SimpleNamespace(name=None, arguments='th":"a"}'),
    )
    response = FakeResponse(
        [
            _chunk(content="hello"),
            _chunk(tool_calls=[tool_start]),
            _chunk(tool_calls=[tool_tail]),
            _chunk(usage=usage),
        ]
    )
    provider = OpenAIProvider(_config("openai"))
    client = FakeClient(response)
    provider._client = client

    tools = [ToolDefinition(name="read_file", description="read", input_schema={})]
    events = [
        event
        async for event in provider.stream(
            [Message(role="user", content="hi")], tools, "plan suffix"
        )
    ]

    kwargs = client.chat.completions.kwargs
    assert "plan suffix" in kwargs["messages"][0]["content"]
    assert kwargs["stream_options"] == {"include_usage": True}
    assert [event.text for event in events if event.text] == ["hello"]
    calls = [call for event in events for call in event.tool_calls]
    assert calls[0].id == "call-1"
    assert calls[0].input == '{"path":"a"}'
    usages = [event.usage for event in events if event.usage]
    assert [(item.input_tokens, item.output_tokens) for item in usages] == [(11, 7)]
    assert events[-1].done
    assert response.closed


@pytest.mark.asyncio
async def test_deepseek_stream_preserves_thinking_and_suffix() -> None:
    usage = SimpleNamespace(prompt_tokens=3, completion_tokens=2)
    response = FakeResponse([_chunk(content="ok"), _chunk(usage=usage)])
    provider = DeepSeekProvider(_config("deepseek", thinking=True))
    client = FakeClient(response)
    provider._client = client

    visible_tools = [
        ToolDefinition("ToolSearch", "load deferred tools", {"type": "object"})
    ]
    events = [
        event async for event in provider.stream([], visible_tools, "read only plan")
    ]
    kwargs = client.chat.completions.kwargs
    assert kwargs["stream_options"] == {"include_usage": True}
    assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "read only plan" in kwargs["messages"][0]["content"]
    assert [tool["function"]["name"] for tool in kwargs["tools"]] == ["ToolSearch"]
    assert events[0].text == "ok"
    assert events[1].usage.input_tokens == 3
    assert events[-1].done
    assert response.closed


@pytest.mark.asyncio
async def test_openai_request_injects_reminder_and_cache_usage() -> None:
    cached_details = SimpleNamespace(cached_tokens=6)
    usage = SimpleNamespace(
        prompt_tokens=10,
        completion_tokens=4,
        prompt_tokens_details=cached_details,
    )
    response = FakeResponse([_chunk(usage=usage)])
    provider = OpenAIProvider(_config("openai"))
    client = FakeClient(response)
    provider._client = client
    from endless_code.llm import Request, System

    request = Request(
        messages=[Message(role="user", content="hello")],
        tools=[ToolDefinition("ToolSearch", "load deferred tools", {"type": "object"})],
        system=System(stable="stable", environment="environment"),
        reminder="<system-reminder>notice</system-reminder>",
    )
    events = [event async for event in provider.stream(request)]
    assert (
        client.chat.completions.kwargs["messages"][0]["content"]
        == "stable\n\nenvironment"
    )
    assert client.chat.completions.kwargs["messages"][-1] == {
        "role": "user",
        "content": request.reminder,
    }
    assert [
        tool["function"]["name"] for tool in client.chat.completions.kwargs["tools"]
    ] == ["ToolSearch"]
    assert request.messages == [Message(role="user", content="hello")]
    event_usage = next(event.usage for event in events if event.usage)
    assert event_usage.cache_read == 6


@pytest.mark.asyncio
async def test_deepseek_cache_field_defaults_and_is_supported() -> None:
    usage = SimpleNamespace(
        prompt_tokens=10,
        completion_tokens=4,
        prompt_cache_hit_tokens=8,
    )
    response = FakeResponse([_chunk(usage=usage)])
    provider = DeepSeekProvider(_config("deepseek"))
    provider._client = FakeClient(response)
    from endless_code.llm import Request

    events = [event async for event in provider.stream(Request())]
    event_usage = next(event.usage for event in events if event.usage)
    assert event_usage.cache_read == 8
