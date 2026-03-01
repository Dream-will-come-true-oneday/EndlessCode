"""CLI 入口 —— 加载配置、打印 banner、启动 TUI。"""

import os
import sys

from endless_code import __version__
from endless_code.config import ConfigError, load
from endless_code.prompt import render_banner
from endless_code.tui import EndlessCodeApp


def main() -> None:
    """应用主入口。"""
    # 加载配置（自动查找配置文件）
    try:
        cfg = load(None)
    except ConfigError as e:
        print(f"配置错误: {e}", file=sys.stderr)
        sys.exit(1)

    # 打印启动横幅
    print(render_banner(__version__, os.getcwd()))

    # 启动 TUI
    app = EndlessCodeApp(cfg.providers)
    app.run()
