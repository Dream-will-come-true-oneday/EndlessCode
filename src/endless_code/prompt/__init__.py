"""模块化 System Prompt、环境信息与运行时提醒。"""

from endless_code.prompt.environment import Environment, gather_environment
from endless_code.prompt.modules import (
    Module,
    assemble_system,
    build_system_prompt,
    fixed_modules,
    optional_modules,
)
from endless_code.prompt.reminder import (
    EXECUTE_DIRECTIVE,
    PLAN_REMINDER_BRIEF,
    PLAN_REMINDER_FULL,
    plan_reminder,
    system_reminder,
)
from endless_code.prompt.style import (
    DEFAULT_STYLE_NAME,
    STYLE_MODULE_NAME,
    STYLE_PRIORITY,
    OutputStyle,
    all_styles,
    find_style,
    style_content,
    style_label,
)

SYSTEM_PROMPT = build_system_prompt()
PLAN_MODE_REMINDER = plan_reminder(full=True)

CAT_BANNER = r"""
    /\_____ /\
   /  o   o  \
  ( ==  ^  == )
   )         (
  (           )
 (  )  end   (  )
(  (  less-   )  )
 (  (  code  )  )
  (  (       )  )
   (  (_____)  )
    (_________)
"""


def render_banner(version: str, cwd: str) -> str:
    """返回包含版本号和工作目录的启动横幅。"""
    return (
        f"{CAT_BANNER}\n"
        f"  endless-code v{version}\n"
        f"  cwd: {cwd}\n"
        "  Type /exit to quit, Alt+Enter for newline.\n"
    )


__all__ = [
    "CAT_BANNER",
    "DEFAULT_STYLE_NAME",
    "EXECUTE_DIRECTIVE",
    "PLAN_MODE_REMINDER",
    "PLAN_REMINDER_BRIEF",
    "PLAN_REMINDER_FULL",
    "STYLE_MODULE_NAME",
    "STYLE_PRIORITY",
    "SYSTEM_PROMPT",
    "Environment",
    "Module",
    "OutputStyle",
    "all_styles",
    "assemble_system",
    "build_system_prompt",
    "find_style",
    "fixed_modules",
    "gather_environment",
    "optional_modules",
    "plan_reminder",
    "render_banner",
    "style_content",
    "style_label",
    "system_reminder",
]
