"""权限引擎、规则、沙箱、配置与本地放行测试。"""

import json

import pytest

from endless_code.llm import ToolCall
from endless_code.permission import (
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
from endless_code.permission.settings import extract_target, friendly_name


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
