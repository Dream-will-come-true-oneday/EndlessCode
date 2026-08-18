"""记忆提取请求模板。"""


def build_memory_prompt(recent_turn: str, catalog: str) -> str:
    return f"""你负责维护 coding agent 的长期记忆。只记录跨会话仍有价值的用户偏好、纠正反馈、项目知识或参考资料；没有内容时四个分类数组都返回空数组。

已有记忆目录（包含可用于 update/delete 的文件名）：
{catalog or "（无）"}

本轮对话：
{recent_turn}

只返回 JSON 对象，不要 Markdown。对象必须包含以下四个数组键：
user_preference、correction_feedback、project_knowledge、reference_material。
每个数组内的操作只能是：
{{"action":"create","title":"简短标题","slug":"lowercase_underscore_slug","content":"笔记正文"}}
{{"action":"update","filename":"existing.md","title":"简短标题","content":"笔记正文"}}
{{"action":"delete","filename":"existing.md"}}
没有某类内容时对应数组返回 []。不要输出 level，存储层级由程序按类型决定。"""
