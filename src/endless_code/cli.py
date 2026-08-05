"""CLI 入口 —— 加载配置、打印 banner、启动 MCP 与 TUI。"""

import asyncio
import os
import sys
from pathlib import Path

from endless_code import __version__
from endless_code.config import ConfigError, load
from endless_code.mcp import load_config, new_manager
from endless_code.permission import new_engine
from endless_code.prompt import render_banner
from endless_code.tool import new_default_registry
from endless_code.tui import EndlessCodeApp


def _print_config_error(message: str) -> None:
    print(f"配置错误: {message}", file=sys.stderr)


async def _amain() -> int:
    try:
        cfg = load(None)
    except ConfigError as e:
        _print_config_error(str(e))
        return 1

    print(render_banner(__version__, os.getcwd()))

    registry = new_default_registry()
    root = str(Path.cwd().resolve())
    engine, engine_err = new_engine(root)
    if engine_err is not None:
        print(f"权限引擎降级: {engine_err}", file=sys.stderr)

    mcp_cfg = load_config(root)
    mgr = await new_manager(mcp_cfg, version=__version__)
    try:
        for t in mgr.tools():
            registry.register(t)
        app = EndlessCodeApp(
            cfg.providers,
            registry=registry,
            version=__version__,
            engine=engine,
        )
        await app.run_async()
    finally:
        await mgr.close()
    return 0


def main() -> None:
    """应用主入口。"""
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
