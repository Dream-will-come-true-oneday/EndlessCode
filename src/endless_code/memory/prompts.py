"""记忆提取请求模板。"""


def build_memory_prompt(recent_turn: str, indexes: str) -> str:
    return f"""你负责维护 coding agent 的长期记忆。只记录跨会话仍有价值的用户偏好、纠正反馈、项目知识或参考资料；没有内容时返回 []。

已有记忆索引：
{indexes or "（无）"}

本轮对话：
{recent_turn}

只返回 JSON 数组，不要 Markdown。每项只能是：
{{"action":"create","level":"project"|"user","type":"user_preference"|"correction_feedback"|"project_knowledge"|"reference_material","title":"简短标题","slug":"lowercase_underscore_slug","content":"笔记正文"}}
{{"action":"update","level":"project"|"user","filename":"existing.md","title":"简短标题","content":"笔记正文"}}
{{"action":"delete","level":"project"|"user","filename":"existing.md"}}
项目相关内容放 project，跨项目的用户偏好和纠正放 user。"""
