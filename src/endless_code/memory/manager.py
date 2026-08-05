"""跨层记忆索引加载与异步 LLM 更新。"""

import asyncio
import inspect
import json
import logging
from collections.abc import AsyncIterator

from endless_code.llm import Message, Provider, Request, StreamEvent, System
from endless_code.memory.prompts import build_memory_prompt
from endless_code.memory.store import Store
from endless_code.memory.types import UpdateAction

logger = logging.getLogger(__name__)
_INDEX_LIMIT = 25_000
_SIGNALS = ("记住", "记忆", "别忘", "remember", "memo")


def has_memory_signal(messages: list[Message]) -> bool:
    return any(
        message.role == "user"
        and any(signal in message.content.lower() for signal in _SIGNALS)
        for message in messages
    )


class Manager:
    """将项目级和用户级笔记编排为可注入的长期记忆。"""

    def __init__(self, project_dir: str, user_dir: str) -> None:
        self._project = Store(project_dir)
        self._user = Store(user_dir)
        self._provider: Provider | None = None
        self._model = ""
        self._lock = asyncio.Lock()

    def load_index(self) -> str:
        parts = [self._project.load_index().strip(), self._user.load_index().strip()]
        merged = "\n\n".join(part for part in parts if part)
        encoded = merged.encode("utf-8")
        if len(encoded) <= _INDEX_LIMIT:
            return merged
        suffix = b"\n(index truncated)"
        return (encoded[: _INDEX_LIMIT - len(suffix)] + suffix).decode(
            "utf-8", errors="ignore"
        )

    def set_provider(self, provider: Provider, model: str) -> None:
        self._provider = provider
        self._model = model

    async def update_async(self, recent_messages: list[Message]) -> None:
        provider = self._provider
        if provider is None or not recent_messages:
            return
        async with self._lock:
            try:
                prompt = build_memory_prompt(
                    _render_messages(recent_messages), self.load_index()
                )
                response = await _collect(
                    provider,
                    Request(
                        messages=[Message(role="user", content=prompt)],
                        tools=[],
                        system=System(
                            stable="You update durable user and project memory."
                        ),
                    ),
                )
                actions = _parse_actions(response)
                self._project.apply(
                    [item for item in actions if item.level == "project"]
                )
                self._user.apply([item for item in actions if item.level == "user"])
            except Exception:
                logger.info("长期记忆更新失败", exc_info=True)


async def _collect(provider: Provider, request: Request) -> str:
    stream_fn = provider.stream
    try:
        legacy = len(inspect.signature(stream_fn).parameters) >= 2
    except (TypeError, ValueError):
        legacy = False
    stream: AsyncIterator[StreamEvent] = (
        stream_fn(request.messages, [], "") if legacy else stream_fn(request)
    )
    pieces: list[str] = []
    async for event in stream:
        if event.err is not None:
            raise event.err
        pieces.append(event.text)
    return "".join(pieces)


def _parse_actions(text: str) -> list[UpdateAction]:
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        return []
    raw = json.loads(text[start : end + 1])
    if not isinstance(raw, list):
        return []
    actions: list[UpdateAction] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", ""))
        level = str(item.get("level", ""))
        if action not in {"create", "update", "delete"} or level not in {
            "project",
            "user",
        }:
            continue
        actions.append(
            UpdateAction(
                action=action,
                level=level,
                type=str(item.get("type", "")),
                title=str(item.get("title", "")),
                slug=str(item.get("slug", "")),
                content=str(item.get("content", "")),
                filename=str(item.get("filename", "")),
            )
        )
    return actions


def _render_messages(messages: list[Message]) -> str:
    return "\n\n".join(f"{message.role}: {message.content}" for message in messages)
