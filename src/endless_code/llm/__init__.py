"""LLM 层：协议无关的 Provider 接口、消息/流式事件类型、工厂函数。"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from endless_code.config import ProviderConfig  # noqa: F401


@dataclass
class Message:
    """单条对话消息。"""

    role: Literal["user", "assistant"]
    content: str


@dataclass
class StreamEvent:
    """流式事件。text / done / err 互斥语义——调用方按字段判断。"""

    text: str = ""
    done: bool = False
    err: Exception | None = None


class Provider(Protocol):
    """协议无关的 LLM Provider 接口。

    所有适配器实现此 Protocol，上层（TUI）只依赖此接口。
    """

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    def stream(self, msgs: list[Message]) -> AsyncIterator[StreamEvent]:
        """发起一轮流式对话，返回 async generator 逐个产出 StreamEvent。

        适配器内部负责：
        - 注入内置 system prompt
        - 根据配置启用/禁用扩展思考
        - 思考增量内部丢弃，只产出文本增量和结束/错误事件
        """
        ...


def new_provider(cfg: "ProviderConfig") -> Provider:
    """根据配置构造对应的 Provider 适配器实例。"""
    if cfg.protocol == "deepseek":
        from endless_code.llm.deepseek_provider import DeepSeekProvider

        return DeepSeekProvider(cfg)
    elif cfg.protocol == "openai":
        from endless_code.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(cfg)
    else:
        raise ValueError(f"不支持的 protocol：{cfg.protocol}")
