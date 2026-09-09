"""第二层：摘要、近期原文和摘要请求 PTL 重试。"""

import inspect
import math
from collections.abc import AsyncIterator

from endless_code.compact.budget import ContextBudget
from endless_code.compact.const import (
    PTL_DROP_PERCENTAGE,
    PTL_RETRY_LIMIT,
    RECENT_KEEP_MESSAGES,
)
from endless_code.compact.recovery import build_recovery_attachment
from endless_code.compact.summary_prompt import build_summary_prompt, extract_summary
from endless_code.compact.token import estimate_tokens
from endless_code.llm import Message, PromptTooLongError, Request, StreamEvent

# 只看「摘得出东西」，拦住空白或标记碎片；量够不够由编排层的预算判定。
MIN_SUMMARY_CHARS = 8


class CompactionQualityError(Exception):
    """摘要结果不合格，不能拿它替换历史。"""


def _provider_stream(provider, request: Request) -> AsyncIterator[StreamEvent]:
    stream_fn = provider.stream
    try:
        legacy = len(inspect.signature(stream_fn).parameters) >= 2
    except (TypeError, ValueError):
        legacy = False
    if legacy:
        return stream_fn(request.messages, request.tools, request.reminder)
    return stream_fn(request)


def recent_tail_start(
    messages: list[Message],
    budget: ContextBudget,
    max_tokens: int | None = None,
    min_messages: int | None = None,
) -> int:
    """近期原文保留区的起始下标；空历史返回 0。"""
    if not messages:
        return 0
    keep = budget.recent_keep_tokens
    if max_tokens is not None and max_tokens > 0:
        keep = max_tokens
    floor = RECENT_KEEP_MESSAGES if min_messages is None else max(1, min_messages)
    total = 0
    start = len(messages)
    for count, index in enumerate(range(len(messages) - 1, -1, -1), start=1):
        total += estimate_tokens(0, [messages[index]], 0)
        start = index
        if total >= keep and count >= floor:
            break
    while start > 0 and messages[start].role == "tool":
        start -= 1
    return start


def pick_recent_tail(
    messages: list[Message],
    budget: ContextBudget,
    max_tokens: int | None = None,
    min_messages: int | None = None,
) -> list[Message]:
    """从尾部保留足够的新近原文，且不拆开工具调用配对。"""
    return messages[recent_tail_start(messages, budget, max_tokens, min_messages) :]


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


PLACEHOLDER_TEXT = "已加载上下文摘要与恢复信息，请继续。"
SUMMARY_MESSAGE_PREFIX = "## 历史会话摘要"


def _join_after_summary(summary: Message, recent: list[Message]) -> list[Message]:
    if not recent:
        return [summary]
    if recent[0].role == "tool":
        return [summary]
    if recent[0].role == "user":
        return [summary, Message(role="assistant", content=PLACEHOLDER_TEXT), *recent]
    return [summary, *recent]


def leading_compacted_count(messages: list[Message]) -> int:
    """统计历史开头已被摘要覆盖的前导消息数（摘要消息与固定占位）。"""
    count = 0
    for message in messages:
        if message.role == "user" and message.content.startswith(
            SUMMARY_MESSAGE_PREFIX
        ):
            count += 1
            continue
        if message.role == "assistant" and message.content == PLACEHOLDER_TEXT:
            count += 1
            continue
        break
    return count


async def summarize_once(
    input_, messages: list[Message], previous_summary: str = ""
) -> str:
    request = Request(
        messages=build_summary_prompt(messages, previous_summary), tools=[]
    )
    text: list[str] = []
    async for event in _provider_stream(input_.provider, request):
        if event.err is not None:
            raise event.err
        if event.text:
            text.append(event.text)
    return extract_summary("".join(text))


async def ptl_retry(
    input_, messages: list[Message], first_error: Exception, previous_summary: str = ""
) -> str:
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
            return await summarize_once(input_, reduced, previous_summary)
        except PromptTooLongError as exc:
            error = exc
    raise error


def _summary_message(summary_text: str, recovery: str) -> Message:
    return Message(
        role="user",
        content=f"{SUMMARY_MESSAGE_PREFIX}\n{summary_text}\n\n{recovery}",
    )


async def run_summary(
    input_,
    recent_keep_tokens: int | None = None,
    recent_min_messages: int | None = None,
) -> tuple[list[Message], str]:
    old_messages = input_.conv.messages()
    if not old_messages:
        return [], ""
    snapshot = input_.recovery.snapshot()
    try:
        summary_text = await summarize_once(input_, old_messages)
    except PromptTooLongError as exc:
        summary_text = await ptl_retry(input_, old_messages, exc)
    recovery = build_recovery_attachment(snapshot, input_.tool_defs, input_.budget)
    summary = _summary_message(summary_text, recovery)
    tail = pick_recent_tail(
        old_messages, input_.budget, recent_keep_tokens, recent_min_messages
    )
    return _join_after_summary(summary, tail), summary_text


async def run_rolling_summary(
    input_,
    recent_keep_tokens: int | None = None,
    recent_min_messages: int | None = None,
) -> tuple[list[Message], str]:
    """滚动增量摘要：只摘上次覆盖点之后的新片段，并与上一版摘要合并。"""
    old_messages = input_.conv.messages()
    if not old_messages:
        return [], ""
    state = input_.summary_state
    segment = old_messages[state.covered_messages :]
    snapshot = input_.recovery.snapshot()
    try:
        summary_text = await summarize_once(input_, segment, state.summary_text)
    except PromptTooLongError as exc:
        summary_text = await ptl_retry(input_, segment, exc, state.summary_text)
    recovery = build_recovery_attachment(snapshot, input_.tool_defs, input_.budget)
    summary = _summary_message(summary_text, recovery)
    tail = pick_recent_tail(
        old_messages, input_.budget, recent_keep_tokens, recent_min_messages
    )
    return _join_after_summary(summary, tail), summary_text


async def auto_compact(
    input_,
    recent_keep_tokens: int | None = None,
    recent_min_messages: int | None = None,
) -> tuple[list[Message], int, int]:
    before = input_.estimated_token
    state = input_.summary_state
    rolling = state is not None and state.usable_for_rolling(
        len(input_.conv.messages())
    )
    try:
        if rolling:
            messages, summary_text = await run_rolling_summary(
                input_, recent_keep_tokens, recent_min_messages
            )
        else:
            messages, summary_text = await run_summary(
                input_, recent_keep_tokens, recent_min_messages
            )
    except Exception:
        input_.auto_tracking.record_failure()
        raise
    if len(summary_text.strip()) < MIN_SUMMARY_CHARS:
        input_.auto_tracking.record_failure()
        raise CompactionQualityError("模型返回的摘要为空或过短，已保留原始历史。")
    input_.auto_tracking.record_success()
    # 全量与滚动都要建状态：首次全量摘要同样产生覆盖点，缺了它第二次永远不会走增量。
    if state is not None:
        state.rolling_update(summary_text, leading_compacted_count(messages))
    return messages, before, estimate_tokens(0, messages, 0)


async def force_compact(
    input_,
    recent_keep_tokens: int | None = None,
    recent_min_messages: int | None = None,
) -> tuple[list[Message], int, int]:
    before = input_.estimated_token
    messages, _ = await run_summary(input_, recent_keep_tokens, recent_min_messages)
    return messages, before, estimate_tokens(0, messages, 0)
