"""评测结果外部看板：读取 evals/reports/*.json 渲染单文件 HTML。

用法：
- 由 runner.py 在聚合后自动调用；
- 也可独立运行 ``python evals/dashboard.py`` 重新生成。

产出：``evals/reports/dashboard.html``（零依赖静态页，可直接用浏览器打开，
或作为 CI artifact 下载后离线查看）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

REPORT_DIR = Path(__file__).resolve().parent.parent / "evals" / "reports"

_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
  background: #0d1117; color: #e6edf3; padding: 32px; max-width: 1080px;
  margin: 0 auto;
}
h1 { font-size: 22px; margin-bottom: 4px; }
h2 { font-size: 15px; color: #8b949e; font-weight: 500; }
.meta { color: #8b949e; font-size: 13px; margin-bottom: 24px; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 14px; margin-bottom: 28px; }
.kpi { background: #161b22; border: 1px solid #30363d; border-radius: 10px;
  padding: 16px; }
.kpi .label { font-size: 12px; color: #8b949e; margin-bottom: 6px; }
.kpi .value { font-size: 26px; font-weight: 700; }
.kpi .sub { font-size: 12px; color: #8b949e; margin-top: 4px; }
.good { color: #3fb950; } .bad { color: #f85149; } .warn { color: #d29922; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 10px;
  padding: 18px; margin-bottom: 18px; }
.card h3 { font-size: 15px; margin-bottom: 12px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid #21262d; }
th { color: #8b949e; font-weight: 500; }
code { background: #21262d; padding: 1px 5px; border-radius: 4px; font-size: 12px; }
.badge { display: inline-block; padding: 2px 9px; border-radius: 20px;
  font-size: 12px; font-weight: 600; }
.badge.pass { background: rgba(63,185,80,.15); color: #3fb950; }
.badge.fail { background: rgba(248,81,73,.15); color: #f85149; }
.badge.na { background: rgba(139,148,158,.15); color: #8b949e; }
.bar { height: 8px; background: #21262d; border-radius: 4px; overflow: hidden;
  margin-top: 8px; }
.bar > div { height: 100%; background: #3fb950; }
.bar > div.mid { background: #d29922; } .bar > div.low { background: #f85149; }
footer { color: #484f58; font-size: 12px; margin-top: 28px; }
"""


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _bar(percent: float) -> str:
    cls = "good" if percent >= 90 else ("mid" if percent >= 60 else "low")
    return (
        f'<div class="bar"><div class="{cls}" style="width:{min(percent, 100):.1f}%">'
        "</div></div>"
    )


def _kpi(label: str, value: str, sub: str = "", cls: str = "") -> str:
    sub_html = f'<div class="sub">{sub}</div>' if sub else ""
    return (
        f'<div class="kpi"><div class="label">{label}</div>'
        f'<div class="value {cls}">{value}</div>{sub_html}</div>'
    )


def _na_card(title: str) -> str:
    return (
        f'<div class="card"><h3>{title}</h3>'
        '<span class="badge na">未运行 / 无数据</span></div>'
    )


def _fmt_time(unix: object) -> str:
    try:
        local_tz = datetime.now().astimezone().tzinfo
        stamp = datetime.fromtimestamp(int(unix), tz=local_tz)
        return stamp.strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "未知时间"


def _matrix_card(matrix: dict | None) -> str:
    if matrix is None:
        return _na_card("权限红队矩阵")
    miss = matrix.get("critical_miss", [])
    fp = matrix.get("false_positive", [])
    mismatch = matrix.get("mismatched", [])
    miss_cls = "good" if not miss else "bad"
    fp_cls = "good" if not fp else "warn"
    rows = "".join(f"<tr><td>{m}</td></tr>" for m in mismatch) or (
        "<tr><td>无（全部用例与期望一致）</td></tr>"
    )
    return (
        '<div class="card"><h3>权限红队矩阵</h3>'
        "<table>"
        f"<tr><th>用例总数</th><td>{matrix.get('total', '-')}</td></tr>"
        f"<tr><th>critical 用例</th><td>{matrix.get('critical', '-')}</td></tr>"
        f"<tr><th>critical 漏报</th><td class='{miss_cls}'>{len(miss)} "
        f"（漏报率 {matrix.get('critical_miss_rate', 0):.4f}）</td></tr>"
        f"<tr><th>误报（期望 allow 判 deny）</th><td class='{fp_cls}'>{len(fp)}"
        f"（误报率 {matrix.get('false_positive_rate', 0):.4f}）</td></tr>"
        "</table>"
        f"<h3 style='margin-top:14px'>期望偏差用例</h3>"
        f"<table>{rows}</table></div>"
    )


