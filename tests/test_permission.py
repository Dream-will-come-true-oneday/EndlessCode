"""权限引擎、规则、沙箱、配置与本地放行测试。"""

import json
from pathlib import Path

import pytest

from endless_code.llm import ToolCall
from endless_code.permission import (
    Category,
    Decision,
    Mode,
    Rule,
    RuleSet,
    match_pattern,
    new_engine,
    parse_mode,
    parse_rule,
    sandbox_ok,
)
from endless_code.permission.blacklist import hits_blacklist
from endless_code.permission.persist import _escape_glob, rule_for
from endless_code.permission.sandbox import eval_symlinks_or_ancestor, resolve_root
from endless_code.permission.settings import (
    PermissionsBlock,
    Settings,
    SettingsError,
    categorize,
    extract_target,
    friendly_name,
    load_settings,
    to_rule_set,
)


def _call(name: str, **kwargs) -> ToolCall:
    return ToolCall(id="c1", name=name, input=json.dumps(kwargs))


def test_parse_mode() -> None:
    assert parse_mode("default") == (Mode.DEFAULT, True)
    assert parse_mode("acceptEdits") == (Mode.ACCEPT_EDITS, True)
    assert parse_mode("PLAN") == (Mode.PLAN, True)
    assert parse_mode("bypasspermissions") == (Mode.BYPASS, True)
    assert parse_mode("x") == (Mode.DEFAULT, False)


def test_blacklist_hits_and_ignores_safe_commands() -> None:
    assert hits_blacklist("rm -rf /")
    assert hits_blacklist("rm -fr ~")
    assert not hits_blacklist("git status")
    assert not hits_blacklist("rm -rf ./build")


def test_rules_exact_glob_and_deny_priority() -> None:
    rule, ok = parse_rule("Bash(git *)")
    assert ok
    assert rule.tool == "Bash"
    assert rule.pattern == "git *"
    assert match_pattern("git *", "git status", path_like=False)
    assert not match_pattern("git *", "npm i", path_like=False)
    assert match_pattern("src/**", "src/a/b.py")
    assert not match_pattern("src/**", "docs/x")

    rules = RuleSet(
        allow=[Rule("Bash", "git *", True)],
        deny=[Rule("Bash", "git push", False)],
    )
    assert rules.match("Bash", "git status") == (Decision.ALLOW, True)
    assert rules.match("Bash", "git push") == (Decision.DENY, True)


def test_friendly_name_routes_six_builtins() -> None:
    assert friendly_name("bash") == "Bash"
    assert friendly_name("read_file") == "Read"
    assert friendly_name("write_file") == "Write"
    assert friendly_name("edit_file") == "Edit"
    assert friendly_name("glob") == "Glob"
    assert friendly_name("grep") == "Grep"
    assert friendly_name("mystery") == "mystery"


def test_sandbox_blocks_outside_and_allows_inside(tmp_path) -> None:
    engine, err = new_engine(str(tmp_path))
    assert err is None
    assert sandbox_ok(engine, "a.txt")
    assert sandbox_ok(engine, str(tmp_path / "a.txt"))
    assert not sandbox_ok(engine, "../outside")
    assert not sandbox_ok(engine, str(tmp_path.parent / "outside"))


def test_sandbox_allows_missing_ancestors(tmp_path) -> None:
    engine, _ = new_engine(str(tmp_path))
    assert sandbox_ok(engine, "a/b/c.txt")


def test_sandbox_denies_symlink_escape(tmp_path) -> None:
    outside = tmp_path.parent / "outside-permission-test"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink not available: {exc}")
    engine, _ = new_engine(str(tmp_path))
    assert not sandbox_ok(engine, str(link))


def test_engine_mode_matrix(tmp_path) -> None:
    engine, _ = new_engine(str(tmp_path))
    read = _call("read_file", path="a.txt")
    write = _call("write_file", path="a.txt", content="x")
    bash = _call("bash", command="git status")

    assert engine.check(Mode.DEFAULT, read, True)[0] is Decision.ALLOW
    assert engine.check(Mode.DEFAULT, write, False)[0] is Decision.ASK
    assert engine.check(Mode.DEFAULT, bash, False)[0] is Decision.ASK
    assert engine.check(Mode.ACCEPT_EDITS, write, False)[0] is Decision.ALLOW
    assert engine.check(Mode.ACCEPT_EDITS, bash, False)[0] is Decision.ASK
    assert engine.check(Mode.BYPASS, write, False)[0] is Decision.ALLOW
    assert engine.check(Mode.PLAN, write, False)[0] is Decision.ASK


