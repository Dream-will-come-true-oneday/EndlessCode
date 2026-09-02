"""P3 真实模型评测（LLM-as-judge）：``python evals/llm_eval.py``。

前置条件：
- 环境变量 ``DEEPSEEK_API_KEY`` 已设置（本地或 CI secrets）；
- 可用 ``EVAL_MODEL`` 覆盖默认模型（deepseek-chat）。

调用预算（全部任务合计约 17 次 API 调用）：
- compact-retention：1 次长历史摘要 + 10 次事实问答 + 1 次批量 judge；
- consistency：3 次重复生成 + 1 次 judge；
- cost：1 次固定任务基线调用（与上次报告对比，规则判定零额外调用）。

产出：``evals/reports/llm_eval.json``（供 runner.py 聚合与看板渲染）。
退出码：任一任务未达阈值返回 1；缺 API key 返回 2。
"""

from __future__ import annotations

import asyncio
import difflib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "evals" / "reports"
TASKS_PATH = ROOT / "evals" / "cases" / "llm_tasks.yaml"

_NAMES = [
    "张伟",
    "李娜",
    "王强",
    "刘洋",
    "陈静",
    "杨帆",
    "赵磊",
    "黄敏",
    "周涛",
    "吴倩",
    "徐鹏",
    "孙丽",
    "马超",
    "朱琳",
    "胡军",
    "郭婷",
    "何平",
    "高远",
    "林悦",
    "罗成",
    "郑洁",
    "梁波",
    "谢强",
    "宋健",
    "唐蕾",
    "韩雪",
    "冯刚",
    "曹颖",
    "彭飞",
    "董洁",
]


def _construct_history(rounds: int) -> tuple[list, list[dict]]:
    """构造合成编码历史：每回合登记一个可验证的模块事实。"""
    from endless_code.llm import Message

    messages: list = []
    facts: list[dict] = []
    for i in range(rounds):
        name = _NAMES[i % len(_NAMES)]
        codename = f"MODULE-{i:02d}"
        deadline = f"2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"
        hours = 8 + (i * 3) % 40
        facts.append(
            {
                "id": f"fact-{i:02d}",
                "module": codename,
                "question": f"模块 {codename} 的负责人、截止日期和预计工时分别是多少？",
                "expected": f"负责人 {name}，截止日期 {deadline}，预计工时 {hours} 小时",
                "tokens": [name, deadline, str(hours)],
            }
        )
        messages.append(
            Message(
                role="user",
                content=(
                    f"任务登记：模块 {codename} 的负责人是 {name}，"
                    f"截止日期 {deadline}，预计工时 {hours} 小时。请确认。"
                ),
            )
        )
        messages.append(
            Message(
                role="assistant",
                content=(
                    f"已确认：模块 {codename}，负责人 {name}，"
                    f"截止 {deadline}，工时 {hours} 小时。"
                ),
            )
        )
    return messages, facts


async def _call(provider, messages: list) -> tuple[str, object]:
    """单次真实调用：收集回复文本与 usage。"""
    from endless_code.llm import Request, Usage

    text_parts: list[str] = []
    usage = Usage()
    async for event in provider.stream(Request(messages=messages, tools=[])):
        if event.err is not None:
            raise event.err
        if event.text:
            text_parts.append(event.text)
        if event.usage is not None:
            usage = event.usage
    return "".join(text_parts), usage


def _parse_json_array(text: str) -> list | None:
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def _parse_json_object(text: str) -> dict | None:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _rule_verdicts(qa_pairs: list[dict]) -> list[dict]:
    """judge 输出解析失败时的规则兜底：关键 token 全部出现即 pass。"""
    out = []
    for qa in qa_pairs:
        ok = all(token in qa["answer"] for token in qa["fact"]["tokens"])
        out.append(
            {
                "id": qa["fact"]["id"],
                "verdict": "pass" if ok else "fail",
                "reason": "规则兜底判定（judge 输出解析失败）",
            }
        )
    return out


