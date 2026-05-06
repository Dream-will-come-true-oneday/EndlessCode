"""规则解析与 glob 匹配。"""

import re
from dataclasses import dataclass, field

from endless_code.permission import Decision


@dataclass
class Rule:
    """一条 allow/deny 规则。"""

    tool: str
    pattern: str
    allow: bool = True


@dataclass
class RuleSet:
    """同一层的 allow 与 deny 规则集合。"""

    allow: list[Rule] = field(default_factory=list)
    deny: list[Rule] = field(default_factory=list)

    def match(self, friendly: str, target: str) -> tuple[Decision, bool]:
        """先 deny 后 allow；返回 (裁决, 是否命中)。"""
        path_like = friendly != "Bash"
        for rule in self.deny:
            if rule.tool == friendly and match_pattern(
                rule.pattern, target, path_like=path_like
            ):
                return Decision.DENY, True
        for rule in self.allow:
            if rule.tool == friendly and match_pattern(
                rule.pattern, target, path_like=path_like
            ):
                return Decision.ALLOW, True
        return Decision.ALLOW, False


_TOOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_rule(s: str, *, allow: bool = True) -> tuple[Rule, bool]:
    """解析 ``Tool(pattern)`` 或 ``Tool``；非法返回 (空 Rule, False)。"""
    text = s.strip()
    if not text:
        return Rule("", "", False), False

    if "(" in text:
        if not text.endswith(")"):
            return Rule("", "", False), False
        open_index = text.index("(")
        tool = text[:open_index].strip()
        pattern = text[open_index + 1 : -1]
    else:
        tool = text
        pattern = ""

    if not _TOOL_RE.fullmatch(tool):
        return Rule("", "", False), False
    return Rule(tool=tool, pattern=pattern, allow=allow), True


def _segment_regex(segment: str) -> str:
    """把单个路径段编译为 regex，支持 *、? 与反斜杠转义。"""
    chars: list[str] = []
    i = 0
    while i < len(segment):
        ch = segment[i]
        if ch == "\\" and i + 1 < len(segment):
            chars.append(re.escape(segment[i + 1]))
            i += 2
        elif ch == "*":
            chars.append(".*")
            i += 1
        elif ch == "?":
            chars.append(".")
            i += 1
        else:
            chars.append(re.escape(ch))
            i += 1
    return "".join(chars)


def _match_segments(pattern_parts: list[str], target_parts: list[str]) -> bool:
    if not pattern_parts:
        return not target_parts

    part = pattern_parts[0]
    if part == "**":
        for i in range(len(target_parts) + 1):
            if _match_segments(pattern_parts[1:], target_parts[i:]):
                return True
        return False

    if not target_parts:
        return False
    return bool(
        re.fullmatch(_segment_regex(part), target_parts[0])
    ) and _match_segments(pattern_parts[1:], target_parts[1:])


def _command_regex(pattern: str) -> str:
    """命令串 glob：* 匹配任意字符，** 等价 *。"""
    chars: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            chars.append(re.escape(pattern[i + 1]))
            i += 2
        elif ch == "*":
            chars.append(".*")
            i += 1
        elif ch == "?":
            chars.append(".")
            i += 1
        else:
            chars.append(re.escape(ch))
            i += 1
    return "".join(chars)


def match_pattern(pattern: str, target: str, *, path_like: bool = True) -> bool:
    """glob 匹配；pattern 为空表示匹配全部。"""
    if pattern == "":
        return True
    if not path_like:
        return re.fullmatch(_command_regex(pattern), target) is not None
    return _match_segments(pattern.split("/"), target.split("/"))
