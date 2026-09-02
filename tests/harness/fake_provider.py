"""脚本驱动的假 Provider：评测确定性的核心资产。

两种协议均支持：
- ``FakeProvider``：旧式流协议 ``stream(msgs, tools, system_suffix)``。
- ``RequestProvider``：请求对象协议 ``stream(request)``。

新场景评测先写脚本（每轮一个 ``StreamEvent`` 列表），再写断言。
"""

import json

from endless_code.llm import Message, StreamEvent, ToolCall, ToolDefinition


class FakeProvider:
    """按脚本顺序逐轮回放事件；``repeat_last`` 时重复最后一个脚本。"""

    name = "fake"
    model = "fake-model"

    def __init__(
        self, scripts: list[list[StreamEvent]], *, repeat_last: bool = False
    ) -> None:
        self.scripts = scripts
        self.repeat_last = repeat_last
        self.call_count = 0
        self.requests: list[tuple[list[Message], list[ToolDefinition], str]] = []

    async def stream(self, msgs, tools, system_suffix=""):
        self.requests.append((msgs, tools, system_suffix))
        index = (
            min(self.call_count, len(self.scripts) - 1)
            if self.repeat_last
            else self.call_count
        )
        self.call_count += 1
        for event in self.scripts[index]:
            yield event


class RequestProvider:
    """请求对象协议版本，可检查稳定前缀/提醒/工具定义。"""

    name = "request-fake"
    model = "request-model"

    def __init__(self, scripts: list[list[StreamEvent]]) -> None:
        self.scripts = scripts
        self.requests: list[object] = []
        self.index = 0

    async def stream(self, request):
        self.requests.append(request)
        script = self.scripts[self.index]
        self.index += 1
        for event in script:
            yield event


def text_turn(text: str) -> list[StreamEvent]:
    """一轮纯文本回复脚本。"""
    return [StreamEvent(text=text), StreamEvent(done=True)]


def tool_turn(
    call_id: str, name: str = "echo_tool", value: str = "ok"
) -> list[StreamEvent]:
    """一轮单工具调用脚本。"""
    return [tool_event(call_id, name, value), StreamEvent(done=True)]


def tool_event(call_id: str, name: str = "echo_tool", value: str = "ok") -> StreamEvent:
    return StreamEvent(
        tool_calls=[ToolCall(id=call_id, name=name, input=json.dumps({"value": value}))]
    )
