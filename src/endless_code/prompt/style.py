"""输出样式预设：名称、展示名、说明与注入正文。

本模块只提供数据与查询函数，不依赖 ``prompt.modules``；
由 ``modules.py`` 单向依赖本模块并用 :func:`style_content` 构造 ``Module``，
避免循环导入。
"""

from dataclasses import dataclass

DEFAULT_STYLE_NAME = "default"
STYLE_MODULE_NAME = "output_style"
STYLE_PRIORITY = 65


@dataclass(frozen=True)
class OutputStyle:
    """一个命名输出样式：命令参数名、展示名、一句话说明与注入正文。"""

    name: str
    label: str
    description: str
    content: str


_STYLE_PRECEDENCE_PREFIX = (
    "The following output requirements take precedence over the tone guidance above."
)

STYLES: tuple[OutputStyle, ...] = (
    OutputStyle(
        name=DEFAULT_STYLE_NAME,
        label="默认",
        description="当前默认风格，简洁准确，必要时使用 Markdown。",
        content="",
    ),
    OutputStyle(
        name="concise",
        label="简洁",
        description="只给结论与必要代码，不复述问题、不加背景。",
        content=(
            f"{_STYLE_PRECEDENCE_PREFIX}\n"
            "Be extremely brief. Give the conclusion and the minimal necessary code "
            "only. Do not restate the question, do not add background, and do not "
            "list alternatives unless the user asked for them."
        ),
    ),
    OutputStyle(
        name="explanatory",
        label="讲解",
        description="同样完成工作，但附带原理、权衡与被否决的方案。",
        content=(
            f"{_STYLE_PRECEDENCE_PREFIX}\n"
            "Keep completing the task fully, and additionally explain the reasoning: "
            "why this approach was chosen, which alternatives were rejected, and what "
            "trade-offs or risks remain. Add insight after meaningful steps rather "
            "than only at the end."
        ),
    ),
    OutputStyle(
        name="learning",
        label="教学",
        description="教学取向，在关键决策点给方案与取舍并邀请你参与。",
        content=(
            f"{_STYLE_PRECEDENCE_PREFIX}\n"
            "Act as a collaborative teacher. At meaningful decision points present "
            "2-3 options with trade-offs and invite the user to choose, explain "
            "unfamiliar concepts briefly, and let the user attempt small decisions "
            "before revealing the answer."
        ),
    ),
)


def all_styles() -> list[OutputStyle]:
    """返回全部输出样式预设，顺序与定义一致。"""
    return list(STYLES)


def find_style(name: str) -> OutputStyle | None:
    """大小写不敏感、去空白后按名称查找；未命中返回 None。"""
    needle = (name or "").strip().lower()
    for style in STYLES:
        if style.name == needle:
            return style
    return None


def style_content(name: str) -> str:
    """返回样式注入正文；未知或 default 返回空串，提示组装永不失败。"""
    style = find_style(name)
    return style.content if style is not None else ""


def style_label(name: str) -> str:
    """返回样式展示名；未知时回落为去除空白的原名。"""
    style = find_style(name)
    if style is not None:
        return style.label
    return (name or "").strip() or DEFAULT_STYLE_NAME
