"""L1 确定性裁剪测试：账本、读取台账与裁剪变换。"""

import json
import threading
from pathlib import Path

from endless_code.compact import (
    build_context_budget,
    new_session_context,
    offload_and_snip,
)
from endless_code.compact.layer1 import build_preview, spill_single
from endless_code.compact.state import (
    ContentReplacementState,
    RecoveryState,
    TrimLedger,
)
from endless_code.compact.trim import (
    DEGRADED_PREFIX,
    SUPERSEDED_PREFIX,
    apply_local_trim,
)
from endless_code.llm import Message, ToolResult


def test_trim_ledger_freezes_first_pointer() -> None:
    ledger = TrimLedger()
    assert ledger.pointer_for("c1") is None
    first = ledger.supersede("c1", "pointer-a")
    assert first == "pointer-a"
    again = ledger.supersede("c1", "pointer-b")
    assert again == "pointer-a"
    assert ledger.pointer_for("c1") == "pointer-a"


def test_trim_ledger_keys_are_independent() -> None:
    ledger = TrimLedger()
    ledger.supersede("c1", "pointer-a")
    ledger.supersede("c2", "pointer-b")
    assert ledger.pointer_for("c1") == "pointer-a"
    assert ledger.pointer_for("c2") == "pointer-b"


def test_recovery_read_ledger_tracks_latest_call() -> None:
    recovery = RecoveryState()
    recovery.record_read("c1", "src/a.py")
    assert recovery.read_path_of("c1") == str(Path("src/a.py").expanduser().resolve())
    assert recovery.latest_call_for("src/a.py") == "c1"

    recovery.record_read("c2", "src/a.py")
    assert recovery.latest_call_for("src/a.py") == "c2"
    assert recovery.read_path_of("c1") == recovery.read_path_of("c2")
    assert recovery.latest_call_for("missing.py") is None
    assert recovery.read_path_of("unknown") is None


def test_recovery_read_ledger_is_thread_safe() -> None:
    recovery = RecoveryState()
    barrier = threading.Barrier(4)

    def worker(index: int) -> None:
        barrier.wait()
        for step in range(50):
            recovery.record_read(f"c-{index}-{step}", f"src/f{index}.py")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for index in range(4):
        assert recovery.latest_call_for(f"src/f{index}.py").startswith(f"c-{index}-")
    assert json.dumps(recovery._latest_call)  # 可序列化，无损坏


def _big_history(first: Message, budget_size: int = 6_000) -> list[Message]:
    """首条消息后拼 5 条大消息，使近期边界落在首条之后。"""
    return [first, *(Message(role="user", content="x" * budget_size) for _ in range(5))]


def test_superseded_file_read_becomes_pointer_and_latest_kept(tmp_path) -> None:
    read_path = str(tmp_path / "a.py")
    recovery = RecoveryState()
    recovery.record_read("c1", read_path)
    recovery.record_read("c2", read_path)
    first = Message(
        role="tool",
        tool_results=[
            ToolResult(tool_call_id="c1", content="旧版本内容"),
            ToolResult(tool_call_id="c2", content="新版本内容"),
        ],
    )
    budget = build_context_budget(128_000)
    ledger = TrimLedger()

    trimmed = apply_local_trim(
        _big_history(first), recovery, budget, ledger, active=True
    )
    results = trimmed[0].tool_results
    assert results[0].content.startswith(SUPERSEDED_PREFIX)
    assert read_path in results[0].content
    assert results[1].content == "新版本内容"  # 最新读取保留原文
    assert trimmed[0].role == "tool"  # 配对完整

    replay = apply_local_trim(trimmed, recovery, budget, ledger, active=True)
    assert [r.content for r in replay[0].tool_results] == [r.content for r in results]


def test_stale_offloaded_preview_degraded_keeps_size_and_path(tmp_path) -> None:
    context = new_session_context(str(tmp_path))
    content = "y" * 300
    spill_path = spill_single(context, "call-big", content)
    preview = build_preview(300, content[:20], spill_path)
    first = Message(role="tool", tool_results=[ToolResult("call-big", preview)])
    budget = build_context_budget(128_000)
    recovery = RecoveryState()
    ledger = TrimLedger()

    trimmed = apply_local_trim(
        _big_history(first), recovery, budget, ledger, active=True
    )
    pointer = trimmed[0].tool_results[0].content
    assert pointer.startswith(DEGRADED_PREFIX)
    assert "original size: 300 bytes" in pointer
    assert spill_path in pointer
    assert "[head preview]" not in pointer  # 预览正文被移除
    assert Path(spill_path).read_text(encoding="utf-8") == content  # 磁盘内容完整


def test_recent_boundary_content_untouched(tmp_path) -> None:
    context = new_session_context(str(tmp_path))
    spill_path = spill_single(context, "call-recent", "z" * 300)
    preview = build_preview(300, "z" * 300, spill_path)
    recovery = RecoveryState()
    ledger = TrimLedger()
    budget = build_context_budget(128_000)
    messages = [
        *(Message(role="user", content="x" * 6_000) for _ in range(5)),
        Message(role="tool", tool_results=[ToolResult("call-recent", preview)]),
    ]

    trimmed = apply_local_trim(messages, recovery, budget, ledger, active=True)
    assert trimmed[-1].tool_results[0].content == preview  # 近期保留范围不动


def test_l0_preview_does_not_cover_l1_pointer_across_rounds(tmp_path) -> None:
    state = ContentReplacementState()
    context = new_session_context(str(tmp_path))
    recovery = RecoveryState()
    ledger = TrimLedger()
    budget = build_context_budget(128_000)
    source = [Message(role="tool", tool_results=[ToolResult("big", "x" * 40_000)])]
    history = _big_history(source[0])

    after_l0 = offload_and_snip(history, state, context, budget)
    after_l1 = apply_local_trim(after_l0, recovery, budget, ledger, active=True)
    pointer = after_l1[0].tool_results[0].content
    assert pointer.startswith(DEGRADED_PREFIX)

    replay_l0 = offload_and_snip(after_l1, state, context, budget)
    assert (
        replay_l0[0].tool_results[0].content.startswith("[tool result offloaded")
    )  # L0 重放会把预览盖回
    replay_l1 = apply_local_trim(replay_l0, recovery, budget, ledger, active=False)
    assert replay_l1[0].tool_results[0].content == pointer  # L1 重放恢复指针且字节一致
