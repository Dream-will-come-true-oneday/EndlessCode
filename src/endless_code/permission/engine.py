"""权限引擎：前四层判定与配置加载。"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from endless_code.llm import ToolCall
from endless_code.permission import Category, Decision, Mode, parse_mode
from endless_code.permission.blacklist import _BLACKLIST, hits_blacklist
from endless_code.permission.rule import RuleSet
from endless_code.permission.sandbox import (
    eval_symlinks_or_ancestor,
    resolve_root,
    sandbox_ok,
)
from endless_code.permission.settings import (
    Settings,
    categorize,
    extract_target,
    friendly_name,
    load_settings,
    to_rule_set,
)


@dataclass
class Engine:
    """权限引擎：根目录、黑名单、三级规则与启动模式。"""

    root: str
    blacklist: list[re.Pattern]
    user: RuleSet = field(default_factory=RuleSet)
    project: RuleSet = field(default_factory=RuleSet)
    local: RuleSet = field(default_factory=RuleSet)
    local_path: str = ""
    _start_mode: Mode = Mode.DEFAULT

    def check(
        self, mode: Mode, call: ToolCall, read_only: bool
    ) -> tuple[Decision, str]:
        """前四层判定：黑名单 → 沙箱 → 规则 → 模式兜底。"""
        category = categorize(call.name, read_only)
        friendly = friendly_name(call.name)
        target, is_file, ok = extract_target(call)

        if category is Category.EXEC and target and hits_blacklist(target):
            return Decision.DENY, f"命中危险命令黑名单：{target}"

        if is_file:
            if not ok:
                return Decision.DENY, "无法解析文件路径参数，安全拒绝"
            if not sandbox_ok(self, target):
                return Decision.DENY, f"路径在项目目录之外：{target}"
            target = self._rule_target(target)

        for ruleset in (self.local, self.project, self.user):
            decision, hit = ruleset.match(friendly, target)
            if hit:
                label = "deny" if decision is Decision.DENY else "allow"
                return decision, f"匹配{label}规则：{friendly}"

        # 未命中规则时，非只读的未知工具按最严处理（Ask），bypass 除外。
        if not read_only and friendly == call.name:
            if mode is Mode.BYPASS:
                return Decision.ALLOW, ""
            return Decision.ASK, "未知工具需确认"

        decision = mode_fallback(mode, category)
        if decision is Decision.ASK:
            return decision, f"{mode} 模式下 {category.name} 类操作需确认"
        return decision, ""

    def start_mode(self) -> Mode:
        """返回启动默认模式。"""
        return self._start_mode

    def persist_local_allow(self, call: ToolCall) -> None:
        """持久化一条本地精确放行规则。"""
        from endless_code.permission.persist import persist_local_allow

        persist_local_allow(self, call)

    def _rule_target(self, target: str) -> str:
        """把文件目标规整为项目相对斜杠路径，供规则匹配。"""
        abs_target = (
            target if os.path.isabs(target) else os.path.join(self.root, target)
        )
        resolved = eval_symlinks_or_ancestor(abs_target)
        try:
            relative = os.path.relpath(resolved, self.root)
        except ValueError:
            relative = resolved
        return relative.replace(os.sep, "/")


def mode_fallback(mode: Mode, category: Category) -> Decision:
    """四档模式兜底矩阵；只产 Allow 或 Ask，绝不产 Deny。"""
    if category is Category.READ or mode is Mode.BYPASS:
        return Decision.ALLOW
    if mode is Mode.ACCEPT_EDITS and category is Category.WRITE:
        return Decision.ALLOW
    return Decision.ASK


def _load_safe(path: Path) -> Settings:
    try:
        return load_settings(path)
    except Exception:  # noqa: BLE001
        return Settings()


def _pick_start_mode(local: Settings, project: Settings, user: Settings) -> Mode:
    for settings in (local, project, user):
        if settings.default_mode:
            mode, ok = parse_mode(settings.default_mode)
            if ok:
                return mode
    return Mode.DEFAULT


def new_engine(root: str) -> tuple[Engine, Exception | None]:
    """构造引擎；即使项目根不可解析也返回非 None 安全引擎。"""
    try:
        engine_root = resolve_root(root)
    except Exception as exc:  # noqa: BLE001
        engine_root = str(root)
        err: Exception | None = exc
    else:
        err = None

    local_path = str(Path(engine_root) / ".endless-code" / "settings.local.yaml")
    user_path = Path.home() / ".config" / "endless-code" / "settings.yaml"
    project_path = Path(engine_root) / ".endless-code" / "settings.yaml"

    user_settings = _load_safe(user_path)
    project_settings = _load_safe(project_path)
    local_settings = _load_safe(local_path)

    engine = Engine(
        root=engine_root,
        blacklist=list(_BLACKLIST),
        user=to_rule_set(user_settings),
        project=to_rule_set(project_settings),
        local=to_rule_set(local_settings),
        local_path=local_path,
        _start_mode=_pick_start_mode(local_settings, project_settings, user_settings),
    )
    return engine, err


def check(
    engine: Engine, mode: Mode, call: ToolCall, read_only: bool
) -> tuple[Decision, str]:
    """函数形式的 check，委托给引擎方法。"""
    return engine.check(mode, call, read_only)
