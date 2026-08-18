"""TUI 内置命令注册与分发。"""

from collections.abc import Callable
from typing import Protocol


class CommandTarget(Protocol):
    def _command_exit(self) -> None: ...
    def _command_plan(self) -> None: ...
    def _command_do(self) -> None: ...
    def _command_compact(self) -> None: ...
    def _command_resume(self) -> None: ...
    def _command_memory(self, args: list[str]) -> None: ...
    def _write_notice(self, text: str) -> None: ...


BUILTIN_COMMANDS: dict[str, Callable[[CommandTarget, list[str]], None]] = {
    "/exit": lambda app, _args: app._command_exit(),
    "/quit": lambda app, _args: app._command_exit(),
    "/plan": lambda app, _args: app._command_plan(),
    "/do": lambda app, _args: app._command_do(),
    "/compact": lambda app, _args: app._command_compact(),
    "/resume": lambda app, _args: app._command_resume(),
    "/memory": lambda app, args: app._command_memory(args),
}


def dispatch_command(target: CommandTarget, text: str) -> bool:
    """分发斜杠命令，返回是否已经消费输入。"""
    if not text.startswith("/"):
        return False
    command, *args = text.split()
    handler = BUILTIN_COMMANDS.get(command)
    if handler is None:
        commands = ", ".join(BUILTIN_COMMANDS)
        target._write_notice(f"未知命令：{text}。可用命令：{commands}")
        return True
    if args and command != "/memory":
        target._write_notice(f"命令 {command} 不接受参数。")
        return True
    handler(target, args)
    return True
