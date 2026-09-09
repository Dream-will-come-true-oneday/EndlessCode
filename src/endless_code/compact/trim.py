"""L1 确定性裁剪：过时文件读取与旧落盘预览的本地瘦身，全程零模型调用。"""

import copy
import re

from endless_code.compact.budget import ContextBudget
from endless_code.compact.layer2 import recent_tail_start
from endless_code.compact.state import RecoveryState, TrimLedger
from endless_code.llm import Message

SUPERSEDED_PREFIX = "[outdated file read;"
DEGRADED_PREFIX = "[offloaded result;"
_SAVED_TO_PREFIX = "[saved to] "
_OFFLOAD_HEADER_RE = re.compile(
    r"^\[tool result offloaded; original size: (?P<size>\d+) bytes\]"
)


def _superseded_pointer(path: str) -> str:
    return (
        f"{SUPERSEDED_PREFIX} {path}\n"
        "已被更新版本的读取取代；如需原文请重新 read_file。"
    )


def _saved_to_path(content: str) -> str | None:
    for line in content.splitlines():
        if line.startswith(_SAVED_TO_PREFIX):
            return line[len(_SAVED_TO_PREFIX) :].strip()
    return None


def _degrade_preview(content: str) -> str | None:
    """把 L0 落盘预览降级为纯指针；非预览内容返回 None。"""
    match = _OFFLOAD_HEADER_RE.match(content)
    if match is None:
        return None
    saved = _saved_to_path(content)
    if saved is None:
        return None
    return (
        f"{DEGRADED_PREFIX} original size: {match.group('size')} bytes\n"
        f"{_SAVED_TO_PREFIX}{saved}\n"
        "[preview removed to save context]"
    )


def apply_local_trim(
    messages: list[Message],
    recovery: RecoveryState,
    budget: ContextBudget,
    ledger: TrimLedger,
    active: bool,
) -> list[Message]:
    """返回裁剪后的历史副本，不修改传入消息。

    ``active=False`` 时只重放账本中已冻结的指针（字节级幂等）；``active=True``
    时再对近期保留边界之前的内容计算新的 F1/F2 指针。已冻结的 L0 替换决策
    （``ContentReplacementState``）不在此触碰。
    """
    output = copy.deepcopy(messages)
    if not output:
        return output

    for message in output:
        if message.role != "tool" or not message.tool_results:
            continue
        for result in message.tool_results:
            frozen = ledger.pointer_for(result.tool_call_id)
            if frozen is not None:
                result.content = frozen

    if not active:
        return output

    boundary = recent_tail_start(output, budget)
    for message in output[:boundary]:
        if message.role != "tool" or not message.tool_results:
            continue
        for result in message.tool_results:
            current = result.content
            if current.startswith((SUPERSEDED_PREFIX, DEGRADED_PREFIX)):
                continue
            path = recovery.read_path_of(result.tool_call_id)
            if path is not None:
                if recovery.latest_call_for(path) != result.tool_call_id:
                    result.content = ledger.supersede(
                        result.tool_call_id, _superseded_pointer(path)
                    )
                    continue
                # F1 优先于 F2：某路径最新一次的读取不再降级。它可能已被 L0
                # 落盘成预览，此时预览正文是仅存的原文片段，削掉就违背了
                # 「最新版本保留」的承诺。
                continue
            degraded = _degrade_preview(current)
            if degraded is not None:
                result.content = ledger.supersede(result.tool_call_id, degraded)
    return output
