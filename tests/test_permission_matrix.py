"""L6 权限红队矩阵：YAML 数据驱动的五层拦截评测。

用例集位于 ``evals/cases/permission_cases.yaml``。
判定标准：
- ``severity: critical`` 的用例绝不允许被判 ALLOW（漏报一票否决）。
- 其余用例精确断言决策/风险/规则来源，期望被 DENY 外的偏差视为误报线索。
"""

import json
from pathlib import Path

import pytest
import yaml

from endless_code.llm import ToolCall
from endless_code.permission import Decision, parse_mode
from endless_code.permission.engine import new_engine
from endless_code.permission.rule import RuleSet

CASES_PATH = Path(__file__).parent.parent / "evals" / "cases" / "permission_cases.yaml"


def load_cases() -> list[dict]:
    raw = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8"))
    cases = raw["cases"]
    assert cases, "权限矩阵用例集为空"
    return cases


_CASES = load_cases()


def _explain(case: dict, tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    if case.get("settings"):
        settings_dir = tmp_path / ".endless-code"
        settings_dir.mkdir(parents=True, exist_ok=True)
        (settings_dir / "settings.local.yaml").write_text(
            yaml.safe_dump(case["settings"], allow_unicode=True), encoding="utf-8"
        )
    engine, err = new_engine(str(tmp_path))
    assert err is None
    # 隔离用户级全局配置，保证矩阵结果只由用例本身决定
    engine.user = RuleSet()
    call = ToolCall(
        id=case["id"],
        name=case["tool"],
        input=json.dumps(case.get("args", {}), ensure_ascii=False),
    )
    mode, ok = parse_mode(case.get("mode", "default"))
    assert ok, f"用例 {case['id']} 的 mode 非法"
    return engine.explain(mode, call, case.get("read_only", False))


@pytest.mark.parametrize("case", _CASES, ids=[item["id"] for item in _CASES])
def test_permission_matrix_case(case, tmp_path) -> None:
    explanation = _explain(case, tmp_path)
    expect = case["expect"]
    decision = Decision[expect["decision"].upper()]
    assert explanation.decision is decision, (
        f"{case['id']} {case.get('name', '')}: 期望 {decision.name}，"
        f"实际 {explanation.decision.name}（{explanation.reason}）"
    )
    if "risk" in expect:
        assert explanation.risk_level.value == expect["risk"], (
            f"{case['id']}: 期望风险 {expect['risk']}，实际 {explanation.risk_level.value}"
        )
    if "rule_source" in expect:
        assert explanation.rule_source == expect["rule_source"], (
            f"{case['id']}: 期望规则来源 {expect['rule_source']}，"
            f"实际 {explanation.rule_source}"
        )


def test_no_critical_miss_in_matrix(tmp_path) -> None:
    """漏报率一票否决：全部 critical 用例不得出现 ALLOW。"""
    misses = []
    for case in _CASES:
        if case.get("severity") != "critical":
            continue
        # 逐用例独立目录，避免 settings 交叉污染
        explanation = _explain(case, tmp_path / case["id"])
        if explanation.decision is Decision.ALLOW:
            misses.append(case["id"])
    assert not misses, f"critical 用例漏报（被判 ALLOW）：{misses}"


def test_matrix_has_enough_critical_coverage() -> None:
    critical = [case for case in _CASES if case.get("severity") == "critical"]
    assert len(_CASES) >= 30, "红队矩阵规模不足"
    assert len(critical) >= 15, "critical 用例数量不足"
