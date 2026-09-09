"""命令系统：注册、解析、分发与内置命令。"""

from endless_code.command.builtin import register_builtin_commands
from endless_code.command.dispatcher import Dispatcher
from endless_code.command.host import CommandHost
from endless_code.command.parser import parse_command
from endless_code.command.registry import Registry
from endless_code.command.types import (
    CommandError,
    CommandKind,
    CommandSpec,
    ModelOption,
    ParsedCommand,
    SessionInfo,
    StyleOption,
    SwitchResult,
)

__all__ = [
    "CommandError",
    "CommandHost",
    "CommandKind",
    "CommandSpec",
    "Dispatcher",
    "ModelOption",
    "ParsedCommand",
    "Registry",
    "SessionInfo",
    "StyleOption",
    "SwitchResult",
    "parse_command",
    "register_builtin_commands",
]
