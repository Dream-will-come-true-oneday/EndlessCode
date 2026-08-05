"""会话层：进程内维护单会话多轮历史。"""

import copy
import threading

from endless_code.llm import ROLE_ASSISTANT, ROLE_TOOL, Message, ToolCall, ToolResult


class Conversation:
    """进程内对话历史管理。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._messages: list[Message] = []

    def add_user(self, text: str) -> None:
        with self._lock:
            self._messages.append(Message(role="user", content=text))

    def add_assistant(self, text: str) -> None:
        with self._lock:
            self._messages.append(Message(role=ROLE_ASSISTANT, content=text))

    def add_assistant_with_tool_calls(self, text: str, calls: list[ToolCall]) -> None:
        with self._lock:
            self._messages.append(
                Message(role=ROLE_ASSISTANT, content=text, tool_calls=list(calls))
            )

    def add_tool_results(self, results: list[ToolResult]) -> None:
        with self._lock:
            self._messages.append(Message(role=ROLE_TOOL, tool_results=list(results)))

    def messages(self) -> list[Message]:
        with self._lock:
            return copy.deepcopy(self._messages)

    def replace_history(self, messages: list[Message] | None) -> None:
        """用独立副本原子替换整段会话历史。"""
        with self._lock:
            self._messages = copy.deepcopy(messages or [])

    def length(self) -> int:
        with self._lock:
            return len(self._messages)

    def last_role(self) -> str:
        """返回最后一条消息的角色；空历史返回空字符串。"""
        with self._lock:
            return self._messages[-1].role if self._messages else ""
