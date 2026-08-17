"""动态 system-reminder 与 Plan Mode 指令。"""

PLAN_REMINDER_FULL = (
    "You are in PLAN MODE. Use only read_file, glob, and grep to investigate. "
    "Do not write or edit files and do not run shell commands. Produce a clear "
    "step-by-step plan, then stop and wait for /do."
)
PLAN_REMINDER_BRIEF = (
    "PLAN MODE remains active: use only read_file, glob, and grep. "
    "Continue investigation and update the execution plan."
)
EXECUTE_DIRECTIVE = "请按上面的计划开始执行。"


def system_reminder(body: str) -> str:
    """标记非持久的系统补充消息。"""
    return f"<system-reminder>{body}</system-reminder>"


def plan_reminder(full: bool, deferred_tools: bool = False) -> str:
    """返回完整或精简的 Plan Mode 提醒。"""
    body = PLAN_REMINDER_FULL if full else PLAN_REMINDER_BRIEF
    if deferred_tools:
        body += (
            " You may also use ToolSearch and MCP tools marked read-only; "
            "load each deferred MCP tool before calling it."
        )
    return system_reminder(body)


def deferred_tools_reminder(names: list[str]) -> str:
    """渲染不持久化的未激活 MCP 工具名录。"""
    if not names:
        return ""
    rendered = "\n".join(f"- {name}" for name in names)
    return system_reminder(
        "Deferred MCP tools available through ToolSearch (names only):\n" + rendered
    )


def combine_reminders(*parts: str) -> str:
    """把多个动态提醒合并为 Provider 的单条 user message。"""
    return "\n\n".join(part.strip() for part in parts if part.strip())
