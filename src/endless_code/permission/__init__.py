"""权限系统：四档模式、三级规则与五层防御。"""

from enum import IntEnum


class Mode(IntEnum):
    """权限模式。"""

    DEFAULT = 0
    ACCEPT_EDITS = 1
    PLAN = 2
    BYPASS = 3

    def __str__(self) -> str:
        return _MODE_NAMES[self]


_MODE_NAMES = {
    Mode.DEFAULT: "default",
    Mode.ACCEPT_EDITS: "acceptEdits",
    Mode.PLAN: "plan",
    Mode.BYPASS: "bypassPermissions",
}


def parse_mode(s: object) -> tuple[Mode, bool]:
    """大小写不敏感识别四档模式名，未知返回 (default, False)。"""
    if not isinstance(s, str):
        return Mode.DEFAULT, False
    key = s.strip().lower()
    table = {
        "default": Mode.DEFAULT,
        "acceptedits": Mode.ACCEPT_EDITS,
        "plan": Mode.PLAN,
        "bypasspermissions": Mode.BYPASS,
    }
    mode = table.get(key)
    if mode is None:
        return Mode.DEFAULT, False
    return mode, True


class Decision(IntEnum):
    """一次工具调用的裁决。"""

    ALLOW = 0
    DENY = 1
    ASK = 2


class Category(IntEnum):
    """工具类别：只读 / 文件写 / 命令执行。"""

    READ = 0
    WRITE = 1
    EXEC = 2


class Outcome(IntEnum):
    """人在回路三选一结果。"""

    DENY_ONCE = 0
    ALLOW_ONCE = 1
    ALLOW_FOREVER = 2


class ApprovalError(Exception):
    """人在回路或规则写入异常。"""


from .blacklist import hits_blacklist
from .engine import Engine, check, mode_fallback, new_engine
from .persist import persist_local_allow, rule_for
from .rule import Rule, RuleSet, match_pattern, parse_rule
from .sandbox import eval_symlinks_or_ancestor, resolve_root, sandbox_ok
from .settings import (
    PermissionsBlock,
    Settings,
    SettingsError,
    categorize,
    extract_target,
    friendly_name,
    load_settings,
    to_rule_set,
)

__all__ = [
    "ApprovalError",
    "Category",
    "Decision",
    "Engine",
    "Mode",
    "Outcome",
    "PermissionsBlock",
    "Rule",
    "RuleSet",
    "Settings",
    "SettingsError",
    "categorize",
    "check",
    "eval_symlinks_or_ancestor",
    "extract_target",
    "friendly_name",
    "hits_blacklist",
    "load_settings",
    "match_pattern",
    "mode_fallback",
    "new_engine",
    "parse_mode",
    "parse_rule",
    "persist_local_allow",
    "resolve_root",
    "rule_for",
    "sandbox_ok",
    "to_rule_set",
]
