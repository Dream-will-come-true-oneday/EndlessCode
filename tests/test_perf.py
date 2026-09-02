"""L5 性能/成本基线：FakeLLM 下的循环开销与 token 构成。

阈值刻意放宽（防灾难性回归）；精确趋势对比由 ``evals/runner.py`` 输出
``evals/reports/perf.json`` 承担。
"""

import json
import time
from pathlib import Path

import pytest
from harness.fake_provider import FakeProvider, text_turn, tool_turn
from harness.tools import EchoTool, make_registry

from endless_code.agent import Agent
from endless_code.conversation import Conversation
from endless_code.llm import StreamEvent, Usage
from endless_code.permission import Mode

pytestmark = pytest.mark.perf

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = REPO_ROOT / "evals" / "reports"


def _write_report(name: str, payload: dict) -> None:
    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pytest.skip("报告目录不可写")


async def test_agent_loop_overhead_baseline() -> None:
    """10 轮工具循环的纯调度开销必须保持在可接受范围（无网络）。"""
    scripts = [tool_turn(f"c{i}") for i in range(9)] + [text_turn("done")]
    provider = FakeProvider(scripts)
    conv = Conversation()
    conv.add_user("loop")
    agent = Agent(provider, make_registry(EchoTool()))

    start = time.perf_counter()
    events = [event async for event in agent.run(conv, mode=Mode.BYPASS)]
    elapsed = time.perf_counter() - start

    assert provider.call_count == 10
    assert any(event.done for event in events)
    assert elapsed < 5.0, f"10 轮循环耗时 {elapsed:.2f}s，疑似调度回归"

    _write_report(
        "perf.json",
        {
            "loop_rounds": provider.call_count,
            "loop_wall_seconds": round(elapsed, 4),
            "per_round_ms": round(elapsed / provider.call_count * 1000, 2),
        },
    )


async def test_usage_tokens_are_aggregated() -> None:
    """多轮 usage 逐轮上报：成本指标的数据来源必须稳定。"""
    provider = FakeProvider(
        [
            [
                StreamEvent(usage=Usage(100, 20, 50, 10)),
                *tool_turn("cost-call")[0:1],
                StreamEvent(done=True),
            ],
            [
                StreamEvent(usage=Usage(120, 30, 0, 110)),
                StreamEvent(text="done"),
                StreamEvent(done=True),
            ],
        ]
    )
    conv = Conversation()
    conv.add_user("cost")
    events = [
        event
        async for event in Agent(provider, make_registry(EchoTool())).run(
            conv, mode=Mode.BYPASS
        )
    ]
    usages = [event.usage for event in events if event.usage]
    assert len(usages) == 2
    total_in = sum(usage.input_tokens for usage in usages)
    total_out = sum(usage.output_tokens for usage in usages)
    total_cache_read = sum(usage.cache_read for usage in usages)
    assert (total_in, total_out, total_cache_read) == (220, 50, 120)

    _write_report(
        "cost.json",
        {
            "input_tokens": total_in,
            "output_tokens": total_out,
            "cache_write": sum(usage.cache_write for usage in usages),
            "cache_read": total_cache_read,
        },
    )
