import json
from datetime import datetime, timedelta
from pathlib import Path

from endless_code.llm import Message, ToolCall, ToolResult
from endless_code.session import Writer, clean_expired, list_sessions, load_session


def test_writer_persists_messages_and_compaction_for_loader(tmp_path: Path) -> None:
    directory = tmp_path / "20260805-120000-abcd"
    writer = Writer(str(directory), "test-model")
    writer.append(Message(role="user", content="old"))
    writer.write_compact_marker()
    writer.append(Message(role="assistant", content="summary"))
    writer.append(
        Message(
            role="assistant",
            content="tool",
            tool_calls=[ToolCall("call", "read_file", "{}")],
        )
    )
    writer.append(Message(role="tool", tool_results=[ToolResult("call", "contents")]))
    writer.close()

    lines = [
        json.loads(line)
        for line in (directory / "conversation.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert lines[0]["model"] == "test-model"
    assert any(line.get("type") == "compact" for line in lines)
    loaded = load_session(str(directory))
    assert [message.content for message in loaded.messages] == ["summary", "tool", ""]
    assert loaded.messages[-1].tool_results[0].content == "contents"


def test_loader_skips_bad_rows_and_lone_tool_call(tmp_path: Path) -> None:
    directory = tmp_path / "20260805-120000-abcd"
    directory.mkdir()
    (directory / "conversation.jsonl").write_text(
        '{"role":"user","content":"ok","ts":1}\n'
        "{bad json}\n"
        '{"role":"assistant","content":"pending","tool_calls":[{"id":"id","name":"read","input":"{}"}],"ts":2}\n',
        encoding="utf-8",
    )
    loaded = load_session(str(directory))
    assert [message.content for message in loaded.messages] == ["ok"]


def test_list_and_cleanup_only_use_new_session_ids(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    new = sessions / "20260805-120000-abcd"
    old = sessions / "1717000000-abc12345"
    new.mkdir(parents=True)
    old.mkdir()
    (new / "conversation.jsonl").write_text(
        '{"role":"user","content":"hello","model":"m","ts":1}\n', encoding="utf-8"
    )
    assert [item.id for item in list_sessions(str(sessions))] == [new.name]

    expired = sessions / (datetime.now().astimezone() - timedelta(days=31)).strftime(
        "%Y%m%d-%H%M%S"
    )
    expired = expired.with_name(f"{expired.name}-abcd")
    expired.mkdir()
    clean_expired(str(sessions))
    assert not expired.exists()
    assert old.exists()
