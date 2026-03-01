"""DeepSeek 适配器 —— 基于 AsyncOpenAI 的流式对话。"""

from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from endless_code.config import ProviderConfig
from endless_code.llm import Message, Provider, StreamEvent
from endless_code.prompt import SYSTEM_PROMPT


class DeepSeekProvider:
    """DeepSeek 适配器，封装 AsyncOpenAI。

    DeepSeek 使用 OpenAI 兼容 API。thinking 模式通过
    ``extra_body={"thinking": {"type": "enabled"}}`` 启用，
    reasoning_content（思考增量）接收后即丢弃。
    """

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

    async def stream(self, msgs: list[Message]) -> AsyncIterator[StreamEvent]:
        try:
            messages: list[dict] = [
                {"role": "system", "content": SYSTEM_PROMPT}
            ]
            for m in msgs:
                messages.append({"role": m.role, "content": m.content})

            create_kwargs: dict = {
                "model": self._cfg.model,
                "messages": messages,
                "stream": True,
            }

            if self._cfg.thinking:
                create_kwargs["extra_body"] = {
                    "thinking": {"type": "enabled"}
                }

            response = await self._client.chat.completions.create(
                **create_kwargs
            )
            async for chunk in response:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        yield StreamEvent(text=delta.content)
                    # reasoning_content is discarded per spec

            yield StreamEvent(done=True)

        except Exception as exc:
            yield StreamEvent(err=exc)
