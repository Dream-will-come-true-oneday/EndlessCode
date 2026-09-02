"""评测指标汇总入口：``python evals/runner.py``。

产出：
- ``evals/reports/permission_matrix.json``：权限红队矩阵漏报/误报统计；
- ``evals/reports/summary.json``：聚合摘要（矩阵 + 覆盖率 + 性能/成本报告透传）。

退出码：存在 critical 漏报（高危用例被判 ALLOW）时非零（一票否决）。
覆盖率读取 ``evals/reports/coverage.json``（由
``pytest --cov-report=json:evals/reports/coverage.json`` 生成）；
性能/成本读取 ``pytest -m perf`` 写入的 ``perf.json`` / ``cost.json``；
真实模型评测读取 ``python evals/llm_eval.py`` 写入的 ``llm_eval.json``。
聚合后调用 dashboard 模块渲染 ``evals/reports/dashboard.html`` 看板。
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CASES_PATH = ROOT / "evals" / "cases" / "permission_cases.yaml"
REPORT_DIR = ROOT / "evals" / "reports"


def _evaluate_case(case: dict, base: Path) -> str:
    from endless_code.llm import ToolCall
    from endless_code.permission import parse_mode
    from endless_code.permission.engine import new_engine
    from endless_code.permission.rule import RuleSet

    # 每个用例独立项目根，避免前一个用例的 settings 污染判定
    root = base / case["id"]
    root.mkdir(parents=True, exist_ok=True)
    if case.get("settings"):
        settings_dir = root / ".endless-code"
        settings_dir.mkdir(parents=True, exist_ok=True)
        (settings_dir / "settings.local.yaml").write_text(
            yaml.safe_dump(case["settings"], allow_unicode=True), encoding="utf-8"
        )
    engine, err = new_engine(str(root))
    if err is not None:
        raise RuntimeError(f"权限引擎构造失败: {err}")
    engine.user = RuleSet()
    call = ToolCall(
        id=case["id"],
        name=case["tool"],
        input=json.dumps(case.get("args", {}), ensure_ascii=False),
    )
    mode, ok = parse_mode(case.get("mode", "default"))
    if not ok:
        raise ValueError(f"用例 {case['id']} 的 mode 非法")
    return engine.explain(
        mode, call, case.get("read_only", False)
    ).decision.name.lower()


def run_permission_matrix() -> dict:
    cases = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    misses: list[str] = []
    false_positives: list[str] = []
    mismatched: list[str] = []
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        for case in cases:
            actual = _evaluate_case(case, root)
            expected = case["expect"]["decision"]
            if actual != expected:
                mismatched.append(f"{case['id']}: 期望 {expected}，实际 {actual}")
            if case.get("severity") == "critical" and actual == "allow":
                misses.append(case["id"])
            if expected == "allow" and actual == "deny":
                false_positives.append(case["id"])
    benign_allow = (
        sum(1 for case in cases if case["expect"]["decision"] == "allow") or 1
    )
    return {
        "total": len(cases),
        "critical": sum(1 for case in cases if case.get("severity") == "critical"),
        "critical_miss": misses,
        "critical_miss_rate": len(misses)
        / max(1, sum(1 for case in cases if case.get("severity") == "critical")),
        "false_positive": false_positives,
        "false_positive_rate": len(false_positives) / benign_allow,
        "mismatched": mismatched,
    }


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    matrix = run_permission_matrix()
    (REPORT_DIR / "permission_matrix.json").write_text(
        json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary: dict[str, object] = {"generated_at": int(time.time())}
    summary["permission_matrix"] = {
        "total": matrix["total"],
        "critical": matrix["critical"],
        "critical_miss_rate": matrix["critical_miss_rate"],
        "false_positive_rate": round(matrix["false_positive_rate"], 4),
        "mismatched": matrix["mismatched"],
    }

    coverage = _read_json(REPORT_DIR / "coverage.json")
    if coverage is not None:
        summary["coverage_percent"] = round(
            coverage.get("totals", {}).get("percent_covered", 0.0), 2
        )

    for name in ("perf.json", "cost.json"):
        payload = _read_json(REPORT_DIR / name)
        if payload is not None:
            summary[name.removesuffix(".json")] = payload

    llm_eval = _read_json(REPORT_DIR / "llm_eval.json")
    if llm_eval is not None:
        results = llm_eval.get("results", {})
        passed = sum(1 for result in results.values() if result.get("passed"))
        summary["llm_eval"] = {
            "model": llm_eval.get("model"),
            "calls": llm_eval.get("calls"),
            "passed": passed,
            "total": len(results),
            "usage": llm_eval.get("usage"),
        }

    (REPORT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 渲染外部看板（失败不影响评测退出码）
    try:
        sys.path.insert(0, str(ROOT))
        from evals.dashboard import generate as generate_dashboard

        dashboard_path = generate_dashboard(REPORT_DIR)
        if dashboard_path is not None:
            print(f"[evals] dashboard={dashboard_path}")
    except Exception as exc:  # noqa: BLE001
        print(f"[evals] dashboard 生成失败：{exc}", file=sys.stderr)

    parts = [
        f"matrix={matrix['total']}例",
        f"critical漏报={len(matrix['critical_miss'])}",
        f"误报={len(matrix['false_positive'])}",
        f"期望偏差={len(matrix['mismatched'])}",
    ]
    if "coverage_percent" in summary:
        parts.append(f"coverage={summary['coverage_percent']}%")
    if "perf" in summary:
        parts.append(f"loop={summary['perf'].get('loop_wall_seconds')}s")
    if "llm_eval" in summary:
        llm = summary["llm_eval"]
        parts.append(f"llm={llm['passed']}/{llm['total']}")
    print("[evals] " + " ".join(parts))

    if matrix["critical_miss"]:
        print(f"[evals] critical 漏报用例：{matrix['critical_miss']}", file=sys.stderr)
        return 1
    if matrix["mismatched"]:
        print(f"[evals] 期望偏差用例：{matrix['mismatched']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
