"""斜杠命令输入解析。"""

from endless_code.command.types import ParsedCommand


def parse_command(text: str) -> ParsedCommand | None:
    """非 ``/`` 开头返回 None；否则按第一个空格切分。

    命令名转小写做到大小写不敏感；仅输入 ``/`` 时返回空命令名，
    由分发器判定为非法输入。
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    head, _, tail = stripped.partition(" ")
    name = "" if head == "/" else head.lower()
    return ParsedCommand(name=name, args=tail.strip())
