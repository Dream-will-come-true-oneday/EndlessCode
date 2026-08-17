"""CLI 入口 —— 加载配置、打印 banner、启动 MCP 与 TUI。"""

import asyncio
import inspect
import os
import sys
from pathlib import Path

from endless_code import __version__
from endless_code.config import ConfigError, load
from endless_code.instructions import Loader
from endless_code.mcp import load_config, new_manager
from endless_code.memory import Manager
from endless_code.permission import new_engine
from endless_code.prompt import render_banner
from endless_code.session import clean_expired
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
    instruction_text = Loader(root).load()
    memory_manager = Manager(
        str(Path(root) / ".endless-code" / "memory"),
        str(Path.home() / ".config" / "endless-code" / "memory"),
    )
    memory_manager.load_index()
    cleanup_task = asyncio.create_task(
        asyncio.to_thread(clean_expired, str(Path(root) / ".endless-code" / "sessions"))
    )
    engine, engine_err = new_engine(root)
    if engine_err is not None:
        print(f"权限引擎降级: {engine_err}", file=sys.stderr)

    mcp_cfg = load_config(root)
    mgr = await new_manager(mcp_cfg, version=__version__)
    try:
        for t in mgr.tools():
            registry.register(t, deferred=True)
        app_kwargs = {
            "registry": registry,
            "version": __version__,
            "engine": engine,
        }
        if "instruction_text" in inspect.signature(EndlessCodeApp).parameters:
            app_kwargs.update(
                instruction_text=instruction_text,
                memory_manager=memory_manager,
            )
        app = EndlessCodeApp(cfg.providers, **app_kwargs)
        await app.run_async()
    finally:
        await asyncio.gather(cleanup_task, return_exceptions=True)
        await mgr.close()
    return 0


def main() -> None:
    """应用主入口。"""
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
