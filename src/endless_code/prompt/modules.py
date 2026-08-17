"""模块化的稳定系统提示。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Module:
    """一段有稳定排序的系统指令。"""

    name: str
    priority: int
    content: str


def fixed_modules() -> list[Module]:
    """返回按职责拆分的固定系统提示模块。"""
    return [
        Module(
            "identity",
            10,
            "You are an AI coding agent running in the terminal (endless-code).",
        ),
        Module(
            "system_constraints",
            20,
            "Work carefully in the current project. Report what you changed and "
            "what you verified. Do not expose API keys or sensitive values.",
        ),
        Module(
            "task_mode",
            30,
            "Continue using tools across multiple steps until the task is complete, "
            "then give a concise final answer.",
        ),
        Module(
            "action_execution",
            40,
            "When filesystem information or an action is needed, use the appropriate "
            "tool and use tool results to decide the next step.",
        ),
        Module(
            "tool_use",
            50,
            "Prefer dedicated read_file, write_file, edit_file, glob, and grep tools "
            "over constructing equivalent shell commands. Before editing a file, "
            "always read the target content with read_file.",
        ),
        Module(
            "tone_style",
            60,
            "Give concise, accurate answers. Use markdown only when it improves "
            "readability. State uncertainty plainly.",
        ),
        Module(
            "text_output",
            70,
            "After completing the requested work, summarize the outcome and relevant "
            "verification results.",
        ),
    ]


def optional_modules(
    instructions: str = "", memory: str = "", deferred_tools: bool = False
) -> list[Module]:
    """返回可由启动期指令和长期记忆填充的可选模块。"""
    return [
        Module("custom_instructions", 80, instructions),
        Module("active_skills", 90, ""),
        Module("long_term_memory", 100, memory),
        Module(
            "deferred_tools",
            110,
            (
                "Some MCP tools are deferred. Their names are listed in a temporary "
                "user message. Before calling a deferred MCP tool, call ToolSearch "
                "with its exact name, then wait for the next model turn where its "
                "full schema will be available. Never call an unloaded MCP tool "
                "directly."
                if deferred_tools
                else ""
            ),
        ),
    ]


def assemble_system(modules: list[Module]) -> str:
    """按稳定优先级组装非空模块。"""
    return "\n\n".join(
        module.content.strip()
        for module in sorted(modules, key=lambda module: module.priority)
        if module.content.strip()
    )


def build_system_prompt(
    instructions: str = "", memory: str = "", deferred_tools: bool = False
) -> str:
    """构造可缓存的稳定系统提示。"""
    return assemble_system(
        fixed_modules() + optional_modules(instructions, memory, deferred_tools)
    )
