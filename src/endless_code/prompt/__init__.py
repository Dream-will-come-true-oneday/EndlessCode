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
    combine_reminders,
    deferred_tools_reminder,
    plan_reminder,
    system_reminder,
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
    "EXECUTE_DIRECTIVE",
    "PLAN_MODE_REMINDER",
    "PLAN_REMINDER_BRIEF",
    "PLAN_REMINDER_FULL",
    "SYSTEM_PROMPT",
    "Environment",
    "Module",
    "assemble_system",
    "build_system_prompt",
    "combine_reminders",
    "deferred_tools_reminder",
    "fixed_modules",
    "gather_environment",
    "optional_modules",
    "plan_reminder",
    "render_banner",
    "system_reminder",
]