async def run_compact_retention(provider, task: dict) -> tuple[dict, list]:
    """压缩保留率：摘要后的事实问答正确率（1 + N + 1 次调用）。"""
    from endless_code.compact.summary_prompt import build_summary_prompt
    from endless_code.llm import Message

    rounds = int(task.get("rounds", 30))
    facts_count = int(task.get("facts", 10))
    threshold = float(task.get("threshold", 0.9))

    history, all_facts = _construct_history(rounds)
    step = max(1, len(all_facts) // facts_count)
    picked = all_facts[::step][:facts_count]

    usages = []
    summary_text, usage = await _call(provider, build_summary_prompt(history))
    usages.append(usage)

    qa_pairs: list[dict] = []
    for fact in picked:
        question = (
            "以下是一次编码会话的摘要：\n\n"
            f"{summary_text}\n\n"
            '请仅根据以上摘要回答问题；摘要未提及的信息回答"摘要未提及"。'
            "只输出答案本身，不要解释。\n\n"
            f"问题：{fact['question']}"
        )
        answer, answer_usage = await _call(
            provider, [Message(role="user", content=question)]
        )
        qa_pairs.append({"fact": fact, "answer": answer.strip()})
        usages.append(answer_usage)

    judge_payload = json.dumps(
        [
            {
                "id": qa["fact"]["id"],
                "question": qa["fact"]["question"],
                "标准答案": qa["fact"]["expected"],
                "回答": qa["answer"],
            }
            for qa in qa_pairs
        ],
        ensure_ascii=False,
    )
    judge_prompt = (
        "你是严格的评测判官。对下列每组问答，判断「回答」是否正确传达了"
        "「标准答案」中的关键信息：姓名、日期、数字必须一致，表述可以不同；"
        "回答「摘要未提及」判 fail。\n\n"
        f"{judge_payload}\n\n"
        "只输出 JSON 数组，共 "
        f"{len(qa_pairs)} 项，格式："
        '[{"id": "fact-00", "verdict": "pass"或"fail", "reason": "一句话理由"}]'
    )
    judge_text, judge_usage = await _call(
        provider, [Message(role="user", content=judge_prompt)]
    )
    usages.append(judge_usage)

    verdicts = _parse_json_array(judge_text)
    judge_ok = verdicts is not None and len(verdicts) == len(qa_pairs)
    if not judge_ok:
        verdicts = _rule_verdicts(qa_pairs)
    verdict_by_id = {v.get("id"): v for v in verdicts if isinstance(v, dict)}

    details = []
    correct = 0
    for qa in qa_pairs:
        verdict = verdict_by_id.get(qa["fact"]["id"], {})
        passed = verdict.get("verdict") == "pass"
        correct += int(passed)
        details.append(
            {
                "id": qa["fact"]["id"],
                "expected": qa["fact"]["expected"],
                "answer": qa["answer"],
                "verdict": verdict.get("verdict", "fail"),
                "reason": verdict.get("reason", ""),
            }
        )

    retention = correct / max(1, len(qa_pairs))
    return (
        {
            "retention": round(retention, 4),
            "threshold": threshold,
            "passed": retention >= threshold,
            "judge_fallback": not judge_ok,
            "summary_chars": len(summary_text),
            "details": details,
        },
        usages,
    )


def _similarity_ratio(outputs: list[str]) -> float:
    """judge 解析失败时的规则兜底：输出两两文本相似度均值。"""
    pairs = [
        (outputs[i], outputs[j])
        for i in range(len(outputs))
        for j in range(i + 1, len(outputs))
    ]
    if not pairs:
        return 1.0
    total = sum(difflib.SequenceMatcher(None, a, b).ratio() for a, b in pairs)
    return total / len(pairs)


async def run_consistency(provider, task: dict) -> tuple[dict, list]:
    """输出稳定性：同提示词重复 N 次的一致率（N + 1 次调用）。"""
    from endless_code.llm import Message

    repeats = int(task.get("repeats", 3))
    threshold = float(task.get("threshold", 0.9))
    prompt = task["prompt"]

    # 评测不走工具循环：若任务要求阅读文件，将内容直接作为上下文提供
    readme = ROOT / "README.md"
    if "README.md" in prompt and readme.exists():
        context = readme.read_text(encoding="utf-8")[:2000]
        prompt = f"以下是 README.md 的内容：\n\n{context}\n\n---\n\n{prompt}"

    usages = []
    outputs: list[str] = []
    for _ in range(repeats):
        text, usage = await _call(provider, [Message(role="user", content=prompt)])
        outputs.append(text.strip())
        usages.append(usage)

    judge_prompt = (
        "你是严格的评测判官。以下是对同一提示词的 "
        f"{repeats} 次独立回答，请评估它们在关键结论上的一致程度"
        "（项目定位、核心要点是否一致，措辞差异忽略）。"
        "只输出 JSON 对象，格式："
        '{"ratio": 0.0到1.0的一致率, "reason": "一句话理由"}\n\n'
    )
    for idx, output in enumerate(outputs, start=1):
        judge_prompt += f"--- 回答 {idx} ---\n{output}\n\n"
    judge_text, judge_usage = await _call(
        provider, [Message(role="user", content=judge_prompt)]
    )
    usages.append(judge_usage)

    parsed = _parse_json_object(judge_text)
    if parsed is not None and isinstance(parsed.get("ratio"), (int, float)):
        ratio = float(parsed["ratio"])
        judge_ok = True
    else:
        ratio = _similarity_ratio(outputs)
        judge_ok = False

    return (
        {
            "ratio": round(ratio, 4),
            "threshold": threshold,
            "passed": ratio >= threshold,
            "judge_fallback": not judge_ok,
            "outputs": outputs,
        },
        usages,
    )


async def run_cost(provider, task: dict, previous: dict | None) -> tuple[dict, list]:
    """成本基线：固定任务与上次报告对比（1 次调用）。"""
    from endless_code.llm import Message

    max_deviation = float(task.get("max_deviation", 0.2))
    _text, usage = await _call(provider, [Message(role="user", content=task["prompt"])])
    current = {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }
    baseline = (previous or {}).get("cost")
    result: dict = {"current": current, "max_deviation": max_deviation}
    if isinstance(baseline, dict) and baseline.get("input_tokens"):
        dev_in = abs(current["input_tokens"] - baseline["input_tokens"]) / max(
            1, baseline["input_tokens"]
        )
        dev_out = abs(current["output_tokens"] - baseline["output_tokens"]) / max(
            1, baseline["output_tokens"]
        )
        result["baseline"] = baseline
        result["deviation"] = {
            "input": round(dev_in, 4),
            "output": round(dev_out, 4),
        }
        result["passed"] = dev_in <= max_deviation and dev_out <= max_deviation
    else:
        result["passed"] = True
        result["note"] = "首次运行，记录成本基线"
    return result, [usage]


def _sum_usage(usages: list) -> dict:
    from endless_code.llm import Usage

    total = Usage()
    for usage in usages:
        total.input_tokens += usage.input_tokens
        total.output_tokens += usage.output_tokens
        total.cache_read += usage.cache_read
    return {
        "input_tokens": total.input_tokens,
        "output_tokens": total.output_tokens,
        "cache_read_tokens": total.cache_read,
    }


async def evaluate(provider, tasks: list[dict], previous: dict | None) -> dict:
    results: dict = {}
    usages: list = []
    for task in tasks:
        kind = task["kind"]
        if kind == "compact_retention":
            result, task_usages = await run_compact_retention(provider, task)
        elif kind == "consistency":
            result, task_usages = await run_consistency(provider, task)
        elif kind == "cost":
            result, task_usages = await run_cost(provider, task, previous)
        else:
            result = {"passed": False, "note": f"未知任务类型：{kind}"}
            task_usages = []
        results[task["id"]] = result
        usages.extend(task_usages)
    return {"results": results, "usage": _sum_usage(usages), "calls": len(usages)}


def main() -> int:
    import yaml

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("[llm_eval] 缺少环境变量 DEEPSEEK_API_KEY，跳过评测", file=sys.stderr)
        return 2

    model = os.environ.get("EVAL_MODEL", "deepseek-chat")
    tasks = yaml.safe_load(TASKS_PATH.read_text(encoding="utf-8"))["tasks"]

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    previous = None
    try:
        previous = json.loads(
            (REPORT_DIR / "llm_eval.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        pass

    from endless_code.config import ProviderConfig
    from endless_code.llm import new_provider

    provider = new_provider(
        ProviderConfig(
            name="deepseek",
            protocol="deepseek",
            api_key="$DEEPSEEK_API_KEY",
            model=model,
        )
    )
    print(f"[llm_eval] model={model} tasks={len(tasks)}")
    report = asyncio.run(evaluate(provider, tasks, previous))
    report["generated_at"] = int(time.time())
    report["model"] = model

    out_path = REPORT_DIR / "llm_eval.json"
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    usage = report["usage"]
    parts = [
        f"calls={report['calls']}",
        f"tokens={usage['input_tokens']}+{usage['output_tokens']}",
    ]
    failed = []
    for task_id, result in report["results"].items():
        status = "pass" if result.get("passed") else "FAIL"
        parts.append(f"{task_id}={status}")
        if not result.get("passed"):
            failed.append(task_id)
    print("[llm_eval] " + " ".join(parts))

    if failed:
        print(f"[llm_eval] 未达标任务：{failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
