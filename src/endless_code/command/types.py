"""命令系统核心类型：元数据、解析结果与会话信息。"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endless_code.command.host import CommandHost


class CommandKind(Enum):
    """命令执行模式。"""

    LOCAL = "local"
    UI = "ui"
    AI = "ai"


@dataclass(frozen=True)
class CommandSpec:
    """一条命令的完整元数据。"""

    name: str
    description: str
    usage: str
    kind: CommandKind
    handler: Callable[["CommandHost", str], None]
    aliases: tuple[str, ...] = ()
    arg_hint: str = ""
    hidden: bool = False


@dataclass(frozen=True)
class ParsedCommand:
    """解析结果：小写命令名与第一个空格后的参数原文。"""

    name: str
    args: str


@dataclass(frozen=True)
class ModelOption:
    """一个可选模型：编号、provider 名、模型标识与是否当前使用。"""

    index: int
    name: str
    model: str
    current: bool


@dataclass(frozen=True)
class StyleOption:
    """一个可选输出样式：标识、展示名、一句话说明与是否当前使用。"""

    name: str
    label: str
    description: str
    current: bool


@dataclass(frozen=True)
class SwitchResult:
    """切换结果：是否成功、成功摘要或失败/拒绝原因，及成功时的目标。"""

    ok: bool
    message: str
    name: str = ""
    model: str = ""


@dataclass(frozen=True)
class SessionInfo:
    """综合状态命令所需快照。"""

    version: str
    provider: str
    model: str
    mode: str
    tokens_in: int
    tokens_out: int
    session_id: str
    message_count: int
    output_style: str = "default"
    context_window: int = 0
    usable_window: int = 0
    auto_compact_threshold: int = 0
    degraded: bool = False
    summary_revision: int = 0
    calibrated_window: int = 0


class CommandError(Exception):
    """命令系统错误基类（注册冲突等）。"""
