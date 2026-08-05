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


def test_callbacks_receive_independent_append_and_replace_snapshots() -> None:
    appended: list[Message] = []
    replaced: list[list[Message]] = []
    conv = Conversation(appended.append, replaced.append)

    conv.add_user("hello")
    conv.replace_history([Message(role="assistant", content="done")])

    assert appended == [Message(role="user", content="hello")]
    assert replaced == [[Message(role="assistant", content="done")]]
    appended[0].content = "changed"
    assert conv.messages()[0].content == "done"


def test_from_messages_does_not_emit_persistence_callback() -> None:
    appended: list[Message] = []
    replaced: list[list[Message]] = []
    conv = Conversation.from_messages(
        [Message(role="user", content="existing")], appended.append, replaced.append
    )
    assert conv.messages()[0].content == "existing"
    assert appended == []
    assert replaced == []


def test_replace_history_does_not_notify_for_unchanged_content() -> None:
    replaced: list[list[Message]] = []
    conv = Conversation(on_replace=replaced.append)
    conv.add_user("same")
    conv.replace_history(conv.messages())
    assert replaced == []
