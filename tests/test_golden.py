"""L4 golden 回归：系统提示、提醒文本、审计记录与压缩预览的文本契约。

快照位于 ``tests/golden/``。更新契约时显式运行：
``GOLDEN_UPDATE=1 python -m pytest tests/test_golden.py``（首次运行自动创建）。
"""

import json
import os
from pathlib import Path

from endless_code.compact import (
    ContentReplacementState,
    build_context_budget,
    new_session_context,
    offload_and_snip,
)
from endless_code.llm import Message, ToolResult
from endless_code.permission import (
    AuditWriter,
    Category,
    Decision,
    Mode,
    RiskLevel,
)
from endless_code.prompt import (
    EXECUTE_DIRECTIVE,
    PLAN_REMINDER_BRIEF,
    PLAN_REMINDER_FULL,
    build_system_prompt,
)

GOLDEN_DIR = Path(__file__).parent / "golden"
_UPDATE = os.environ.get("GOLDEN_UPDATE") == "1"


def _assert_golden(name: str, content: str) -> None:
    path = GOLDEN_DIR / name
    if _UPDATE or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return
    expected = path.read_text(encoding="utf-8")
    assert content == expected, (
        f"golden 快照不一致：{name}；如为有意变更，"
        "请运行 GOLDEN_UPDATE=1 python -m pytest tests/test_golden.py 刷新"
    )


def test_stable_system_prompt_contract() -> None:
    """稳定系统提示是缓存前缀，任何变更必须显式刷新快照。"""
    prompt = build_system_prompt()
    assert prompt == build_system_prompt()  # 确定性
    _assert_golden("system_prompt.txt", prompt)


def test_reminder_and_directive_contract() -> None:
    sep = "\n\n<<<SEP>>>\n\n"
    joined = f"{PLAN_REMINDER_FULL}{sep}{PLAN_REMINDER_BRIEF}{sep}{EXECUTE_DIRECTIVE}"
    _assert_golden("reminders.txt", joined)


def test_audit_record_schema_contract(tmp_path) -> None:
    """审计记录字段与脱敏行为是外部可见契约。"""
    writer = AuditWriter(tmp_path / "session", "golden-session", secrets=["topsecret"])
    writer.record(
        "permission_checked",
        call_id="c1",
        tool="write_file",
        target="src/a.py",
        mode=Mode.DEFAULT,
        category=Category.WRITE,
        decision=Decision.ASK,
        risk_level=RiskLevel.MEDIUM,
        rule_source="mode",
        reason="default 模式下 WRITE 类操作需确认",
        args={"path": "src/a.py", "content": "topsecret"},
        timestamp=1_700_000_000.0,
    )
    writer.close()
    records = writer.recent(50)
    assert len(records) == 1
    record = records[0]
    assert "topsecret" not in json.dumps(record, ensure_ascii=False)
    _assert_golden(
        "audit_record.json",
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def test_offload_preview_format_contract(tmp_path) -> None:
    """超大工具结果落盘后的预览格式（路径归一化后快照）。"""
    state = ContentReplacementState()
    context = new_session_context(str(tmp_path))
    source = Message(
        role="tool",
        tool_results=[ToolResult(tool_call_id="large", content="y" * 260_000)],
    )
    budget = build_context_budget(1_000_000)
    replaced = offload_and_snip([source], state, context, budget)
    preview = replaced[0].tool_results[0].content
    normalized = preview.replace(context.spill_dir, "<SPILL_DIR>")
    assert "[head preview]" in normalized
    assert "[saved to]" in normalized
    _assert_golden("offload_preview.txt", normalized)
