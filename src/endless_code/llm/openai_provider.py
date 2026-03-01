"""OpenAI 适配器 —— 基于 AsyncOpenAI 的流式对话。"""

from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from endless_code.config import ProviderConfig
from endless_code.llm import Message, Provider, StreamEvent
from endless_code.prompt import SYSTEM_PROMPT


class OpenAIProvider:
    """OpenAI 适配器，封装 AsyncOpenAI。

    使用标准 OpenAI chat completions 流式 API。
    thinking 配置被忽略（OpenAI 不支持通过 chat.completions 返回思考内容）。
    """

    def __init__(self, cfg: ProviderConfig) -> None:
        self._cfg = cfg
        client_kwargs: dict = {"api_key": cfg.resolve_api_key()}
        if cfg.base_url:
            client_kwargs["base_url"] = cfg.base_url
        self._client = AsyncOpenAI(**client_kwargs)

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

            response = await self._client.chat.completions.create(
                model=self._cfg.model,
                messages=messages,
                stream=True,
            )

            async for chunk in response:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        yield StreamEvent(text=delta.content)

            yield StreamEvent(done=True)

        except Exception as exc:
            yield StreamEvent(err=exc)