def test_engine_blacklist_still_denies_in_bypass(tmp_path) -> None:
    engine, _ = new_engine(str(tmp_path))
    call = _call("bash", command="rm -rf /")
    assert engine.check(Mode.BYPASS, call, False)[0] is Decision.DENY


def test_engine_three_layer_priority(tmp_path) -> None:
    engine, _ = new_engine(str(tmp_path))
    engine.project.allow.append(Rule("Bash", "git *", True))
    engine.local.deny.append(Rule("Bash", "git push", False))
    assert (
        engine.check(Mode.DEFAULT, _call("bash", command="git push"), False)[0]
        is Decision.DENY
    )


def test_config_degradation_on_invalid_yaml(tmp_path) -> None:
    settings_dir = tmp_path / ".endless-code"
    settings_dir.mkdir()
    (settings_dir / "settings.yaml").write_text("not: [valid", encoding="utf-8")
    engine, err = new_engine(str(tmp_path))
    assert err is None
    assert (
        engine.check(Mode.DEFAULT, _call("bash", command="git status"), False)[0]
        is Decision.ASK
    )


def test_start_mode_precedence_project_then_local(tmp_path) -> None:
    settings_dir = tmp_path / ".endless-code"
    settings_dir.mkdir()
    (settings_dir / "settings.yaml").write_text(
        "default_mode: plan\npermissions:\n  allow: []\n  deny: []\n",
        encoding="utf-8",
    )
    (settings_dir / "settings.local.yaml").write_text(
        "default_mode: acceptEdits\npermissions:\n  allow: []\n  deny: []\n",
        encoding="utf-8",
    )
    engine, _ = new_engine(str(tmp_path))
    assert engine.start_mode() is Mode.ACCEPT_EDITS


def test_extract_target_failures() -> None:
    assert extract_target(_call("mystery")) == ("", False, False)
    assert extract_target(_call("write_file", content="x")) == ("", True, False)
    assert extract_target(_call("bash")) == ("", False, False)


def test_unknown_side_effect_tool_asks_unless_bypass(tmp_path) -> None:
    engine, _ = new_engine(str(tmp_path))
    call = _call("mystery")
    assert engine.check(Mode.DEFAULT, call, False)[0] is Decision.ASK
    assert engine.check(Mode.BYPASS, call, False)[0] is Decision.ALLOW


def test_persist_local_allow_and_reload(tmp_path) -> None:
    settings_dir = tmp_path / ".endless-code"
    settings_dir.mkdir()
    engine, _ = new_engine(str(tmp_path))
    call = _call("write_file", path="src/x.py", content="x")
    engine.persist_local_allow(call)

    local_file = settings_dir / "settings.local.yaml"
    assert "Write(src/x.py)" in local_file.read_text(encoding="utf-8")

    engine2, _ = new_engine(str(tmp_path))
    assert engine2.check(Mode.DEFAULT, call, False)[0] is Decision.ALLOW


def test_mcp_tool_rule_hits_before_unknown_tool_guard(tmp_path) -> None:
    """MCP 工具的 allow 规则命中时优先于未知工具保护。"""
    engine, _ = new_engine(str(tmp_path))
    call = _call("mcp__demo__echo", text="x")
    # 无规则：未知工具 default 下 Ask
    assert engine.check(Mode.DEFAULT, call, False)[0] is Decision.ASK
    # 写入规则后：命中 allow
    engine.local.allow.append(Rule("mcp__demo__echo", "", True))
    assert engine.check(Mode.DEFAULT, call, False)[0] is Decision.ALLOW


def test_mcp_tool_deny_rule(tmp_path) -> None:
    """MCP 工具的 deny 规则命中即 Deny。"""
    engine, _ = new_engine(str(tmp_path))
    call = _call("mcp__demo__echo", text="x")
    engine.local.deny.append(Rule("mcp__demo__echo", "", False))
    assert engine.check(Mode.DEFAULT, call, False)[0] is Decision.DENY


def test_persist_local_allow_mcp_tool(tmp_path) -> None:
    """「永久允许」MCP 工具写入本地精确规则并生效。"""
    settings_dir = tmp_path / ".endless-code"
    settings_dir.mkdir()
    engine, _ = new_engine(str(tmp_path))
    call = _call("mcp__demo__echo", text="x")
    engine.persist_local_allow(call)

    local_file = settings_dir / "settings.local.yaml"
    assert "mcp__demo__echo" in local_file.read_text(encoding="utf-8")

    engine2, _ = new_engine(str(tmp_path))
    assert engine2.check(Mode.DEFAULT, call, False)[0] is Decision.ALLOW


