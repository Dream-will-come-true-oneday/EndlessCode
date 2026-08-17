"""第二层：摘要、近期原文和摘要请求 PTL 重试。"""

import inspect
import math
from collections.abc import AsyncIterator

from endless_code.compact.const import (
    PTL_DROP_PERCENTAGE,
    PTL_RETRY_LIMIT,
    RECENT_KEEP_MESSAGES,
)
from endless_code.compact.limits import (
    BASE_CONTEXT_WINDOW,
    build_context_limits,
)
from endless_code.compact.recovery import build_recovery_attachment
from endless_code.compact.summary_prompt import build_summary_prompt, extract_summary
from endless_code.compact.token import estimate_tokens
from endless_code.llm import Message, PromptTooLongError, Request, StreamEvent


def _provider_stream(provider, request: Request) -> AsyncIterator[StreamEvent]:
    stream_fn = provider.stream
    try:
        legacy = len(inspect.signature(stream_fn).parameters) >= 2
    except (TypeError, ValueError):
        legacy = False
    if legacy:
        return stream_fn(request.messages, request.tools, request.reminder)
    return stream_fn(request)


def pick_recent_tail(
    messages: list[Message], recent_keep_tokens: int | None = None
) -> list[Message]:
    """从尾部保留足够的新近原文，且不拆开工具调用配对。"""
    if not messages:
        return []
    if recent_keep_tokens is None:
        recent_keep_tokens = build_context_limits(
            BASE_CONTEXT_WINDOW
        ).recent_keep_tokens
    total = 0
    start = len(messages)
    for count, index in enumerate(range(len(messages) - 1, -1, -1), start=1):
        total += estimate_tokens(0, [messages[index]], 0)
        start = index
        if total >= recent_keep_tokens and count >= RECENT_KEEP_MESSAGES:
            break
    while start > 0 and messages[start].role == "tool":
        start -= 1
    return messages[start:]


def group_by_user_turn(messages: list[Message]) -> list[list[Message]]:
    """将历史拆成“用户提交及其后续往返”分组。"""
    groups: list[list[Message]] = []
    current: list[Message] = []
    for message in messages:
        if message.role == "user" and current:
            groups.append(current)
            current = []
        current.append(message)
    if current:
        groups.append(current)
    return groups


def _join_after_summary(summary: Message, recent: list[Message]) -> list[Message]:
    if not recent:
        return [summary]
    if recent[0].role == "tool":
        return [summary]
    if recent[0].role == "user":
        return [
            summary,
            Message(role="assistant", content="已加载上下文摘要与恢复信息，请继续。"),
            *recent,
        ]
    return [summary, *recent]


async def summarize_once(input_, messages: list[Message]) -> str:
    request = Request(messages=build_summary_prompt(messages), tools=[])
    text: list[str] = []
    async for event in _provider_stream(input_.provider, request):
        if event.err is not None:
            raise event.err
        if event.text:
            text.append(event.text)
    return extract_summary("".join(text))


async def ptl_retry(input_, messages: list[Message], first_error: Exception) -> str:
    """摘要请求超限时丢弃最旧用户组后重试。"""
    groups = group_by_user_turn(messages)
    error = first_error
    direct_retries = 0
    while groups:
        if direct_retries < PTL_RETRY_LIMIT:
            groups = groups[1:]
            direct_retries += 1
        else:
            drop = max(1, math.ceil(len(groups) * PTL_DROP_PERCENTAGE))
            groups = groups[drop:]
        if not groups:
            break
        reduced = [message for group in groups for message in group]
        try:
            return await summarize_once(input_, reduced)
        except PromptTooLongError as exc:
            error = exc
    raise error


async def run_summary(input_) -> list[Message]:
    old_messages = input_.conv.messages()
    if not old_messages:
        return []
    snapshot = input_.recovery.snapshot()
    try:
        summary_text = await summarize_once(input_, old_messages)
    except PromptTooLongError as exc:
        summary_text = await ptl_retry(input_, old_messages, exc)
    limits = getattr(input_, "limits", None)
    if limits is None:
        limits = build_context_limits(
            getattr(input_, "context_window", BASE_CONTEXT_WINDOW)
        )
    recovery = build_recovery_attachment(
        snapshot,
        input_.tool_defs,
        recovery_tokens_per_file=limits.recovery_tokens_per_file,
    )
    summary = Message(
        role="user",
        content=f"## 历史会话摘要\n{summary_text}\n\n{recovery}",
    )
    return _join_after_summary(
        summary, pick_recent_tail(old_messages, limits.recent_keep_tokens)
    )


async def auto_compact(input_) -> tuple[list[Message], int, int]:
    before = input_.estimated_token
    try:
        messages = await run_summary(input_)
    except Exception:
        input_.auto_tracking.record_failure()
        raise
    input_.auto_tracking.record_success()
    return messages, before, estimate_tokens(0, messages, 0)


async def force_compact(input_) -> tuple[list[Message], int, int]:
    before = input_.estimated_token
    messages = await run_summary(input_)
    return messages, before, estimate_tokens(0, messages, 0)
