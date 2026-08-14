"""CLI 入口的报错与退出路径测试。"""

import pytest

import endless_code.cli as cli_mod
from endless_code.config import ConfigError


@pytest.mark.asyncio
async def test_amain_returns_1_and_prints_on_config_error(
    monkeypatch, capsys
) -> None:
    def _fail(_path=None):
        raise ConfigError("配置文件未找到")

    monkeypatch.setattr(cli_mod, "load", _fail)
    code = await cli_mod._amain()
    captured = capsys.readouterr()
    assert code == 1
    assert "配置错误" in captured.err


def test_main_raises_system_exit_with_amain_code(monkeypatch) -> None:
    async def _fake_amain():
        return 3

    monkeypatch.setattr(cli_mod, "_amain", _fake_amain)
    with pytest.raises(SystemExit) as exc_info:
        cli_mod.main()
    assert exc_info.value.code == 3