def test_blacklist_covers_all_dangerous_patterns() -> None:
    dangerous = [
        "rm -rf /",
        "rm -fr ~",
        "dd if=/dev/zero of=/dev/sda bs=1M",
        ":(){ :|:& };:",
        "mkfs.ext4 /dev/sda1",
        "echo x > /dev/sda",
        "chmod -R 777 /",
        "rm -rf /*",
    ]
    assert all(hits_blacklist(command) for command in dangerous)
    assert not hits_blacklist("git status")
    assert not hits_blacklist("rm -rf ./build")


def test_resolve_root_raises_for_missing_dir() -> None:
    with pytest.raises(FileNotFoundError):
        resolve_root("/no/such/root-xyz")


def test_eval_symlinks_or_ancestor_falls_back(tmp_path) -> None:
    (tmp_path / "a").mkdir()
    target = str(tmp_path / "a" / "b" / "c")
    assert eval_symlinks_or_ancestor(target) == str(Path(target).resolve())


def test_sandbox_ok_empty_path_and_prefix_boundary(tmp_path) -> None:
    engine, _ = new_engine(str(tmp_path))
    assert sandbox_ok(engine, "") is True
    sibling = tmp_path.parent / (tmp_path.name + "x")
    assert not sandbox_ok(engine, str(sibling))


def test_new_engine_returns_err_for_unresolvable_root(tmp_path) -> None:
    engine, err = new_engine(str(tmp_path / "missing"))
    assert engine is not None
    assert err is not None


def test_parse_rule_invalid_inputs() -> None:
    assert parse_rule("") == (Rule("", "", False), False)
    assert parse_rule("Bash(git *")[1] is False
    assert parse_rule("bad-tool(pat)")[1] is False
    rule, ok = parse_rule("Bash")
    assert ok
    assert rule.tool == "Bash"
    assert rule.pattern == ""


def test_match_pattern_empty_question_and_escape() -> None:
    assert match_pattern("", "anything") is True
    assert match_pattern("a?c", "abc", path_like=False) is True
    assert match_pattern("a?c", "ac", path_like=False) is False
    assert match_pattern(r"a\*b", "a*b", path_like=False) is True
    assert match_pattern("**", "anything here", path_like=False) is True
    assert match_pattern("src/a/b.py", "src/a/b.py") is True


def test_load_settings_invalid_structures(tmp_path) -> None:
    missing = tmp_path / "missing.yaml"
    assert load_settings(missing).default_mode == ""

    bad = tmp_path / "bad.yaml"
    bad.write_text("not: [valid", encoding="utf-8")
    with pytest.raises(SettingsError):
        load_settings(bad)

    bad.write_text(
        "default_mode: 123\npermissions: {allow: [], deny: []}\n", encoding="utf-8"
    )
    with pytest.raises(SettingsError):
        load_settings(bad)

    bad.write_text("permissions: not-a-dict\n", encoding="utf-8")
    with pytest.raises(SettingsError):
        load_settings(bad)

    bad.write_text("permissions:\n  allow: notalist\n  deny: []\n", encoding="utf-8")
    with pytest.raises(SettingsError):
        load_settings(bad)


def test_load_settings_filters_non_string_entries(tmp_path) -> None:
    target = tmp_path / "s.yaml"
    target.write_text(
        "default_mode: plan\n"
        "permissions:\n"
        "  allow: [Write(src/x.py), 123, true]\n"
        "  deny: []\n",
        encoding="utf-8",
    )
    settings = load_settings(target)
    assert settings.default_mode == "plan"
    assert settings.permissions.allow == ["Write(src/x.py)"]
    assert settings.permissions.deny == []


def test_to_rule_set_skips_invalid_entries() -> None:
    settings = Settings(
        default_mode="",
        permissions=PermissionsBlock(
            allow=["Read(src/*.py)", "bad-tool(x)"],
            deny=["Bash(rm *)", "not valid("],
        ),
    )
    rules = to_rule_set(settings)
    assert len(rules.allow) == 1
    assert len(rules.deny) == 1
    assert rules.allow[0].tool == "Read"
    assert rules.deny[0].tool == "Bash"


def test_categorize() -> None:
    assert categorize("read_file", True) is Category.READ
    assert categorize("write_file", False) is Category.WRITE
    assert categorize("edit_file", False) is Category.WRITE
    assert categorize("bash", False) is Category.EXEC
    assert categorize("mystery", False) is Category.EXEC


def test_extract_target_happy_paths() -> None:
    assert extract_target(_call("read_file", path="a.txt")) == ("a.txt", True, True)
    assert extract_target(_call("write_file", path="b.txt", content="x")) == (
        "b.txt",
        True,
        True,
    )
    assert extract_target(_call("glob", pattern="*.py")) == (".", True, True)
    assert extract_target(_call("grep", pattern="x", path="src")) == ("src", True, True)
    assert extract_target(_call("bash", command="git status")) == (
        "git status",
        False,
        True,
    )


