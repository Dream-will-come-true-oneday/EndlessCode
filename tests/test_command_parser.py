"""parse_command 解析规则测试。"""

from endless_code.command.parser import parse_command


def test_plain_text_is_not_command() -> None:
    assert parse_command("你好，帮我看看这段代码") is None


def test_command_without_args() -> None:
    parsed = parse_command("/status")
    assert parsed is not None
    assert parsed.name == "/status"
    assert parsed.args == ""


def test_command_name_lowercased_and_args_preserved() -> None:
    parsed = parse_command("/STATUS 详细 信息")
    assert parsed is not None
    assert parsed.name == "/status"
    assert parsed.args == "详细 信息"


def test_extra_spaces_in_args_are_stripped() -> None:
    parsed = parse_command("/cmd   多余空格   ")
    assert parsed is not None
    assert parsed.name == "/cmd"
    assert parsed.args == "多余空格"


def test_bare_slash_is_empty_command_name() -> None:
    parsed = parse_command("/")
    assert parsed is not None
    assert parsed.name == ""
    assert parsed.args == ""


def test_surrounding_whitespace_is_ignored() -> None:
    parsed = parse_command("  /help  ")
    assert parsed is not None
    assert parsed.name == "/help"
    assert parsed.args == ""
