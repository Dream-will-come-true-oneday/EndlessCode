"""权限配置加载、友好名映射与工具参数提取。"""

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from endless_code.llm import ToolCall
from endless_code.permission import Category
from endless_code.permission.rule import RuleSet, parse_rule


class SettingsError(Exception):
    """权限配置文件格式错误。"""


@dataclass
class PermissionsBlock:
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)


@dataclass
class Settings:
    default_mode: str = ""
    permissions: PermissionsBlock = field(default_factory=PermissionsBlock)


def load_settings(path: str | Path) -> Settings:
    """加载单个权限配置；文件缺失视为空，格式非法抛 SettingsError。"""
    file_path = Path(path)
    if not file_path.exists():
        return Settings()
    try:
        raw = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as exc:
        raise SettingsError(f"权限配置文件 YAML 解析失败: {exc}") from exc

    if raw is None:
        return Settings()
    if not isinstance(raw, dict):
        raise SettingsError("权限配置根节点必须是映射")

    default_mode = raw.get("default_mode", "")
    if default_mode is None:
        default_mode = ""
    if not isinstance(default_mode, str):
        raise SettingsError("default_mode 必须是字符串")

    permissions = raw.get("permissions") or {}
    if not isinstance(permissions, dict):
        raise SettingsError("permissions 必须是映射")
    allow = permissions.get("allow") or []
    deny = permissions.get("deny") or []
    if not isinstance(allow, list) or not isinstance(deny, list):
        raise SettingsError("permissions.allow/deny 必须是列表")

    return Settings(
        default_mode=default_mode,
        permissions=PermissionsBlock(
            allow=[str(item) for item in allow if isinstance(item, str)],
            deny=[str(item) for item in deny if isinstance(item, str)],
        ),
    )


def to_rule_set(settings: Settings) -> RuleSet:
    """把允许/拒绝列表解析为 RuleSet，非法条目跳过（N5）。"""
    allow_rules = []
    for item in settings.permissions.allow:
        rule, ok = parse_rule(item, allow=True)
        if ok:
            allow_rules.append(rule)
    deny_rules = []
    for item in settings.permissions.deny:
        rule, ok = parse_rule(item, allow=False)
        if ok:
            deny_rules.append(rule)
    return RuleSet(allow=allow_rules, deny=deny_rules)


_FRIENDLY_NAMES = {
    "bash": "Bash",
    "read_file": "Read",
    "write_file": "Write",
    "edit_file": "Edit",
    "glob": "Glob",
    "grep": "Grep",
}


def friendly_name(internal: str) -> str:
    """内部工具名 → 面向用户的友好名；未知原样返回。"""
    return _FRIENDLY_NAMES.get(internal, internal)


def categorize(internal: str, read_only: bool) -> Category:
    """工具类别；只读标志优先，未知/未注册工具按命令执行类最严处理。"""
    if read_only:
        return Category.READ
    if internal in ("write_file", "edit_file"):
        return Category.WRITE
    return Category.EXEC


def _parse_args(raw: object) -> dict | None:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        data = json.loads(raw.strip() or "{}")
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def extract_target(call: ToolCall) -> tuple[str, bool, bool]:
    """提取规则/沙箱使用的目标；返回 (target, is_file, ok)。"""
    data = _parse_args(call.input)
    if data is None:
        return "", False, False

    if call.name in ("read_file", "write_file", "edit_file"):
        path = data.get("path")
        if not isinstance(path, str) or not path.strip():
            return "", True, False
        return path, True, True

    if call.name in ("glob", "grep"):
        path = data.get("path")
        target = path if isinstance(path, str) and path.strip() else "."
        return target, True, True

    if call.name == "bash":
        command = data.get("command")
        if not isinstance(command, str):
            return "", False, False
        return command, False, True

    return "", False, False
