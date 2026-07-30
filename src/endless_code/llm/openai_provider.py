"""OpenAI 适配器 —— 基于 AsyncOpenAI 的流式对话，支持工具调用。"""

from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from endless_code.config import ProviderConfig
from endless_code.llm import (
    ROLE_ASSISTANT,
    ROLE_TOOL,
    Message,
    StreamEvent,
    ToolCall,
    ToolDefinition,
    Usage,
)
from endless_code.prompt import SYSTEM_PROMPT


def _to_openai_tools(tools: list[ToolDefinition]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]


def _effective_system(system_suffix: str = "") -> str:
    if not system_suffix:
        return SYSTEM_PROMPT
    return f"{SYSTEM_PROMPT}\n\n{system_suffix}"


def _to_openai_messages(msgs: list[Message], system_suffix: str = "") -> list[dict]:
    out: list[dict] = [{"role": "system", "content": _effective_system(system_suffix)}]
    for m in msgs:
        if m.role == ROLE_ASSISTANT:
            msg: dict = {"role": "assistant", "content": m.content or None}
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": c.input or "{}"},
                    }
                    for c in m.tool_calls
                ]
            out.append(msg)
        elif m.role == ROLE_TOOL:
            for r in m.tool_results:
                out.append(
                    {"role": "tool", "tool_call_id": r.tool_call_id, "content": r.content}
                )
        else:
            out.append({"role": m.role, "content": m.content})
    return out


class OpenAIProvider:
    """OpenAI 适配器，支持工具调用全流程。"""

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

    async def stream(
        self,
        msgs: list[Message],
        tools: list[ToolDefinition],
        system_suffix: str = "",
    ) -> AsyncIterator[StreamEvent]:
        try:
            messages = _to_openai_messages(msgs, system_suffix)
            params: dict = {
                "model": self._cfg.model,
                "messages": messages,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if tools:
                params["tools"] = _to_openai_tools(tools)

            response = await self._client.chat.completions.create(**params)

            tool_calls_buf: dict[int, dict[str, str]] = {}

            usage_seen = False
            async with response:
                async for chunk in response:
                    chunk_usage = getattr(chunk, "usage", None)
                    if chunk_usage is not None and not usage_seen:
                        usage_seen = True
                        yield StreamEvent(
                            usage=Usage(
                                input_tokens=getattr(chunk_usage, "prompt_tokens", 0) or 0,
                                output_tokens=getattr(chunk_usage, "completion_tokens", 0) or 0,
                            )
                        )

                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta

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
