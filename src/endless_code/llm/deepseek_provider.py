"""DeepSeek 适配器：基于 OpenAI 兼容 API 的 Provider。"""

from collections.abc import AsyncIterator
from typing import Any

from endless_code.config import ProviderConfig
from endless_code.llm import Message, Request, StreamEvent, ToolDefinition, Usage
from endless_code.llm.openai_provider import OpenAIProvider, _coerce_request


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek Provider，保留 thinking 与默认 base URL。"""

    def __init__(self, cfg: ProviderConfig) -> None:
        super().__init__(cfg)
        self._client = self._build_client(cfg)

    @staticmethod
    def _build_client(cfg: ProviderConfig):
        from openai import AsyncOpenAI

        return AsyncOpenAI(
            api_key=cfg.resolve_api_key(),
            base_url=cfg.base_url or "https://api.deepseek.com",
        )

    async def stream(
        self,
        request_or_messages: Request | list[Message],
        tools: list[ToolDefinition] | None = None,
        system_suffix: str = "",
    ) -> AsyncIterator[StreamEvent]:
        request = _coerce_request(request_or_messages, tools, system_suffix)
        try:
            params: dict[str, Any] = {
                "model": self._cfg.model,
                "messages": self._messages_for_request(request),
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if request.tools:
                from endless_code.llm.openai_provider import _to_openai_tools

                params["tools"] = _to_openai_tools(request.tools)
            if self._cfg.thinking:
                params["extra_body"] = {"thinking": {"type": "enabled"}}

            response = await self._client.chat.completions.create(**params)
            tool_calls_buf: dict[int, dict[str, str]] = {}
            usage_seen = False
            async with response:
                async for chunk in response:
                    chunk_usage = getattr(chunk, "usage", None)
                    if chunk_usage is not None and not usage_seen:
                        usage_seen = True
                        details = getattr(chunk_usage, "prompt_tokens_details", None)
                        cache_read = getattr(details, "cached_tokens", 0) or 0
                        cache_read = (
                            getattr(chunk_usage, "prompt_cache_hit_tokens", cache_read)
                            or cache_read
                        )
                        yield StreamEvent(
                            usage=Usage(
                                input_tokens=getattr(chunk_usage, "prompt_tokens", 0)
                                or 0,
                                output_tokens=getattr(
                                    chunk_usage, "completion_tokens", 0
                                )
                                or 0,
                                cache_read=cache_read,
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
                from endless_code.llm import ToolCall

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
            from endless_code.llm.openai_provider import _wrap_prompt_too_long

            yield StreamEvent(err=_wrap_prompt_too_long(exc))

    @staticmethod
    def _messages_for_request(request: Request) -> list[dict[str, Any]]:
        from endless_code.llm.openai_provider import _to_openai_messages

        return _to_openai_messages(request)
