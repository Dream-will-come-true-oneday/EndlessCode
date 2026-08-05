"""内置危险命令黑名单：启发式、非完备、不可配置放开（N1）。"""

import re

_BLACKLIST_PATTERNS = [
    r"rm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+(/|~|\$HOME|/\*)",
    r"dd\s+.*\bof=/dev/",
    r":\s*\(\s*\)\s*\{\s*.*\|.*&\s*\}",
    r"\bmkfs\.",
    r">\s*/dev/(sd|hd|nvme|disk)",
    r"chmod\s+-R\s+0?777\s+(/|~|\$HOME)",
    r"rm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+/\*",
]

_BLACKLIST: list[re.Pattern] = [re.compile(p) for p in _BLACKLIST_PATTERNS]


def hits_blacklist(command: str) -> bool:
    """命令串命中任一高危模式即返回 True。"""
    return any(pattern.search(command) for pattern in _BLACKLIST)
