"""OpenAI 兼容适配器：OpenAI 官方、DeepSeek 和兼容 base_url 共用。"""

from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from endless_code.config import ProviderConfig
from endless_code.llm import (
    ROLE_ASSISTANT,
    ROLE_TOOL,
    Message,
    Request,
    StreamEvent,
    System,
    ToolCall,
    ToolDefinition,
    Usage,
)
from endless_code.prompt import SYSTEM_PROMPT


def _to_openai_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }
        for tool in tools
    ]


def _to_openai_messages(
    request_or_messages: Request | list[Message],
    tools: list[ToolDefinition] | None = None,
    system_suffix: str = "",
) -> list[dict[str, Any]]:
    if isinstance(request_or_messages, Request):
        request = request_or_messages
        stable = request.system.stable
        environment = request.system.environment
        reminder = request.reminder
        messages = request.messages
    else:
        request = Request(
            messages=request_or_messages,
            tools=tools or [],
            system=System(
                stable=SYSTEM_PROMPT,
                environment=system_suffix,
            ),
        )
        stable = request.system.stable
        environment = request.system.environment
        reminder = ""
        messages = request.messages

    system_content = stable
    if environment:
        system_content = (
            f"{system_content}\n\n{environment}" if system_content else environment
        )
    output: list[dict[str, Any]] = []
    if system_content:
        output.append({"role": "system", "content": system_content})

    for message in messages:
        if message.role == ROLE_ASSISTANT:
            item: dict[str, Any] = {
                "role": "assistant",
                "content": message.content or None,
            }
            if message.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": call.input or "{}",
                        },
                    }
                    for call in message.tool_calls
                ]
            output.append(item)
        elif message.role == ROLE_TOOL:
            output.extend(
                {
                    "role": "tool",
                    "tool_call_id": result.tool_call_id,
                    "content": result.content,
                }
                for result in message.tool_results
            )
        else:
            output.append({"role": message.role, "content": message.content})

    if reminder:
        output.append({"role": "user", "content": reminder})
    return output


def _coerce_request(
    request_or_messages: Request | list[Message],
    tools: list[ToolDefinition] | None,
    system_suffix: str,
) -> Request:
    if isinstance(request_or_messages, Request):
        return request_or_messages
    return Request(
        messages=request_or_messages,
        tools=tools or [],
        system=System(stable=SYSTEM_PROMPT, environment=system_suffix),
    )


class OpenAIProvider:
    """OpenAI 兼容适配器，支持工具调用全流程。"""

    def __init__(self, cfg: ProviderConfig) -> None:
        self._cfg = cfg
        client_kwargs: dict[str, Any] = {"api_key": cfg.resolve_api_key()}
        if cfg.base_url:
            client_kwargs["base_url"] = cfg.base_url
        self._client = AsyncOpenAI(**client_kwargs)

    @property
    def name(self) -> str:
        return self._cfg.name

    @property
    def model(self) -> str:
        return self._cfg.model

    async def stream(
        self,
        request_or_messages: Request | list[Message],
        tools: list[ToolDefinition] | None = None,
        system_suffix: str = "",
    ) -> AsyncIterator[StreamEvent]:
        request = _coerce_request(request_or_messages, tools, system_suffix)
        try:
            messages = _to_openai_messages(request)
            params: dict[str, Any] = {
                "model": self._cfg.model,
                "messages": messages,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if request.tools:
                params["tools"] = _to_openai_tools(request.tools)

            response = await self._client.chat.completions.create(**params)
            tool_calls_buf: dict[int, dict[str, str]] = {}
            usage_seen = False

            async with response:
                async for chunk in response:
                    chunk_usage = getattr(chunk, "usage", None)
                    if chunk_usage is not None and not usage_seen:
                        usage_seen = True
                        details = getattr(chunk_usage, "prompt_tokens_details", None)
                        yield StreamEvent(
                            usage=Usage(
                                input_tokens=getattr(chunk_usage, "prompt_tokens", 0)
                                or 0,
                                output_tokens=getattr(
                                    chunk_usage, "completion_tokens", 0
                                )
                                or 0,
                                cache_read=getattr(details, "cached_tokens", 0) or 0,
                            )
                        )

                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        yield StreamEvent(text=delta.content)
                    if delta and delta.tool_calls:
                        for tool_call in delta.tool_calls:
                            index = tool_call.index
                            if index not in tool_calls_buf:
                                tool_calls_buf[index] = {
                                    "id": "",
                                    "name": "",
                                    "args": "",
                                }
                            if tool_call.id:
                                tool_calls_buf[index]["id"] = tool_call.id
                            if tool_call.function and tool_call.function.name:
                                tool_calls_buf[index]["name"] = tool_call.function.name
                            if tool_call.function and tool_call.function.arguments:
                                tool_calls_buf[index]["args"] += (
                                    tool_call.function.arguments
                                )

            if tool_calls_buf:
                yield StreamEvent(
                    tool_calls=[
                        ToolCall(
                            id=value["id"],
                            name=value["name"],
                            input=value["args"] or "{}",
                        )
                        for _, value in sorted(tool_calls_buf.items())
                    ]
                )
            yield StreamEvent(done=True)
        except Exception as exc:  # noqa: BLE001
            yield StreamEvent(err=exc)
