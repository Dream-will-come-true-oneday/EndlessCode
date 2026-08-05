"""MCP 配置加载测试：两层合并、变量展开、字段校验与降级。"""

from pathlib import Path

import pytest

from endless_code.mcp.config import _apply_expansion, load_config


def _write_project_config(root: Path, content: str) -> Path:
    config_dir = root / ".endless-code"
    config_dir.mkdir(exist_ok=True)
    path = config_dir / "mcp.yaml"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def isolated_home(tmp_path, monkeypatch) -> Path:
    """把用户级配置目录隔离到临时目录。"""
    home = tmp_path / "home"
    config_dir = home / ".config" / "endless-code"
    config_dir.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


def test_no_config_files(tmp_path) -> None:
    cfg = load_config(str(tmp_path))
    assert cfg.servers == {}


def test_user_only(isolated_home: Path) -> None:
    user_cfg = isolated_home / ".config" / "endless-code" / "mcp.yaml"
    user_cfg.parent.mkdir(parents=True, exist_ok=True)
    user_cfg.write_text(
        "mcp_servers:\n"
        "  s1:\n"
        "    type: stdio\n"
        "    command: npx\n"
        "    args: ['-y', 'server']\n",
        encoding="utf-8",
    )
    cfg = load_config(str(isolated_home / "proj"))
    assert "s1" in cfg.servers
    assert cfg.servers["s1"].command == "npx"
    assert cfg.servers["s1"].args == ["-y", "server"]


def test_project_only(tmp_path) -> None:
    _write_project_config(
        tmp_path,
        "mcp_servers:\n"
        "  s1:\n"
        "    type: stdio\n"
        "    command: python\n"
        "    args: ['-m', 'server']\n",
    )
    cfg = load_config(str(tmp_path))
    assert cfg.servers["s1"].command == "python"


def test_project_overrides_user(isolated_home: Path) -> None:
    user_cfg = isolated_home / ".config" / "endless-code" / "mcp.yaml"
    user_cfg.parent.mkdir(parents=True, exist_ok=True)
    user_cfg.write_text(
        "mcp_servers:\n"
        "  s1:\n"
        "    type: stdio\n"
        "    command: npx\n"
        "  s2:\n"
        "    type: http\n"
        "    url: 'https://user.example.com/mcp'\n",
        encoding="utf-8",
    )
    # 确保项目目录存在
    (isolated_home / "proj").mkdir(parents=True, exist_ok=True)
    _write_project_config(
        isolated_home / "proj",
        "mcp_servers:\n"
        "  s1:\n"
        "    type: stdio\n"
        "    command: python\n"
        "  s3:\n"
        "    type: http\n"
        "    url: 'https://proj.example.com/mcp'\n",
    )
    cfg = load_config(str(isolated_home / "proj"))
    # s1 被项目级完整覆盖
    assert cfg.servers["s1"].command == "python"
    # s2 仅用户级
    assert cfg.servers["s2"].url == "https://user.example.com/mcp"
    # s3 仅项目级
    assert cfg.servers["s3"].url == "https://proj.example.com/mcp"


def test_invalid_yaml_skips_layer(tmp_path, capsys) -> None:
    bad_dir = tmp_path / ".endless-code"
    bad_dir.mkdir()
    (bad_dir / "mcp.yaml").write_text("mcp_servers: [unclosed", encoding="utf-8")
    cfg = load_config(str(tmp_path))
    assert cfg.servers == {}
    assert "load" in capsys.readouterr().err


def test_invalid_yaml_other_layer_still_loads(tmp_path, capsys) -> None:
    # 项目级非法，但用户级正常 —— 用户级独立隔离测一下（这里用临时 home）
    home = tmp_path / "home"
    user_dir = home / ".config" / "endless-code"
    user_dir.mkdir(parents=True)
    (user_dir / "mcp.yaml").write_text(
        "mcp_servers:\n  u1:\n    type: stdio\n    command: ok\n",
        encoding="utf-8",
    )
    import endless_code.mcp.config as config_mod

    original_home = Path.home
    try:
        config_mod.Path.home = lambda: home
        bad_dir = tmp_path / "proj" / ".endless-code"
        bad_dir.mkdir(parents=True)
        (bad_dir / "mcp.yaml").write_text("mcp_servers: [unclosed", encoding="utf-8")
        cfg = load_config(str(tmp_path / "proj"))
        assert "u1" in cfg.servers
        assert "load" in capsys.readouterr().err
    finally:
        config_mod.Path.home = original_home