def _llm_card(llm: dict | None) -> str:
    if llm is None:
        return _na_card("真实模型评测（P3，需要 API key）")
    usage = llm.get("usage", {})
    rows = ""
    for task_id, result in llm.get("results", {}).items():
        passed = bool(result.get("passed"))
        badge = "pass" if passed else "fail"
        label = "通过" if passed else "未达标"
        metric = ""
        if "retention" in result:
            metric = f"保留率 {result['retention']}（阈值 {result.get('threshold')}）"
            metric += _bar(result["retention"] * 100)
        elif "ratio" in result:
            metric = f"一致率 {result['ratio']}（阈值 {result.get('threshold')}）"
            metric += _bar(result["ratio"] * 100)
        elif "current" in result:
            metric = (
                f"tokens {result['current'].get('input_tokens')}/"
                f"{result['current'].get('output_tokens')}"
            )
            if result.get("baseline"):
                dev = result.get("deviation", {})
                metric += f"<br>偏差 in={dev.get('input')} out={dev.get('output')}"
        extra = result.get("note", "")
        rows += (
            f"<tr><td><code>{task_id}</code></td>"
            f"<td><span class='badge {badge}'>{label}</span></td>"
            f"<td>{metric}{('<br>' + extra) if extra else ''}</td></tr>"
        )
    return (
        f'<div class="card"><h3>真实模型评测 <span style="color:#8b949e;font-weight:400">'
        f"（{llm.get('model', '-')}）</span></h3>"
        f"<p class='meta'>调用 {llm.get('calls', '-')} 次，"
        f"tokens in={usage.get('input_tokens', '-')} "
        f"out={usage.get('output_tokens', '-')} "
        f"cache_read={usage.get('cache_read_tokens', '-')}</p>"
        f"<table>{rows}</table></div>"
    )


def _perf_card(summary: dict | None) -> str:
    if summary is None:
        return _na_card("性能 / 成本基线")
    perf = summary.get("perf")
    cost = summary.get("cost")
    rows = ""
    if isinstance(perf, dict):
        rows += (
            f"<tr><th>10 轮工具调用循环耗时</th>"
            f"<td>{perf.get('loop_wall_seconds', '-')} s</td></tr>"
            f"<tr><th>循环 tokens</th><td>"
            f"{perf.get('loop_input_tokens', '-')}/"
            f"{perf.get('loop_output_tokens', '-')}</td></tr>"
        )
    if isinstance(cost, dict):
        rows += (
            f"<tr><th>单轮成本 tokens</th><td>"
            f"{cost.get('input_tokens', '-')}/{cost.get('output_tokens', '-')}"
            f"</td></tr>"
        )
    if not rows:
        return _na_card("性能 / 成本基线")
    return f'<div class="card"><h3>性能 / 成本基线</h3><table>{rows}</table></div>'


def _render(summary: dict, matrix: dict | None, llm: dict | None) -> str:
    coverage = summary.get("coverage_percent")
    matrix_meta = summary.get("permission_matrix", {})
    miss_rate = matrix_meta.get("critical_miss_rate", 0)
    cov_value = f"{coverage}%" if coverage is not None else "无数据"
    cov_cls = "good" if (coverage or 0) >= 85 else "warn"
    miss_value = str(matrix_meta.get("critical", "-")) + " 例"
    kpis = [
        _kpi("测试覆盖率", cov_value, "阈值 85%", cov_cls),
        _kpi(
            "critical 漏报",
            "0" if miss_rate == 0 else f"{miss_rate:.2%}",
            f"共 {miss_value} 用例",
            "good" if miss_rate == 0 else "bad",
        ),
        _kpi(
            "权限矩阵误报率",
            f"{matrix_meta.get('false_positive_rate', 0):.2%}",
            f"共 {matrix_meta.get('total', '-')} 用例",
            "good" if not matrix_meta.get("false_positive_rate") else "warn",
        ),
    ]
    if llm is not None:
        results = llm.get("results", {})
        passed = sum(1 for r in results.values() if r.get("passed"))
        kpis.append(
            _kpi(
                "LLM 评测任务",
                f"{passed}/{len(results)}",
                f"调用 {llm.get('calls', '-')} 次",
                "good" if passed == len(results) else "bad",
            )
        )
    kpi_html = "".join(kpis)
    return (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>endless-code 评测看板</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n"
        "<h1>endless-code 评测看板</h1>\n"
        f"<p class='meta'>生成时间：{_fmt_time(summary.get('generated_at'))}"
        "　|　数据来源：evals/reports/*.json</p>\n"
        f"<div class='kpis'>{kpi_html}</div>\n"
        f"{_matrix_card(matrix)}\n"
        f"{_llm_card(llm)}\n"
        f"{_perf_card(summary)}\n"
        "<footer>endless-code 评测体系 · 权限矩阵 / 覆盖率 / 性能 / LLM-as-judge"
        "</footer>\n</body>\n</html>\n"
    )


def generate(report_dir: Path = REPORT_DIR) -> Path | None:
    """读取报告 JSON 并渲染 dashboard.html；无 summary.json 时返回 None。"""
    summary = _read(report_dir / "summary.json")
    if summary is None:
        return None
    matrix = _read(report_dir / "permission_matrix.json")
    llm = _read(report_dir / "llm_eval.json")
    html = _render(summary, matrix, llm)
    out_path = report_dir / "dashboard.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


if __name__ == "__main__":
    path = generate()
    print(f"[dashboard] {path}" if path else "[dashboard] 缺少 summary.json，跳过")
