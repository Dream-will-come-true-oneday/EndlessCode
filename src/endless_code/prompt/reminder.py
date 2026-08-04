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


def plan_reminder(full: bool) -> str:
    """返回完整或精简的 Plan Mode 提醒。"""
    return system_reminder(PLAN_REMINDER_FULL if full else PLAN_REMINDER_BRIEF)