def test_expand_env_var(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MY_TOKEN", "secret123")
    _write_project_config(
        tmp_path,
        "mcp_servers:\n"
        "  s1:\n"
        "    type: http\n"
        "    url: 'https://example.com/mcp'\n"
        "    headers:\n"
        "      Authorization: 'Bearer ${MY_TOKEN}'\n"
        "    env:\n"
        "      TOKEN: '${MY_TOKEN}'\n",
    )
    cfg = load_config(str(tmp_path))
    srv = cfg.servers["s1"]
    assert srv.headers["Authorization"] == "Bearer secret123"
    assert srv.env["TOKEN"] == "secret123"


def test_undefined_env_var_warns_once(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("MISSING_VAR", raising=False)
    _write_project_config(
        tmp_path,
        "mcp_servers:\n"
        "  s1:\n"
        "    type: http\n"
        "    url: 'https://example.com/mcp'\n"
        "    headers:\n"
        "      X: '${MISSING_VAR}'\n"
        "    env:\n"
        "      A: '${MISSING_VAR}'\n"
        "      B: '${MISSING_VAR}'\n",
    )
    cfg = load_config(str(tmp_path))
    srv = cfg.servers["s1"]
    assert srv.headers["X"] == ""
    assert srv.env["A"] == ""
    assert srv.env["B"] == ""
    err = capsys.readouterr().err
    # 同一变量在同一个 server 只告警一次
    assert err.count("MISSING_VAR") == 1


def test_command_args_not_expanded(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SHOULD_NOT_EXPAND", "boom")
    _write_project_config(
        tmp_path,
        "mcp_servers:\n"
        "  s1:\n"
        "    type: stdio\n"
        "    command: '${SHOULD_NOT_EXPAND}'\n"
        "    args: ['${SHOULD_NOT_EXPAND}']\n",
    )
    cfg = load_config(str(tmp_path))
    srv = cfg.servers["s1"]
    assert srv.command == "${SHOULD_NOT_EXPAND}"
    assert srv.args == ["${SHOULD_NOT_EXPAND}"]


@pytest.mark.parametrize(
    ("config_body", "valid_names"),
    [
        (
            "mcp_servers:\n  no_type:\n    command: x\n",
            [],
        ),
        (
            "mcp_servers:\n  bad_type:\n    type: sse\n    command: x\n",
            [],
        ),
        (
            "mcp_servers:\n  no_cmd:\n    type: stdio\n",
            [],
        ),
        (
            "mcp_servers:\n  no_url:\n    type: http\n",
            [],
        ),
        (
            (
                "mcp_servers:\n"
                "  bad:\n    type: sse\n"
                "  good:\n    type: stdio\n    command: ok\n"
            ),
            ["good"],
        ),
    ],
)
def test_invalid_servers_skipped(
    tmp_path, capsys, config_body: str, valid_names: list[str]
) -> None:
    _write_project_config(tmp_path, config_body)
    cfg = load_config(str(tmp_path))
    assert set(cfg.servers) == set(valid_names)
    assert "skip server" in capsys.readouterr().err


def test_load_config_never_raises(tmp_path, monkeypatch) -> None:
    """缺失文件、非法 YAML、非法 server 全部不抛出。"""
    monkeypatch.delenv("SOME_VAR", raising=False)
    bad_dir = tmp_path / ".endless-code"
    bad_dir.mkdir()
    (bad_dir / "mcp.yaml").write_text("::: not yaml", encoding="utf-8")
    cfg = load_config(str(tmp_path))
    assert cfg.servers == {}


def test_apply_expansion_keeps_literal_when_no_pattern() -> None:
    """没有 ${} 的字符串原样保留。"""
    from endless_code.mcp.config import _RawServer

    srv = _RawServer()
    srv.env["K"] = "plain"
    _apply_expansion("s1", srv)
    assert srv.env["K"] == "plain"


def test_example_config_loads(tmp_path, monkeypatch) -> None:
    """读取 docs/mcp/mcp-servers.example.yaml，断言三个 server 都解析成功。"""
    example = (
        Path(__file__).resolve().parent.parent
        / "docs"
        / "mcp"
        / "mcp-servers.example.yaml"
    )
    if not example.exists():
        pytest.skip("示例配置文件不存在")
    monkeypatch.setenv("GITHUB_TOKEN", "gh_token")
    monkeypatch.setenv("EXAMPLE_TOKEN", "example_token")
    # 放到项目级
    config_dir = tmp_path / ".endless-code"
    config_dir.mkdir()
    (config_dir / "mcp.yaml").write_text(
        example.read_text(encoding="utf-8"), encoding="utf-8"
    )
    cfg = load_config(str(tmp_path))
    assert set(cfg.servers) == {"github", "local-sqlite", "example-http"}
    assert cfg.servers["github"].env["GITHUB_TOKEN"] == "gh_token"
    assert cfg.servers["github"].command == "npx"
    assert cfg.servers["local-sqlite"].command == "python"
    assert cfg.servers["example-http"].url == "https://mcp.example.com/mcp"
    assert (
        cfg.servers["example-http"].headers["Authorization"] == "Bearer example_token"
    )
