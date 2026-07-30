"""LLM 层：协议无关的 Provider 接口、消息/流式事件类型、工厂函数。"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from endless_code.config import ProviderConfig

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"


@dataclass
class ToolCall:
    """协议无关地承载模型发起的一次工具调用。"""

    id: str
    name: str
    input: str


@dataclass
class ToolResult:
    """协议无关地承载一次工具执行结果。"""

    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class Usage:
    """一次模型请求的输入与输出 Token 用量。"""

    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ToolDefinition:
    """注册中心导出的协议无关工具定义。"""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class Message:
    """单条对话消息。"""

    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)


@dataclass
class StreamEvent:
    """流式事件：text 增量 / tool_calls 请求 / done 结束 / err 错误。"""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage | None = None
    done: bool = False
    err: Exception | None = None


class Provider(Protocol):
    """协议无关的 LLM Provider 接口。"""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    def stream(
        self,
        msgs: list[Message],
        tools: list[ToolDefinition],
        system_suffix: str = "",
    ) -> AsyncIterator[StreamEvent]: ...


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