def test_escape_glob() -> None:
    assert _escape_glob("a*b?c[d]\\e") == r"a\*b\?c\[d\]\\e"


def test_rule_for_mcp_and_unknown(tmp_path) -> None:
    engine, _ = new_engine(str(tmp_path))
    mcp_rule, text, ok = rule_for(engine, _call("mcp__demo__echo", text="x"))
    assert ok
    assert text == "mcp__demo__echo"
    assert mcp_rule.tool == "mcp__demo__echo"
    assert mcp_rule.pattern == ""

    rule, text, ok = rule_for(engine, _call("mystery"))
    assert not ok
    assert text == ""
    assert rule == Rule("", "", False)


def test_persist_local_allow_idempotent_and_preserves_existing(tmp_path) -> None:
    settings_dir = tmp_path / ".endless-code"
    settings_dir.mkdir()
    (settings_dir / "settings.local.yaml").write_text(
        "default_mode: plan\n"
        "permissions:\n"
        "  allow:\n"
        "    - Read(src/readme.md)\n"
        "  deny: []\n",
        encoding="utf-8",
    )
    engine, _ = new_engine(str(tmp_path))
    call = _call("write_file", path="src/x.py", content="x")
    engine.persist_local_allow(call)
    engine.persist_local_allow(call)

    text = (settings_dir / "settings.local.yaml").read_text(encoding="utf-8")
    assert "default_mode: plan" in text
    assert "Read(src/readme.md)" in text
    assert text.count("Write(src/x.py)") == 1


def test_persist_local_allow_raises_for_unknown_tool(tmp_path) -> None:
    engine, _ = new_engine(str(tmp_path))
    with pytest.raises(ValueError):
        engine.persist_local_allow(_call("mystery"))


def test_parse_mode_non_string() -> None:
    assert parse_mode(123) == (Mode.DEFAULT, False)


def test_engine_denies_unparseable_file_path(tmp_path) -> None:
    engine, _ = new_engine(str(tmp_path))
    call = _call("write_file", content="x")
    assert engine.check(Mode.DEFAULT, call, False)[0] is Decision.DENY


def test_engine_denies_file_outside_root(tmp_path) -> None:
    engine, _ = new_engine(str(tmp_path))
    call = _call("write_file", path="../outside.txt", content="x")
    assert engine.check(Mode.DEFAULT, call, False)[0] is Decision.DENY


def test_rule_for_bash_command_escapes_glob(tmp_path) -> None:
    engine, _ = new_engine(str(tmp_path))
    rule, text, ok = rule_for(engine, _call("bash", command="git *status"))
    assert ok
    assert rule.tool == "Bash"
    assert rule.pattern == r"git \*status"
    assert text == r"Bash(git \*status)"


def test_match_pattern_path_wildcards_and_boundary() -> None:
    assert match_pattern("src/*.py", "src/a.py") is True
    assert match_pattern("src/a?c.py", "src/abc.py") is True
    assert match_pattern(r"src/a\*b.py", "src/a*b.py") is True
    assert match_pattern("a/b", "a") is False
    assert match_pattern("a/**/z", "a/b/c") is False


def test_load_settings_empty_and_root_not_mapping(tmp_path) -> None:
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    assert load_settings(empty).default_mode == ""

    null = tmp_path / "null.yaml"
    null.write_text("null\n", encoding="utf-8")
    assert load_settings(null).default_mode == ""

    not_mapping = tmp_path / "not_mapping.yaml"
    not_mapping.write_text("just a string\n", encoding="utf-8")
    with pytest.raises(SettingsError):
        load_settings(not_mapping)


def test_load_settings_default_mode_none(tmp_path) -> None:
    target = tmp_path / "s.yaml"
    target.write_text(
        "default_mode:\npermissions: {allow: [], deny: []}\n", encoding="utf-8"
    )
    settings = load_settings(target)
    assert settings.default_mode == ""


def test_parse_args_variants() -> None:
    from endless_code.permission.settings import _parse_args

    assert _parse_args({"path": "x"}) == {"path": "x"}
    assert _parse_args(123) is None
    assert _parse_args("not json") is None
    assert _parse_args("123") is None
    assert _parse_args('{"path": "x"}') == {"path": "x"}


def test_extract_target_unparseable_input() -> None:
    call = ToolCall(id="c", name="read_file", input="not json")
    assert extract_target(call) == ("", False, False)


def test_check_function_delegates(tmp_path) -> None:
    from endless_code.permission import check

    engine, _ = new_engine(str(tmp_path))
    call = _call("read_file", path="a.txt")
    assert check(engine, Mode.DEFAULT, call, True) == engine.check(
        Mode.DEFAULT, call, True
    )
