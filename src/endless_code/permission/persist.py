"""本地永久放行规则写入。"""

import os
from pathlib import Path

import yaml

from endless_code.llm import ToolCall
from endless_code.permission.engine import Engine
from endless_code.permission.rule import Rule
from endless_code.permission.sandbox import eval_symlinks_or_ancestor
from endless_code.permission.settings import (
    extract_target,
    friendly_name,
    load_settings,
)

_KNOWN_FRIENDLY = {"Bash", "Read", "Write", "Edit", "Glob", "Grep"}


def _escape_glob(value: str) -> str:
    """转义 glob 元字符，使自动生成的规则保持精确匹配。"""
    chars: list[str] = []
    for ch in value:
        if ch == "\\":
            chars.append("\\\\")
        elif ch in "*?[]":
            chars.append("\\" + ch)
        else:
            chars.append(ch)
    return "".join(chars)


def rule_for(engine: Engine, call: ToolCall) -> tuple[Rule, str, bool]:
    """为一次具体调用生成精确 allow 规则（Rule + YAML 串）。"""
    target, is_file, ok = extract_target(call)
    friendly = friendly_name(call.name)
    if not ok or friendly not in _KNOWN_FRIENDLY:
        return Rule("", "", False), "", False

    if is_file:
        abs_target = (
            target if os.path.isabs(target) else os.path.join(engine.root, target)
        )
        resolved = eval_symlinks_or_ancestor(abs_target)
        try:
            relative = os.path.relpath(resolved, engine.root)
        except ValueError:
            return Rule("", "", False), "", False
        pattern = _escape_glob(relative.replace(os.sep, "/"))
    else:
        pattern = _escape_glob(target)

    rule = Rule(tool=friendly, pattern=pattern, allow=True)
    return rule, f"{friendly}({pattern})", True


def persist_local_allow(engine: Engine, call: ToolCall) -> None:
    """把精确 allow 规则写入本地层配置并同步内存。"""
    rule, rule_text, ok = rule_for(engine, call)
    if not ok:
        raise ValueError(f"无法为工具 {call.name} 生成精确放行规则")

    settings = load_settings(engine.local_path)
    if rule_text not in settings.permissions.allow:
        settings.permissions.allow.append(rule_text)

    path = Path(engine.local_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {}
    if settings.default_mode:
        payload["default_mode"] = settings.default_mode
    payload["permissions"] = {
        "allow": settings.permissions.allow,
        "deny": settings.permissions.deny,
    }
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, allow_unicode=True, sort_keys=False)

    if rule not in engine.local.allow:
        engine.local.allow.append(rule)
