"""CLI 装配集成测试：验证 MCP 配置加载 → 连接 → 注册进 registry → 关闭。"""

import sys
from pathlib import Path

import pytest

import endless_code.cli as cli_mod


@pytest.mark.asyncio
async def test_amain_registers_mcp_tools_and_closes(tmp_path, monkeypatch) -> None:
    """临时项目里配好 provider + MCP server，_amain 应注册 MCP 工具并干净关闭。"""
    # 临时项目根：放 provider 配置
    config_dir = tmp_path / ".endless-code"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "providers:\n"
        "  - name: fake\n"
        "    protocol: deepseek\n"
        "    model: deepseek-chat\n"
        "    api_key: fake_key\n"
        "    base_url: https://example.com\n",
        encoding="utf-8",
    )
    # MCP 配置指向本地 demo server
    demo_server = Path(__file__).parent / "_demo_mcp_server.py"
    (config_dir / "mcp.yaml").write_text(
        "mcp_servers:\n"
        "  demo:\n"
        "    type: stdio\n"
        f"    command: {sys.executable!r}\n"
        f"    args: ['{demo_server}']\n",
        encoding="utf-8",
    )

    # 记录被构造的 app 的 registry
    captured = {}

    class FakeApp:
        def __init__(self, providers, registry=None, version="", engine=None):
            captured["registry"] = registry
            captured["providers"] = providers

        async def run_async(self):
            # 验证 registry 里已有 MCP 工具
            names = [d.name for d in captured["registry"].definitions()]
            assert "mcp__demo__add" in names
            assert "mcp__demo__echo" in names

    monkeypatch.setattr(cli_mod, "EndlessCodeApp", FakeApp)
    # 限制连接超时，避免卡住
    monkeypatch.setattr("endless_code.mcp.manager.connect_timeout", 5.0)
    monkeypatch.setattr("endless_code.mcp.manager.close_timeout", 3.0)
    monkeypatch.chdir(tmp_path)

    code = await cli_mod._amain()
    assert code == 0
    # _amain 内部 new_manager 已被 close（finally），tools 已清理
    assert captured["registry"] is not None
