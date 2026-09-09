"""上下文摘要请求和结果解析。"""

import json
import re

from endless_code.llm import Message

_SUMMARY_RE = re.compile(r"<summary>\s*(.*?)\s*</summary>", re.DOTALL)

SUMMARY_SECTIONS = (
    "## 1 主要请求和意图",
    "## 2 关键技术概念",
    "## 3 文件和代码段",
    "## 4 错误和修复",
    "## 5 问题解决过程",
    "## 6 所有用户消息原文",
    "## 7 待办任务",
    "## 8 当前工作",
    "## 9 可能的下一步",
)


def serialize_conversation(messages: list[Message]) -> str:
    """确定性序列化协议无关历史，供摘要模型理解。"""
    payload = [
        {
            "role": message.role,
            "content": message.content,
            "tool_calls": [
                {"id": call.id, "name": call.name, "input": call.input}
                for call in message.tool_calls
            ],
            "tool_results": [
                {
                    "tool_call_id": result.tool_call_id,
                    "content": result.content,
                    "is_error": result.is_error,
                }
                for result in message.tool_results
            ],
        }
        for message in messages
    ]
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def build_summary_prompt(
    messages: list[Message], previous_summary: str = ""
) -> list[Message]:
    sections = "\n".join(SUMMARY_SECTIONS)
    prior = ""
    stripped = previous_summary.strip()
    if stripped:
        prior = (
            "## 上一版摘要（滚动合并）\n"
            "下面是上一次压缩生成的摘要。它覆盖的历史不再重复出现，"
            "你必须原样保留其中的全部关键事实，并与本次新增片段合并为一份"
            "完整的新版摘要；第 6 节应包含上一版摘要第 6 节的全部用户原话，"
            "再加上本次新增片段中的用户原话。\n\n"
            f"{stripped}\n\n"
        )
    content = (
        "请总结以下编码 Agent 会话。先在 <analysis> 标签内写分析草稿，"
        "再在 <summary> 标签内写正式摘要。分析草稿会被丢弃。\n\n"
        "<analysis>分析草稿</analysis>\n"
        "<summary>\n"
        f"{sections}\n"
        "</summary>\n\n"
        "第 6 节必须按出现顺序保留所有用户消息原文。不要调用工具，只输出纯文本。\n\n"
        f"{prior}"
        "[conversation]\n"
        f"{serialize_conversation(messages)}"
    )
    return [Message(role="user", content=content)]


def extract_summary(text: str) -> str:
    match = _SUMMARY_RE.search(text)
    return match.group(1).strip() if match else text.strip()
