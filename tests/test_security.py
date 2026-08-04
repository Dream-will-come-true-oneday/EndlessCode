"""安全输出与工具参数摘要测试。"""

import json

from endless_code.security import redact_sensitive, summarize_tool_args

TEST_SECRET = "sk-review-secret-123456"


def test_redacts_exact_and_pattern_keys() -> None:
    text = f"token={TEST_SECRET} api_key=deepseek-secret-value"
    redacted = redact_sensitive(text, {TEST_SECRET})
    assert TEST_SECRET not in redacted
    assert "deepseek-secret-value" not in redacted
    assert redacted.count("[REDACTED]") == 2


def test_redaction_handles_empty_and_short_secrets() -> None:
    assert redact_sensitive(None) == ""
    assert redact_sensitive("abc", {"abc"}) == "abc"


def test_write_and_edit_summaries_hide_content() -> None:
    write_args = json.dumps({"path": "a.txt", "content": TEST_SECRET})
    edit_args = json.dumps(
        {"path": "a.txt", "old_string": TEST_SECRET, "new_string": "replacement"}
    )
    write_summary = summarize_tool_args("write_file", write_args, {TEST_SECRET})
    edit_summary = summarize_tool_args("edit_file", edit_args, {TEST_SECRET})
    assert TEST_SECRET not in write_summary
    assert TEST_SECRET not in edit_summary
    assert "content=<23 chars>" in write_summary
    assert "old=<23 chars>" in edit_summary


def test_bash_summary_redacts_command() -> None:
    args = json.dumps({"command": f"echo {TEST_SECRET}"})
    summary = summarize_tool_args("bash", args, {TEST_SECRET})
    assert TEST_SECRET not in summary
    assert "[REDACTED]" in summary


def test_search_summary_and_invalid_json() -> None:
    args = json.dumps({"pattern": "Registry", "path": "src", "glob": "*.py"})
    assert summarize_tool_args("grep", args) == "pattern=Registry, path=src, glob=*.py"
    invalid = summarize_tool_args("unknown", f"not-json {TEST_SECRET}", {TEST_SECRET})
    assert TEST_SECRET not in invalid
