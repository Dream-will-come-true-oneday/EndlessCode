"""AI 类命令的预设提示词。"""

REVIEW_PROMPT = (
    "请审查当前代码改动：先用工具查看当前修改（优先执行 git diff 与 git status；"
    "若不在 git 仓库中，则读取最近修改过的文件），再从正确性、潜在风险、可读性"
    "三个方面给出审查意见，指出具体文件与位置，并给出修改建议。"
)
FIX_PROMPT = (
    "上一轮出现了如下错误，请定位原因并修复，修复后说明验证方式：\n\n"
    "错误内容：\n{error}"
)
EXPLAIN_PROMPT = (
    "请逐段解释以下内容的含义、作用与关键结论，指出值得注意的细节：\n\n{context}"
)


def _with_args(prompt: str, args: str) -> str:
    suffix = args.strip()
    if suffix:
        return f"{prompt}\n\n补充要求：{suffix}"
    return prompt


def build_review_prompt(args: str) -> str:
    """构造代码审查提示词；参数作为审查重点追加。"""
    return _with_args(REVIEW_PROMPT, args)


def build_fix_prompt(error: str, args: str) -> str:
    """构造修复错误提示词；携带最近错误文本。"""
    return _with_args(FIX_PROMPT.format(error=error), args)


def build_explain_prompt(context: str, args: str) -> str:
    """构造解释上下文提示词；携带最近回复内容。"""
    return _with_args(EXPLAIN_PROMPT.format(context=context), args)
