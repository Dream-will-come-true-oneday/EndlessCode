"""CLI 入口 —— 加载配置、打印 banner、启动 TUI。"""

import os
import sys
from pathlib import Path

from endless_code import __version__
from endless_code.config import ConfigError, load
from endless_code.permission import new_engine
from endless_code.prompt import render_banner
from endless_code.tool import new_default_registry
from endless_code.tui import EndlessCodeApp


def main() -> None:
    """应用主入口。"""
    try:
        cfg = load(None)
    except ConfigError as e:
        print(f"配置错误: {e}", file=sys.stderr)
        sys.exit(1)

    print(render_banner(__version__, os.getcwd()))

    registry = new_default_registry()
    root = str(Path.cwd().resolve())
    engine, engine_err = new_engine(root)
    if engine_err is not None:
        print(f"权限引擎降级: {engine_err}", file=sys.stderr)
    app = EndlessCodeApp(cfg.providers, registry, version=__version__, engine=engine)
    app.run()
