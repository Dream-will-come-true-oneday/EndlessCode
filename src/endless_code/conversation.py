"""会话层：进程内维护单会话多轮历史。"""

from endless_code.llm import Message


class Conversation:
    """进程内对话历史管理。

    启动时创建新会话；退出时不保存历史。
    """

    def __init__(self) -> None:
        self._messages: list[Message] = []

    def add_user(self, text: str) -> None:
        """追加一条用户消息。"""
        self._messages.append(Message(role="user", content=text))

    def add_assistant(self, text: str) -> None:
        """追加一条 assistant 回复。"""
        self._messages.append(Message(role="assistant", content=text))

    def messages(self) -> list[Message]:
        """返回消息列表的副本。"""
        return list(self._messages)
