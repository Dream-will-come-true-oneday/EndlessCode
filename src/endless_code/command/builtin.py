"""内置命令：高频操作的定义与注册。"""

from endless_code.command.prompts import (
    build_explain_prompt,
    build_fix_prompt,
    build_review_prompt,
)
from endless_code.command.registry import Registry
from endless_code.command.types import CommandKind, CommandSpec
from endless_code.permission import parse_mode

_PERMISSION_OPTIONS = "default | acceptEdits | plan | bypassPermissions"


def _help_line(spec: CommandSpec) -> str:
    if spec.aliases:
        return f"{spec.name} [{' '.join(spec.aliases)}] — {spec.description}"
    return f"{spec.name} — {spec.description}"


def _detail_line(spec: CommandSpec) -> str:
    lines = [
        f"{spec.name} — {spec.description}",
        f"用法：{spec.usage}",
    ]
    if spec.arg_hint:
        lines.append(f"参数：{spec.arg_hint}")
    lines.append(f"类型：{spec.kind.value}")
    if spec.aliases:
        lines.append(f"别名：{' '.join(spec.aliases)}")
    return "\n".join(lines)


def register_builtin_commands(registry: Registry) -> None:
    """注册全部内置命令。"""

    def help_command(host, args: str) -> None:
        needle = args.strip()
        if not needle:
            lines = [_help_line(spec) for spec in registry.visible()]
            host.show_notice("\n".join(lines) if lines else "暂无可用命令。")
            return
        spec = registry.lookup(needle)
        if spec is None or spec.hidden:
            host.show_notice(f"未知命令：{needle}。输入 /help 查看可用命令。")
            return
        host.show_notice(_detail_line(spec))

    def exit_command(host, _args: str) -> None:
        host.quit_app()

    def compact_command(host, _args: str) -> None:
        host.start_compact()

    def clear_command(host, args: str) -> None:
        if args.strip().lower() == "yes":
            host.clear_session()
            return
        host.show_notice("清空将重置当前会话且不可恢复，输入 /clear yes 确认。")

    def plan_command(host, _args: str) -> None:
        host.enter_plan()

    def do_command(host, _args: str) -> None:
        host.start_execute()

    def resume_command(host, _args: str) -> None:
        host.start_resume()

    def rewind_command(host, _args: str) -> None:
        if not host.rewind_available():
            host.show_notice("当前环境不支持 checkpoint 回滚。")
            return
        host.show_notice(f"checkpoint 模式：{host.get_checkpoint_mode()}")
        host.start_rewind()

    def audit_command(host, _args: str) -> None:
        host.show_audit()

    def memory_command(host, _args: str) -> None:
        index = host.get_memory_index()
        if not index.strip():
            host.show_notice("当前未配置长期记忆。")
            return
        host.show_notice(f"长期记忆索引：\n{index}")

    def permissions_command(host, args: str) -> None:
        needle = args.strip()
        if not needle:
            host.show_notice(
                f"当前权限模式：{host.get_mode()}。可选：{_PERMISSION_OPTIONS}"
            )
            return
        mode, ok = parse_mode(needle)
        if not ok:
            host.show_notice(f"未知权限模式：{needle}。可选：{_PERMISSION_OPTIONS}")
            return
        host.set_mode(mode)
        host.show_notice(f"已切换到 {mode} 模式。")

    def status_command(host, args: str) -> None:
        info = host.get_session_info()
        threshold_line = f"自动压缩阈值：{info.auto_compact_threshold}"
        if info.degraded:
            threshold_line += "（窗口过小，已进入降级模式）"
        lines = [
            f"版本：{info.version}",
            f"Provider：{info.provider}",
            f"模型：{info.model}",
            f"权限模式：{info.mode}",
            f"输出样式：{info.output_style}",
            f"上下文窗口：{info.context_window}",
            f"可用窗口：{info.usable_window}",
            threshold_line,
            f"Token 用量：↑{info.tokens_in} ↓{info.tokens_out}",
            f"会话标识：{info.session_id}",
            f"消息数：{info.message_count}",
        ]
        host.show_notice("\n".join(lines))

    def model_command(host, args: str) -> None:
        needle = args.strip()
        if not needle:
            options = host.get_model_options()
            if not options:
                host.show_notice("未配置可用模型。")
                return
            lines = ["可用模型："]
            for option in options:
                line = f"{option.index}. {option.name} — {option.model}"
                if option.current:
                    line += "（当前）"
                lines.append(line)
            host.show_notice("\n".join(lines))
            return
        result = host.switch_model(needle)
        if result.ok:
            host.show_notice(result.message)
        else:
            host.show_error(result.message)

    def style_command(host, args: str) -> None:
        needle = args.strip()
        if not needle:
            lines = ["输出样式："]
            for option in host.get_style_options():
                line = f"{option.name}（{option.label}）— {option.description}"
                if option.current:
                    line += "（当前）"
                lines.append(line)
            host.show_notice("\n".join(lines))
            return
        result = host.set_output_style(needle)
        if result.ok:
            host.show_notice(result.message)
        else:
            host.show_error(result.message)

    def review_command(host, args: str) -> None:
        host.send_user_message(build_review_prompt(args))

    def fix_command(host, args: str) -> None:
        error = host.get_last_error()
        if not error:
            host.show_notice("没有可修复的最近错误。")
            return
        host.send_user_message(build_fix_prompt(error, args))

    def explain_command(host, args: str) -> None:
        reply = host.get_last_reply()
        if not reply:
            host.show_notice("没有可解释的最近回复。")
            return
        host.send_user_message(build_explain_prompt(reply, args))

    commands = [
        CommandSpec(
            name="/help",
            description="显示命令列表或单个命令的详细用法",
            usage="/help [命令名]",
            kind=CommandKind.LOCAL,
            handler=help_command,
            aliases=("/?",),
            arg_hint="[命令名]",
        ),
        CommandSpec(
            name="/exit",
            description="安全退出程序",
            usage="/exit",
            kind=CommandKind.LOCAL,
            handler=exit_command,
            aliases=("/quit",),
        ),
        CommandSpec(
            name="/compact",
            description="手动触发上下文压缩",
            usage="/compact",
            kind=CommandKind.UI,
            handler=compact_command,
        ),
        CommandSpec(
            name="/clear",
            description="清空当前会话（需 /clear yes 确认）",
            usage="/clear yes",
            kind=CommandKind.UI,
            handler=clear_command,
            arg_hint="yes",
        ),
        CommandSpec(
            name="/plan",
            description="进入计划模式（只读工具）",
            usage="/plan",
            kind=CommandKind.UI,
            handler=plan_command,
        ),
        CommandSpec(
            name="/do",
            description="退出计划模式，开始执行上文计划",
            usage="/do",
            kind=CommandKind.UI,
            handler=do_command,
        ),
        CommandSpec(
            name="/resume",
            description="恢复历史会话",
            usage="/resume",
            kind=CommandKind.UI,
            handler=resume_command,
        ),
        CommandSpec(
            name="/rewind",
            description="回滚文件/对话到本会话检查点",
            usage="/rewind",
            kind=CommandKind.UI,
            handler=rewind_command,
        ),
        CommandSpec(
            name="/audit",
            description="显示当前会话最近的权限审计事件",
            usage="/audit",
            kind=CommandKind.LOCAL,
            handler=audit_command,
        ),
        CommandSpec(
            name="/memory",
            description="显示当前长期记忆概况",
            usage="/memory",
            kind=CommandKind.LOCAL,
            handler=memory_command,
            aliases=("/mem",),
        ),
        CommandSpec(
            name="/permissions",
            description="显示或切换权限模式",
            usage="/permissions [模式名]",
            kind=CommandKind.UI,
            handler=permissions_command,
            aliases=("/perm",),
            arg_hint=f"[{_PERMISSION_OPTIONS}]",
        ),
        CommandSpec(
            name="/model",
            description="列出或切换本会话使用的模型",
            usage="/model [编号|名称]",
            kind=CommandKind.UI,
            handler=model_command,
            aliases=("/models",),
            arg_hint="[编号|名称]",
        ),
        CommandSpec(
            name="/style",
            description="列出或切换输出样式",
            usage="/style [样式名]",
            kind=CommandKind.UI,
            handler=style_command,
            aliases=("/styles",),
            arg_hint="[样式名]",
        ),
        CommandSpec(
            name="/status",
            description="显示版本、模型、模式、token 用量与会话状态",
            usage="/status",
            kind=CommandKind.LOCAL,
            handler=status_command,
        ),
        CommandSpec(
            name="/review",
            description="把代码审查提示词送入对话流，由 AI 审查当前改动",
            usage="/review [审查重点]",
            kind=CommandKind.AI,
            handler=review_command,
            arg_hint="[审查重点]",
        ),
        CommandSpec(
            name="/fix",
            description="把最近错误连同修复提示词送入对话流，由 AI 分析修复",
            usage="/fix [补充说明]",
            kind=CommandKind.AI,
            handler=fix_command,
            arg_hint="[补充说明]",
        ),
        CommandSpec(
            name="/explain",
            description="把最近回复连同解释提示词送入对话流，由 AI 逐段解释",
            usage="/explain [补充说明]",
            kind=CommandKind.AI,
            handler=explain_command,
            arg_hint="[补充说明]",
        ),
    ]
    for spec in commands:
        registry.register(spec)
