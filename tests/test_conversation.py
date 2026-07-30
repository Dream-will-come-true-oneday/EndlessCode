"""Conversation 历史尾部查询测试。"""

from endless_code.conversation import Conversation
from endless_code.llm import ToolCall, ToolResult


def test_last_role_tracks_history() -> None:
    conv = Conversation()
    assert conv.last_role() == ""

    conv.add_user("hello")
    assert conv.last_role() == "user"

    conv.add_assistant_with_tool_calls(
        "checking",
        [ToolCall(id="call-1", name="read_file", input="{}")],
    )
    assert conv.last_role() == "assistant"

    conv.add_tool_results([ToolResult(tool_call_id="call-1", content="ok")])
    assert conv.last_role() == "tool"

    conv.add_assistant("done")
    assert conv.last_role() == "assistant"
