"""命令分流与分发。"""

from endless_code.command.host import CommandHost
from endless_code.command.parser import parse_command
from endless_code.command.registry import Registry


class Dispatcher:
    """把用户输入分流为命令或对话，并执行命令。"""

    def __init__(self, registry: Registry, host: CommandHost) -> None:
        self._registry = registry
        self._host = host

    def try_dispatch(self, text: str) -> bool:
        """命令分流入口；返回是否已作为命令消费输入。"""
        parsed = parse_command(text)
        if parsed is None:
            return False
        if not parsed.name:
            self._host.show_error("非法命令输入：请输入 /命令名，输入 /help 查看帮助。")
            return True
        spec = self._registry.lookup(parsed.name)
        if spec is None:
            self._host.show_notice(
                f"未知命令：{parsed.name}。输入 /help 查看可用命令。"
            )
            return True
        try:
            spec.handler(self._host, parsed.args)
        except Exception as exc:  # noqa: BLE001
            self._host.show_error(f"命令执行失败: {type(exc).__name__}: {exc}")
        return True

    def complete(self, text: str) -> tuple[str, list[str]]:
        """Tab 补全：返回 (补全后的输入框文本, 候选列表)。"""
        parsed = parse_command(text)
        if parsed is None or parsed.args:
            return text, []
        candidates = self._registry.completions(parsed.name)
        if len(candidates) == 1:
            return candidates[0] + " ", []
        if len(candidates) > 1:
            return text, candidates
        return text, []
