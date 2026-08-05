"""Anthropic Messages API 适配器。"""

from collections.abc import AsyncIterator
from typing import Any

from anthropic import AsyncAnthropic

from endless_code.config import ProviderConfig
from endless_code.llm import (
    ROLE_ASSISTANT,
    ROLE_TOOL,
    Message,
    PromptTooLongError,
    Request,
    StreamEvent,
    ToolCall,
    ToolDefinition,
    Usage,
)


def _wrap_prompt_too_long(exc: Exception) -> Exception:
    """将 Anthropic 上下文超限错误归一化。"""
    text = f"{exc} {getattr(exc, 'body', '')}".lower()
    markers = ("prompt is too long", "context_length", "context window")
    if any(marker in text for marker in markers):
        wrapped = PromptTooLongError("anthropic prompt too long")
        wrapped.__cause__ = exc
        return wrapped
    return exc


def _to_anthropic_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
        }
        for tool in tools
    ]


def _content_blocks(message: Message) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if message.content:
        blocks.append({"type": "text", "text": message.content})
    if message.tool_calls:
        blocks.extend(
            {
                "type": "tool_use",
                "id": call.id,
                "name": call.name,
                "input": _parse_json(call.input),
            }
            for call in message.tool_calls
        )
    return blocks or [{"type": "text", "text": ""}]


def _parse_json(value: str) -> dict[str, Any]:
    import json

    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _to_anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for message in messages:
        if message.role == ROLE_ASSISTANT:
            output.append({"role": "assistant", "content": _content_blocks(message)})
        elif message.role == ROLE_TOOL:
            output.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": result.tool_call_id,
                            "content": result.content,
                            "is_error": result.is_error,
                        }
                        for result in message.tool_results
                    ],
                }
            )
        else:
            output.append({"role": "user", "content": message.content})
    return output


def _with_reminder(
    messages: list[dict[str, Any]], reminder: str
) -> list[dict[str, Any]]:
    if not reminder:
        return messages
    output = [dict(message) for message in messages]
    if output and output[-1]["role"] == "user":
        content = output[-1]["content"]
        blocks = (
            list(content)
            if isinstance(content, list)
            else [{"type": "text", "text": content}]
        )
        blocks.append({"type": "text", "text": reminder})
        output[-1]["content"] = blocks
    else:
        output.append({"role": "user", "content": [{"type": "text", "text": reminder}]})
    return output


def _to_system(system) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if system.stable:
        blocks.append(
            {
                "type": "text",
                "text": system.stable,
                "cache_control": {"type": "ephemeral"},
            }
        )
    if system.environment:
        blocks.append({"type": "text", "text": system.environment})
    return blocks


class AnthropicProvider:
    """Anthropic Messages streaming provider。"""

    def __init__(self, cfg: ProviderConfig) -> None:
        self._cfg = cfg
        kwargs: dict[str, Any] = {"api_key": cfg.resolve_api_key()}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        self._client = AsyncAnthropic(**kwargs)

    @property
    def name(self) -> str:
        return self._cfg.name

    @property
    def model(self) -> str:
        return self._cfg.model

    async def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        params: dict[str, Any] = {
            "model": self._cfg.model,
            "max_tokens": 4096,
            "system": _to_system(req.system),
            "messages": _with_reminder(
                _to_anthropic_messages(req.messages), req.reminder
            ),
            "stream": True,
        }
        if req.tools:
            params["tools"] = _to_anthropic_tools(req.tools)

        tool_buffers: dict[int, dict[str, str]] = {}
        usage = Usage()
        usage_seen = False
        try:
            async with self._client.messages.stream(**params) as stream:
                async for event in stream:
                    event_type = getattr(event, "type", "")
                    if event_type == "message_start":
                        message_usage = getattr(
                            getattr(event, "message", None), "usage", None
                        )
                        if message_usage is not None:
                            usage.input_tokens = (
                                getattr(message_usage, "input_tokens", 0) or 0
                            )
                            usage.cache_write = (
                                getattr(
                                    message_usage,
                                    "cache_creation_input_tokens",
                                    0,
                                )
                                or 0
                            )
                            usage.cache_read = (
                                getattr(
                                    message_usage,
                                    "cache_read_input_tokens",
                                    0,
                                )
                                or 0
                            )
                            usage_seen = True
                    elif event_type == "message_delta":
                        delta_usage = getattr(event, "usage", None)
                        if delta_usage is not None:
                            usage.output_tokens = (
                                getattr(delta_usage, "output_tokens", 0) or 0
                            )
                            usage_seen = True
                    elif event_type == "content_block_start":
                        block = getattr(event, "content_block", None)
                        if getattr(block, "type", "") == "tool_use":
                            index = getattr(event, "index", 0)
                            tool_buffers[index] = {
                                "id": getattr(block, "id", "") or "",
                                "name": getattr(block, "name", "") or "",
                                "args": "",
                            }
                    elif event_type == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        delta_type = getattr(delta, "type", "")
                        if delta_type == "text_delta":
                            text = getattr(delta, "text", "") or ""
                            if text:
                                yield StreamEvent(text=text)
                        elif delta_type == "input_json_delta":
                            index = getattr(event, "index", 0)
                            if index in tool_buffers:
                                tool_buffers[index]["args"] += (
                                    getattr(delta, "partial_json", "") or ""
                                )
                    elif event_type == "message_stop":
                        break

            if usage_seen:
                yield StreamEvent(usage=usage)
            if tool_buffers:
                yield StreamEvent(
                    tool_calls=[
                        ToolCall(
                            id=buffer["id"],
                            name=buffer["name"],
                            input=buffer["args"] or "{}",
                        )
                        for _, buffer in sorted(tool_buffers.items())
                    ]
                )
            yield StreamEvent(done=True)
        except Exception as exc:  # noqa: BLE001
            yield StreamEvent(err=_wrap_prompt_too_long(exc))
