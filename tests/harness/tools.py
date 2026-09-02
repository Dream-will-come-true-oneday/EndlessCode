"""评测用脚本化工具：回声、计时、阻塞与注册辅助。"""

import asyncio
import json
from dataclasses import dataclass, field

from endless_code.tool import Registry, Result


class EchoTool:
    read_only = True

    def name(self) -> str:
        return "echo_tool"

    def description(self) -> str:
        return "echo"

    def parameters(self) -> dict:
        return {"type": "object"}

    async def execute(self, args: str) -> Result:
        data = json.loads(args or "{}")
        return Result(content=data.get("value", "ok"))


class BlockingTool(EchoTool):
    """执行时挂起，直到被取消；用于取消路径评测。"""

    read_only = False

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    def name(self) -> str:
        return "blocking"

    async def execute(self, args: str) -> Result:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return Result(content="unreachable")


@dataclass
class Timeline:
    active_reads: int = 0
    peak_reads: int = 0
    events: list[str] = field(default_factory=list)


class TimedTool(EchoTool):
    """记录执行起止时序，只读工具附带短睡眠以观察并发。"""

    def __init__(self, tool_name: str, read_only: bool, timeline: Timeline) -> None:
        self._name = tool_name
        self.read_only = read_only
        self.timeline = timeline

    def name(self) -> str:
        return self._name

    async def execute(self, args: str) -> Result:
        label = json.loads(args)["value"]
        self.timeline.events.append(f"start:{label}")
        if self.read_only:
            self.timeline.active_reads += 1
            self.timeline.peak_reads = max(
                self.timeline.peak_reads, self.timeline.active_reads
            )
            await asyncio.sleep(0.03)
            self.timeline.active_reads -= 1
        else:
            await asyncio.sleep(0)
        self.timeline.events.append(f"end:{label}")
        return Result(content=label)


def make_registry(*tools) -> Registry:
    registry = Registry()
    for tool in tools:
        registry.register(tool)
    return registry
