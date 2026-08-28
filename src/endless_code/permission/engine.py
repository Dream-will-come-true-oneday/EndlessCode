"""权限引擎：前四层判定与配置加载。"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from endless_code.llm import ToolCall
from endless_code.permission import Category, Decision, Mode, RiskLevel, parse_mode
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
class PermissionExplanation:
    """一次工具调用的确定性权限解释。"""

    decision: Decision
    category: Category
    risk_level: RiskLevel
    tool: str
    target: str
    mode: Mode
    rule_source: str
    reason: str
    requires_approval: bool
    read_only: bool

    @property
    def impact(self) -> str:
        """返回面向用户的影响说明。"""
        return {
            RiskLevel.LOW: "只读取信息，不修改项目文件。",
            RiskLevel.MEDIUM: "可能修改项目文件，请确认变更目标。",
            RiskLevel.HIGH: "可能执行命令或产生外部副作用，请谨慎确认。",
            RiskLevel.CRITICAL: "检测到高危或越界操作，系统将阻止执行。",
        }[self.risk_level]

    @property
    def risk(self) -> RiskLevel:
        """兼容简写字段名，便于审计/界面调用。"""
        return self.risk_level

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly explanation snapshot."""
        return {
            "decision": self.decision.name.lower(),
            "category": self.category.name.lower(),
            "risk_level": self.risk_level.value,
            "tool": self.tool,
            "target": self.target,
            "mode": str(self.mode),
            "rule_source": self.rule_source,
            "reason": self.reason,
            "requires_approval": self.requires_approval,
            "read_only": self.read_only,
        }

    as_dict = to_dict


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
        """兼容接口：返回 ``(Decision, reason)``。"""
        explanation = self.explain(mode, call, read_only)
        return explanation.decision, explanation.reason

    def explain(
        self, mode: Mode, call: ToolCall, read_only: bool
    ) -> PermissionExplanation:
        """生成前四层判定及稳定风险解释。"""
        category = categorize(call.name, read_only)
        friendly = friendly_name(call.name)
        target, is_file, ok = extract_target(call)
        shown_target = target or "(未提供目标)"

        def result(
            decision: Decision,
            reason: str,
            rule_source: str = "default",
            risk: RiskLevel | None = None,
        ) -> PermissionExplanation:
            return PermissionExplanation(
                decision=decision,
                category=category,
                risk_level=risk or self._risk_for(category, read_only),
                tool=call.name,
                target=shown_target,
                mode=mode,
                rule_source=rule_source,
                reason=reason,
                requires_approval=decision is Decision.ASK,
                read_only=read_only,
            )

        if category is Category.EXEC and target and hits_blacklist(target):
            return result(
                Decision.DENY,
                f"命中危险命令黑名单：{target}",
                "blacklist",
                RiskLevel.CRITICAL,
            )

        if is_file:
            if not ok:
                return result(
                    Decision.DENY,
                    "无法解析文件路径参数，安全拒绝",
                    "sandbox",
                    RiskLevel.CRITICAL,
                )
            if not sandbox_ok(self, target):
                return result(
                    Decision.DENY,
                    f"路径在项目目录之外：{target}",
                    "sandbox",
                    RiskLevel.CRITICAL,
                )
            target = self._rule_target(target)
            shown_target = target

        for source, ruleset in (
            ("local", self.local),
            ("project", self.project),
            ("user", self.user),
        ):
            decision, hit = ruleset.match(friendly, target)
            if hit:
                label = "deny" if decision is Decision.DENY else "allow"
                return result(
                    decision,
                    f"匹配{label}规则：{friendly}",
                    source,
                )

        # 未命中规则时，非只读的未知工具按最严处理（Ask），bypass 除外。
        if not read_only and friendly == call.name:
            if mode is Mode.BYPASS:
                return result(Decision.ALLOW, "", "default")
            return result(Decision.ASK, "未知工具需确认", "default")

        decision = mode_fallback(mode, category)
        if decision is Decision.ASK:
            return result(
                decision,
                f"{mode} 模式下 {category.name} 类操作需确认",
                "mode",
            )
        return result(decision, "", "mode")

    @staticmethod
    def _risk_for(category: Category, read_only: bool) -> RiskLevel:
        if read_only or category is Category.READ:
            return RiskLevel.LOW
        if category is Category.WRITE:
            return RiskLevel.MEDIUM
        return RiskLevel.HIGH

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
