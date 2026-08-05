"""Conversation 历史尾部查询测试。"""

from endless_code.conversation import Conversation
from endless_code.llm import Message, ToolCall, ToolResult


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


def test_replace_history_uses_deep_copy_and_accepts_none() -> None:
    conv = Conversation()
    messages = [Message(role="user", content="original")]
    conv.replace_history(messages)
    messages[0].content = "changed"
    assert conv.messages()[0].content == "original"
    conv.replace_history(None)
    assert conv.messages() == []
