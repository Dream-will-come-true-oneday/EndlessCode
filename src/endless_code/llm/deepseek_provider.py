"""DeepSeek 适配器 —— 基于 AsyncOpenAI 的流式对话，支持工具调用。"""

from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from endless_code.config import ProviderConfig
from endless_code.llm import (
    Message,
    StreamEvent,
    ToolCall,
    ToolDefinition,
)
from endless_code.llm.openai_provider import _to_openai_messages, _to_openai_tools


class DeepSeekProvider:
    """DeepSeek 适配器，使用 OpenAI 兼容 API。"""

    def __init__(self, cfg: ProviderConfig) -> None:
        self._cfg = cfg
        self._client = AsyncOpenAI(
            api_key=cfg.resolve_api_key(),
            base_url=cfg.base_url or "https://api.deepseek.com",
        )

    @property
    def name(self) -> str:
        return self._cfg.name

    @property
    def model(self) -> str:
        return self._cfg.model

    async def stream(
        self, msgs: list[Message], tools: list[ToolDefinition]
    ) -> AsyncIterator[StreamEvent]:
        try:
            messages = _to_openai_messages(msgs)
            create_kwargs: dict = {
                "model": self._cfg.model,
                "messages": messages,
                "stream": True,
            }
            if tools:
                create_kwargs["tools"] = _to_openai_tools(tools)
            if self._cfg.thinking:
                create_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

            response = await self._client.chat.completions.create(**create_kwargs)

            tool_calls_buf: dict[int, dict[str, str]] = {}

            async for chunk in response:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                if delta and delta.content:
                    yield StreamEvent(text=delta.content)

                if delta and delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_buf:
                            tool_calls_buf[idx] = {"id": "", "name": "", "args": ""}
                        if tc.id:
                            tool_calls_buf[idx]["id"] = tc.id
                        if tc.function and tc.function.name:
                            tool_calls_buf[idx]["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            tool_calls_buf[idx]["args"] += tc.function.arguments

            if tool_calls_buf:
                calls = [
                    ToolCall(
                        id=v["id"],
                        name=v["name"],
                        input=v["args"] or "{}",
                    )
                    for _, v in sorted(tool_calls_buf.items())
                ]
                yield StreamEvent(tool_calls=calls)

            yield StreamEvent(done=True)

        except Exception as exc:
            yield StreamEvent(err=exc)
