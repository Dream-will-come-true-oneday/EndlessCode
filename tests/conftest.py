"""全局测试配置：确保 tests 目录可导入，便于复用 harness 子包。"""

import sys
from pathlib import Path

_TESTS_DIR = str(Path(__file__).resolve().parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)
