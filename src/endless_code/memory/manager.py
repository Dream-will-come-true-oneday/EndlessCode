"""跨层记忆索引加载与异步 LLM 更新。"""

import asyncio
import copy
import inspect
import json
import logging
import math
import re
import unicodedata
from collections.abc import AsyncIterator
from dataclasses import asdict

from endless_code.llm import Message, Provider, Request, StreamEvent, System
from endless_code.memory.prompts import build_memory_prompt
from endless_code.memory.store import Store
from endless_code.memory.types import (
    MemoryOverview,
    Note,
    NoteType,
    ScopeOverview,
    UpdateAction,
)

logger = logging.getLogger(__name__)
_INDEX_LIMIT = 25_000
_WORD_RE = re.compile(r"[a-z0-9_]+|[\u3400-\u4dbf\u4e00-\u9fff]+")
_USER_TYPES = frozenset({NoteType.USER_PREFERENCE, NoteType.CORRECTION_FEEDBACK})
_PROJECT_TYPES = frozenset({NoteType.PROJECT_KNOWLEDGE, NoteType.REFERENCE_MATERIAL})
_TYPE_SCOPE = {
    **{item: "user" for item in _USER_TYPES},
    **{item: "project" for item in _PROJECT_TYPES},
}


class Manager:
    """将项目级和用户级笔记编排为可注入的长期记忆。"""

    def __init__(self, project_dir: str, user_dir: str) -> None:
        self._project = Store(project_dir, _PROJECT_TYPES)
        self._user = Store(user_dir, _USER_TYPES)
        self._provider: Provider | None = None
        self._model = ""
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()

    def load_index(self) -> str:
        user = self._safe_load_index(self._user).strip()
        project = self._safe_load_index(self._project).strip()
        parts = [
            f"### User memory\n{user}" if user else "",
            f"### Project memory\n{project}" if project else "",
        ]
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

    def recall(self, query: str, limit: int = 6, max_bytes: int = 8192) -> str:
        query_tokens = _tokenize(query)
        if not query_tokens or limit <= 0 or max_bytes <= 0:
            return ""
        records = [
            *(("user", note) for note in self._safe_list_notes(self._user)),
            *(("project", note) for note in self._safe_list_notes(self._project)),
        ]
        if not records:
            return ""
        token_sets = [_tokenize(f"{note.title}\n{note.content}") for _, note in records]
        scored: list[tuple[float, str, Note]] = []
        for (scope, note), document_tokens in zip(records, token_sets, strict=True):
            title_tokens = _tokenize(note.title)
            content_tokens = _tokenize(note.content)
            score = 0.0
            for token in query_tokens:
                if token not in document_tokens:
                    continue
                frequency = sum(token in tokens for tokens in token_sets)
                inverse = math.log((len(records) + 1) / (frequency + 1)) + 1.0
                score += inverse * (
                    (3.0 if token in title_tokens else 0.0)
                    + (1.0 if token in content_tokens else 0.0)
                )
            if score > 0:
                scored.append((score, scope, note))
        scored.sort(
            key=lambda item: (
                -item[0],
                -_safe_timestamp(item[2]),
                item[2].filename,
            )
        )
        return _render_recall(scored[:limit], max_bytes)

    def overview(self) -> MemoryOverview:
        return MemoryOverview(
            user=self._safe_overview(self._user, "user"),
            project=self._safe_overview(self._project, "project"),
        )

    def clear(self, scope: str) -> int:
        stores = {
            "user": (self._user,),
            "project": (self._project,),
            "all": (self._user, self._project),
        }.get(scope)
        if stores is None:
            raise ValueError(f"未知记忆范围: {scope}")
        for task in tuple(self._tasks):
            task.cancel()
        removed = 0
        for store in stores:
            try:
                removed += store.clear()
            except OSError:
                logger.info("清空长期记忆失败: %s", store.directory, exc_info=True)
        return removed

    def schedule_update(self, recent_messages: list[Message]) -> None:
        provider = self._provider
        if provider is None or not recent_messages:
            return
        snapshot = copy.deepcopy(recent_messages)
        task = asyncio.create_task(self._update_with_provider(provider, snapshot))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def update_async(self, recent_messages: list[Message]) -> None:
        provider = self._provider
        if provider is None or not recent_messages:
            return
        await self._update_with_provider(provider, copy.deepcopy(recent_messages))

    async def drain(self, timeout: float = 2.0) -> None:
        tasks = set(self._tasks)
        if not tasks:
            return
        _done, pending = await asyncio.wait(tasks, timeout=max(0.0, timeout))
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _update_with_provider(
        self, provider: Provider, recent_messages: list[Message]
    ) -> None:
        async with self._lock:
            try:
                prompt = build_memory_prompt(
                    _render_messages(recent_messages), self._catalog()
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
                    [
                        item
                        for item in actions
                        if _TYPE_SCOPE.get(NoteType(item.type)) == "project"
                    ]
                )
                self._user.apply(
                    [
                        item
                        for item in actions
                        if _TYPE_SCOPE.get(NoteType(item.type)) == "user"
                    ]
                )
            except Exception:
                logger.info("长期记忆更新失败", exc_info=True)

    def _catalog(self) -> str:
        rows: list[str] = []
        for store in (self._user, self._project):
            for note in self._safe_list_notes(store):
                summary = " ".join(note.content.split())[:160]
                rows.append(
                    f"- [{note.type.value}] {note.filename} | {note.title} | {summary}"
                )
        return "\n".join(rows)

    @staticmethod
    def _safe_load_index(store: Store) -> str:
        try:
            return store.load_index()
        except OSError:
            logger.info("加载长期记忆索引失败: %s", store.directory, exc_info=True)
            return ""

    @staticmethod
    def _safe_list_notes(store: Store) -> list[Note]:
        try:
            return store.list_notes()
        except OSError:
            logger.info("读取长期记忆失败: %s", store.directory, exc_info=True)
            return []

    @staticmethod
    def _safe_overview(store: Store, scope: str) -> ScopeOverview:
        try:
            return store.overview(scope)
        except OSError:
            logger.info("读取长期记忆概览失败: %s", store.directory, exc_info=True)
            return ScopeOverview(scope, store.directory, 0, {}, ())


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
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return []
    raw = json.loads(text[start : end + 1])
    if not isinstance(raw, dict):
        return []
    actions: list[UpdateAction] = []
    for note_type in NoteType:
        items = raw.get(note_type.value, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            action = str(item.get("action", ""))
            if action not in {"create", "update", "delete"}:
                continue
            actions.append(
                UpdateAction(
                    action=action,
                    type=note_type.value,
                    title=str(item.get("title", "")),
                    slug=str(item.get("slug", "")),
                    content=str(item.get("content", "")),
                    filename=str(item.get("filename", "")),
                )
            )
    return actions


def _render_messages(messages: list[Message]) -> str:
    return "\n".join(
        json.dumps(asdict(message), ensure_ascii=False) for message in messages
    )


def _tokenize(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text).lower()
    tokens: set[str] = set()
    for match in _WORD_RE.finditer(normalized):
        value = match.group(0)
        if value[0].isascii() or len(value) == 1:
            tokens.add(value)
        else:
            tokens.update(value[index : index + 2] for index in range(len(value) - 1))
    return tokens


def _render_recall(scored: list[tuple[float, str, Note]], max_bytes: int) -> str:
    if not scored:
        return ""
    opening = (
        "<system-reminder>Relevant durable memories for the current request "
        "(user-maintained context):\n"
    )
    closing = "\n</system-reminder>"
    reserved = len((opening + closing).encode("utf-8"))
    if max_bytes <= reserved:
        return ""
    sections: list[str] = []
    used = reserved
    for _score, scope, note in scored:
        section = (
            f"### [{scope}/{note.type.value}] {note.title} ({note.filename})\n"
            f"{note.content}"
        )
        separator = "\n\n" if sections else ""
        available = max_bytes - used - len(separator.encode("utf-8"))
        if available <= 0:
            break
        encoded = section.encode("utf-8")
        if len(encoded) > available:
            section = encoded[:available].decode("utf-8", errors="ignore").rstrip()
        if not section:
            break
        sections.append(section)
        used += len((separator + section).encode("utf-8"))
        if len(encoded) > available:
            break
    if not sections:
        return ""
    return opening + "\n\n".join(sections) + closing


def _safe_timestamp(note: Note) -> float:
    try:
        return note.updated.timestamp()
    except (OSError, OverflowError, ValueError):
        return 0.0
