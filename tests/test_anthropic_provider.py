from types import SimpleNamespace

import pytest

from endless_code.config import ProviderConfig
from endless_code.llm import Message, Request, System, ToolDefinition
from endless_code.llm.anthropic_provider import AnthropicProvider


class FakeStream:
    def __init__(self, events):
        self.events = events
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for event in self.events:
            yield event


class FakeMessages:
    def __init__(self, stream):
        self.response = stream
        self.kwargs = None

    def stream(self, **kwargs):
        self.kwargs = kwargs
        return self.response


@pytest.mark.asyncio
async def test_cache_control_reminder_usage_and_tool_stream() -> None:
    events = [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(
                usage=SimpleNamespace(
                    input_tokens=11,
                    cache_creation_input_tokens=7,
                    cache_read_input_tokens=5,
                )
            ),
        ),
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(
                type="tool_use", id="call-1", name="read_file"
            ),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="input_json_delta", partial_json='{"path":"a"}'),
        ),
        SimpleNamespace(
            type="message_delta",
            usage=SimpleNamespace(output_tokens=3),
        ),
        SimpleNamespace(type="message_stop"),
    ]
    stream = FakeStream(events)
    provider = AnthropicProvider(
        ProviderConfig("anthropic", "anthropic", "test-key", "claude-test")
    )
    messages = FakeMessages(stream)
    provider._client = SimpleNamespace(messages=messages)
    request = Request(
        messages=[Message(role="user", content="hello")],
        tools=[ToolDefinition("read_file", "read", {"type": "object"})],
        system=System(stable="stable", environment="environment"),
        reminder="<system-reminder>plan</system-reminder>",
    )

    result = [event async for event in provider.stream(request)]

    assert messages.kwargs["system"] == [
        {"type": "text", "text": "stable", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "environment"},
    ]
    assert messages.kwargs["messages"][-1]["content"][-1]["text"] == request.reminder
    usage = next(event.usage for event in result if event.usage)
    assert (
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_write,
        usage.cache_read,
    ) == (11, 3, 7, 5)
    call = next(call for event in result for call in event.tool_calls)
    assert (call.id, call.name, call.input) == ("call-1", "read_file", '{"path":"a"}')
    assert result[-1].done
    assert stream.closed
