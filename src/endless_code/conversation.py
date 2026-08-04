"""会话层：进程内维护单会话多轮历史。"""

from endless_code.llm import ROLE_ASSISTANT, ROLE_TOOL, Message, ToolCall, ToolResult


class Conversation:
    """进程内对话历史管理。"""

    def __init__(self) -> None:
        self._messages: list[Message] = []

    def add_user(self, text: str) -> None:
        self._messages.append(Message(role="user", content=text))

    def add_assistant(self, text: str) -> None:
        self._messages.append(Message(role=ROLE_ASSISTANT, content=text))

    def add_assistant_with_tool_calls(self, text: str, calls: list[ToolCall]) -> None:
        self._messages.append(
            Message(role=ROLE_ASSISTANT, content=text, tool_calls=list(calls))
        )

    def add_tool_results(self, results: list[ToolResult]) -> None:
        self._messages.append(Message(role=ROLE_TOOL, tool_results=list(results)))

    def messages(self) -> list[Message]:
        return list(self._messages)

    def last_role(self) -> str:
        """返回最后一条消息的角色；空历史返回空字符串。"""
        return self._messages[-1].role if self._messages else ""
