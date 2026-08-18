"""请求期用户上下文前缀。"""

from endless_code.llm import Message

CONTEXT_ACK = (
    "I have loaded the runtime environment, project instructions, and durable "
    "memory for this session."
)


def build_context_prefix(
    environment: str, instructions: str = "", memory: str = ""
) -> list[Message]:
    """构建不写入真实会话历史的 user/assistant 上下文前缀。"""
    sections = [
        ("Runtime environment", environment),
        ("Project instructions", instructions),
        ("Durable memory", memory),
    ]
    body = "\n\n".join(
        f"## {title}\n{content.strip()}"
        for title, content in sections
        if content.strip()
    )
    if not body:
        return []
    return [
        Message(
            role="user",
            content=(
                "The following context is user-maintained background for this "
                f"session.\n\n{body}"
            ),
        ),
        Message(role="assistant", content=CONTEXT_ACK),
    ]
