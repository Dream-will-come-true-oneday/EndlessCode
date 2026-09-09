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


def test_writer_records_model_and_style_markers(tmp_path: Path) -> None:
    directory = tmp_path / "20260909-120000-abcd"
    writer = Writer(str(directory), "m1")
    writer.append(Message(role="user", content="hi"))
    writer.write_model_marker("m1", "m2")
    writer.write_style_marker("concise")
    writer.close()

    rows = [
        json.loads(line)
        for line in (directory / "conversation.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [row.get("type") or row.get("role") for row in rows] == [
        "user",
        "model_switch",
        "style_switch",
    ]
    assert rows[1]["model"] == "m2"
    assert rows[1]["previous"] == "m1"
    assert isinstance(rows[1]["ts"], int)
    assert rows[2]["style"] == "concise"


def test_loader_ignores_switch_markers(tmp_path: Path) -> None:
    directory = tmp_path / "20260909-120000-abcd"
    writer = Writer(str(directory), "m1")
    writer.append(Message(role="user", content="ok"))
    writer.write_model_marker("m1", "m2")
    writer.append(Message(role="assistant", content="reply"))
    writer.write_style_marker("concise")
    writer.close()

    loaded = load_session(str(directory))
    assert [message.role for message in loaded.messages] == ["user", "assistant"]
    assert [message.content for message in loaded.messages] == ["ok", "reply"]


def test_list_sessions_reports_latest_model(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    directory = sessions / "20260909-120000-abcd"
    directory.mkdir(parents=True)
    writer = Writer(str(directory), "m1")
    writer.append(Message(role="user", content="第一条标题"))
    writer.append(Message(role="assistant", content="reply"))
    writer.write_model_marker("m1", "m2")
    writer.close()

    items = list_sessions(str(sessions))
    assert len(items) == 1
    assert items[0].model == "m2"
    assert items[0].title.startswith("第一条标题")
