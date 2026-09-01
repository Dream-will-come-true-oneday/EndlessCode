# 斜杠命令系统 Tasks

> 依据：已批准的 `spec.md` 与 `plan.md`（接口定义以 `plan.md`「核心数据结构与接口」为准）。
> 本文档自包含，执行者无需额外上下文；每步完成后立即运行对应验证。
> 环境：Windows，PowerShell（用 `;` 分隔命令，不能用 `&&`）；测试命令统一为 `python -m pytest`。

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/endless_code/command/types.py` | CommandKind / CommandSpec / ParsedCommand / SessionInfo / CommandError |
| 新建 | `src/endless_code/command/parser.py` | parse_command |
| 新建 | `src/endless_code/command/registry.py` | Registry（注册、冲突、查找、补全） |
| 新建 | `src/endless_code/command/host.py` | CommandHost Protocol |
| 新建 | `src/endless_code/command/dispatcher.py` | Dispatcher（分流、异常兜底、补全） |
| 新建 | `src/endless_code/command/prompts.py` | AI 命令预设提示词 |
| 新建 | `src/endless_code/command/builtin.py` | register_builtin_commands |
| 新建 | `src/endless_code/command/__init__.py` | 包导出 |
| 新建 | `tests/test_command_parser.py` | 解析器测试 |
| 新建 | `tests/test_command_registry.py` | 注册中心测试 |
| 新建 | `tests/test_command_builtin.py` | 分发与内置命令测试（FakeHost） |
| 修改 | `src/endless_code/tui/app.py` | 接线分发器、实现 host、清空会话、补全、状态栏 |
| 删除 | `src/endless_code/tui/commands.py` | 旧命令表（被 command 包取代） |
| 修改 | `tests/test_tui.py` | 增补分流 / /clear / /status / Tab 补全测试 |

## T1: 命令类型定义

**文件：** `src/endless_code/command/types.py`（新建）
**依赖：** 无

**步骤：**
1. 创建 `src/endless_code/command/` 目录。
2. 按 `plan.md`「types.py」定义：
   - `CommandKind(Enum)`：`LOCAL = "local"`、`UI = "ui"`、`AI = "ai"`。
   - `CommandSpec`（`@dataclass(frozen=True)`）：字段依次为 `name: str`、`description: str`、`usage: str`、`kind: CommandKind`、`handler: Callable[[CommandHost, str], None]`、`aliases: tuple[str, ...] = ()`、`arg_hint: str = ""`、`hidden: bool = False`。handler 类型用 `TYPE_CHECKING` 导入 `CommandHost` 字符串注解，避免循环导入。
   - `ParsedCommand`（frozen）：`name: str`、`args: str`。
   - `SessionInfo`（frozen）：`version`、`provider`、`model`、`mode` 均为 `str`；`tokens_in`、`tokens_out`、`message_count` 为 `int`；`session_id: str`。
   - `CommandError(Exception)`。
3. 模块首行写中文 docstring，风格对齐现有代码（如 `conversation.py`）。

**验证：** 运行 `python -m compileall -q src/endless_code/command`，预期无输出（编译通过）。

## T2: 命令解析器

**文件：** `src/endless_code/command/parser.py`（新建）
**依赖：** T1

**步骤：**
1. 实现 `parse_command(text: str) -> ParsedCommand | None`：
   - `text` 不以 `/` 开头（对已 strip 的输入判断）→ 返回 `None`；
   - 以第一个空格切分：左侧为命令名并转小写，右侧 `.strip()` 后作为 `args`；
   - 无空格时 `args` 为空串；`/` 之后为空（输入就是 `/`）时返回 `ParsedCommand(name="", args="")`。

**验证：** 运行 `python -c "from endless_code.command.parser import parse_command; print(parse_command('/HELP x Y'))"`，预期输出 `ParsedCommand(name='/help', args='x Y')`。

## T3: 注册中心

**文件：** `src/endless_code/command/registry.py`（新建）
**依赖：** T1

**步骤：**
1. 实现 `Registry`，内部维护 `_specs: dict[str, CommandSpec]`（键为小写命令名与全部别名，指向同一 spec）：
   - `register(spec)`：校验 `spec.name` 以 `/` 开头且等于其小写形式，否则抛 `CommandError`；收集 `[spec.name, *spec.aliases]`，任一已存在于 `_specs` 时抛 `CommandError`，消息包含冲突键与已占用它的命令名；全部校验通过后写入。
   - `lookup(name)`：`self._specs.get(name.strip().lower())`。
   - `visible()`：去重后按 `name` 排序返回 `hidden is False` 的 spec 列表。
   - `completions(prefix)`：小写前缀匹配全部可见命令的名称与别名，排序去重返回字符串列表。

**验证：** 运行 `python -c "from endless_code.command.registry import Registry; from endless_code.command.types import CommandSpec, CommandKind, CommandError; r=Registry(); s=CommandSpec(name='/a', description='', usage='', kind=CommandKind.LOCAL, handler=lambda h,x:None); r.register(s); r.register(CommandSpec(name='/A', description='', usage='', kind=CommandKind.LOCAL, handler=lambda h,x:None))"`，预期抛出包含冲突信息的 `CommandError`。

## T4: 界面控制抽象接口

**文件：** `src/endless_code/command/host.py`（新建）
**依赖：** T1

**步骤：**
1. 定义 `CommandHost(Protocol)`，方法集合与签名严格按 `plan.md`「host.py」：`show_notice`、`show_error`、`send_user_message`、`get_last_error`、`get_last_reply`、`get_mode`、`set_mode`、`get_session_info`、`get_memory_index`、`show_audit`、`start_compact`、`start_resume`、`enter_plan`、`start_execute`、`clear_session`、`quit_app`。
2. `Mode` 从 `endless_code.permission` 导入；`SessionInfo` 从 `types` 导入。

**验证：** 运行 `python -c "from endless_code.command.host import CommandHost; print(len([m for m in dir(CommandHost) if not m.startswith('_')]))"`，预期输出 `16`。

## T5: 分发器

**文件：** `src/endless_code/command/dispatcher.py`（新建）
**依赖：** T2、T3、T4

**步骤：**
1. 实现 `Dispatcher`：
   - `__init__(self, registry: Registry, host: CommandHost)`。
   - `try_dispatch(text) -> bool`：按 `plan.md` 四步逻辑——非命令返回 `False`；命令名为空调 `host.show_error("非法命令输入：请输入 /命令名，输入 /help 查看帮助。")`；未找到命令调 `host.show_notice(f"未知命令：{name}。输入 /help 查看可用命令。")`；找到则 `try: spec.handler(host, parsed.args) except Exception as exc: host.show_error(f"命令执行失败: {type(exc).__name__}: {exc}")`。所有命令路径返回 `True`。
   - `complete(text) -> tuple[str, list[str]]`：`parse_command` 为 `None` 或 `args` 非空（已在输入参数）时返回原文与空列表；取 `registry.completions(name)`——唯一候选时返回 `(候选名 + " ", [])`；多个时返回 `(text, 候选列表)`；零个时返回 `(text, [])`。

**验证：** 运行 `python -c "from endless_code.command.dispatcher import Dispatcher; print(Dispatcher)"`，预期输出类对象无报错。

## T6: AI 命令提示词

**文件：** `src/endless_code/command/prompts.py`（新建）
**依赖：** 无

**步骤：**
1. 定义三段中文提示词：
   - `REVIEW_PROMPT`：要求 AI 先用工具查看当前改动（优先 `git diff` 与 `git status`，无 git 时读取最近修改的文件），再从正确性、潜在风险、可读性三方面给出审查意见；
   - `FIX_PROMPT`：模板含 `{error}` 占位，要求定位错误原因、给出修复并验证；
   - `EXPLAIN_PROMPT`：模板含 `{context}` 占位，要求逐段解释内容的作用与关键结论。
2. 实现 `build_review_prompt(args)`、`build_fix_prompt(error, args)`、`build_explain_prompt(context, args)`：`args` 非空时以「补充要求：{args}」追加到提示词末尾。

**验证：** 运行 `python -c "from endless_code.command.prompts import build_fix_prompt; print(build_fix_prompt('boom', '注意兼容'))"`，预期输出包含 `boom` 与 `补充要求：注意兼容` 的完整提示词。

## T7: 内置命令注册

**文件：** `src/endless_code/command/builtin.py`（新建）
**依赖：** T1–T6

**步骤：**
1. 按 `plan.md`「builtin.py」表格实现 14 条命令的 handler 与 `register_builtin_commands(registry)`，全部通过 `register` 注册：
   - `/help`（别名 `/?`，LOCAL，arg_hint `[命令名]`）：无参时遍历 `registry.visible()` 逐行输出 `名称 [别名] — 描述`；有参时 `lookup` 输出名称、描述、用法、参数提示、类型（`kind.value`），查不到输出提示。
   - `/exit`（别名 `/quit`，LOCAL）：`host.quit_app()`。
   - `/compact`（UI）：`host.start_compact()`。
   - `/clear`（UI）：`args.strip().lower() == "yes"` 时 `host.clear_session()`，否则 `host.show_notice("清空将重置当前会话且不可恢复，输入 /clear yes 确认。")`。
   - `/plan`（UI）：`host.enter_plan()`；`/do`（UI）：`host.start_execute()`。
   - `/resume`（UI）：`host.start_resume()`。
   - `/audit`（LOCAL）：`host.show_audit()`。
   - `/memory`（别名 `/mem`，LOCAL）：`host.get_memory_index()` 为空提示「当前未配置长期记忆。」，否则 `show_notice` 输出索引内容。
   - `/permissions`（别名 `/perm`，UI，arg_hint `[default|acceptEdits|plan|bypassPermissions]`）：无参 `show_notice` 当前模式与四档可选名；有参用 `endless_code.permission.parse_mode` 解析，成功 `host.set_mode(mode)` 并提示已切换，失败提示合法取值列表。
   - `/status`（LOCAL）：`host.get_session_info()` 后逐行输出全部 8 个字段。
   - `/review`（AI）：`host.send_user_message(build_review_prompt(args))`。
   - `/fix`（AI）：`host.get_last_error()` 为空提示「没有可修复的最近错误。」，否则 `send_user_message(build_fix_prompt(error, args))`。
   - `/explain`（AI）：`host.get_last_reply()` 为空提示「没有可解释的最近回复。」，否则 `send_user_message(build_explain_prompt(reply, args))`。
2. 每条命令的 `usage` 写成可直接照抄的形式，如 `/permissions [模式名]`。

**验证：** 运行 `python -c "from endless_code.command.registry import Registry; from endless_code.command.builtin import register_builtin_commands; r=Registry(); register_builtin_commands(r); print(len(r.visible()))"`，预期输出 `13`。

## T8: 包导出

**文件：** `src/endless_code/command/__init__.py`（新建）
**依赖：** T1–T7

**步骤：**
1. 从各子模块重导出：`CommandKind`、`CommandSpec`、`ParsedCommand`、`SessionInfo`、`CommandError`、`parse_command`、`Registry`、`CommandHost`、`Dispatcher`、`register_builtin_commands`。
2. 定义 `__all__`。

**验证：** 运行 `python -c "from endless_code.command import Dispatcher, Registry, register_builtin_commands; print('ok')"`，预期输出 `ok`。

## T9: 解析器测试

**文件：** `tests/test_command_parser.py`（新建）
**依赖：** T2

**步骤：**
1. 覆盖用例：普通文本返回 `None`；`/status` 无参数；`/STATUS 详细 信息` 名称小写且参数保留原文；`/cmd   多余空格` 参数被 strip；仅 `/` 返回空名称；前后带空格的命令输入（`  /help  `）。

**验证：** 运行 `python -m pytest tests/test_command_parser.py -q`，预期全部通过。

## T10: 注册中心测试

**文件：** `tests/test_command_registry.py`（新建）
**依赖：** T3

**步骤：**
1. 辅助函数构造最小 `CommandSpec`（handler 用 `lambda host, args: None`）。
2. 覆盖用例：正常注册后按名称与各别名 `lookup` 命中；名称大小写冲突抛 `CommandError` 且消息含冲突命令名；别名与他人名称冲突抛错；别名之间冲突抛错；非法名称（无 `/`、含大写）抛错；`visible` 排除 hidden 且按名称排序；`completions` 前缀匹配名称与别名、排除 hidden、空结果返回空列表。

**验证：** 运行 `python -m pytest tests/test_command_registry.py -q`，预期全部通过。

## T11: 分发与内置命令测试

**文件：** `tests/test_command_builtin.py`（新建）
**依赖：** T5、T7

**步骤：**
1. 定义 `FakeHost` 类：实现 `CommandHost` 全部 16 个方法，把调用记录追加到实例属性列表（如 `notices`、`errors`、`sent_messages`），`get_last_error`/`get_last_reply`/`get_memory_index` 返回可注入属性，`get_mode` 返回注入的 `Mode`，`get_session_info` 返回固定 `SessionInfo`。全程不导入 `textual`。
2. 用真实 `Registry` + `register_builtin_commands` + `Dispatcher` 覆盖：
   - 非命令文本 `try_dispatch` 返回 `False` 且 host 无输出；
   - 仅 `/` 返回 `True` 且 `errors` 非空；
   - 未知命令返回 `True` 且 `notices` 含 `/help`；
   - `/HELP` 大写可执行，无参列出全部可见命令，带参输出单命令详情；
   - `/exit` 触发 `quit_app`；`/quit` 同样触发（别名）；
   - `/clear` 无参只提示确认，`/clear yes` 触发 `clear_session`；
   - `/permissions` 无参提示当前模式；`/permissions plan` 触发 `set_mode(Mode.PLAN)`；`/permissions nope` 提示合法取值；
   - `/memory` 有/无索引两种分支；`/status` 输出包含全部字段值；
   - `/review` 产生一条 `sent_messages`；`/fix` 无错误时提示、有错误时发送含错误文本的消息；`/explain` 同理；
   - 注册一个 handler 抛 `RuntimeError` 的测试命令，`try_dispatch` 后 `errors` 含异常信息且不向外抛出；
   - `complete`：唯一匹配返回补全名加空格与空候选；多匹配返回原文与候选列表；隐藏命令不参与。

**验证：** 运行 `python -m pytest tests/test_command_builtin.py -q`，预期全部通过。

## T12: App 接线分发器

**文件：** `src/endless_code/tui/app.py`（修改）
**依赖：** T8

**步骤：**
1. 删除 `from endless_code.tui.commands import dispatch_command`，改为导入 `Dispatcher`、`Registry`、`register_builtin_commands`（来自 `endless_code.command`）与 `SessionInfo`。
2. `__init__` 末尾构建 `self._registry = Registry()`、`register_builtin_commands(self._registry)`、`self._dispatcher = Dispatcher(self._registry, self)`，并新增 `self._last_error = ""`、`self._last_reply = ""`。
3. `on_input_submitted` 与 `_handle_idle_input` 中两处 `dispatch_command(self, text)` 替换为 `self._dispatcher.try_dispatch(text)`。

**验证：** 运行 `python -m pytest tests/test_tui.py -q`，预期现有测试全部通过（行为不变）。

## T13: App 实现 CommandHost 方法

**文件：** `src/endless_code/tui/app.py`（修改）
**依赖：** T12

**步骤：**
1. 新增方法（放在 `_command_audit` 之后的集中区域）：
   - `show_notice(text)` → `self._write_notice(text)`；`show_error(text)` → `self._write_error(text)`；
   - `send_user_message(text)` → `self._start_turn(text)`；
   - `quit_app()` → 调用 `await` 语义的 `action_quit` 需异步处理：实现为 `self._dispatcher` 场景下直接 `self.exit()`（与旧 `_command_exit` 行为一致）；
   - `get_mode()` → `return self._mode`；`set_mode(mode)` → 仅在 `IDLE` 状态生效：`self._mode = mode`，`_write_notice(f"已切换到 {MODE_LABELS[mode]} 模式")`，`_update_status()`；
   - `get_session_info()` → 用现有字段组装 `SessionInfo`（version=`self._version`、provider/model 取 `self._provider`（无则 `--`）、mode=`MODE_LABELS.get(self._mode, str(self._mode))`、tokens=`_usage_in/_usage_out`、session_id=`self._runtime.session.session_id if self._runtime else "--"`、message_count=`self._conv.length()`）；
   - `get_memory_index()` → `self._memory_manager.load_index() if self._memory_manager else ""`；
   - `show_audit()` → `self._command_audit()`；`start_compact()` → `self._command_compact()`；`start_resume()` → `self._command_resume()`；`enter_plan()` → `self._command_plan()`；`start_execute()` → `self._command_do()`；
   - `get_last_error()` → `return self._last_error`；`get_last_reply()` → `return self._last_reply`。
2. 删除 `_command_exit`（已无引用）。

**验证：** 运行 `python -m pytest tests/test_tui.py -q`，预期现有测试全部通过。

## T14: 清空会话实现

**文件：** `src/endless_code/tui/app.py`（修改）
**依赖：** T13

**步骤：**
1. 实现 `clear_session()`：
   - 非 `IDLE` 状态 → `show_notice("当前任务尚未结束，暂不能清空会话。")` 返回；
   - `self._writer` 为 `None` → 仅 `self._conv = Conversation()` 并提示已清空；
   - 否则：`self._writer.close()`；用 `open(writer.path, "w", encoding="utf-8").close()` 截断会话文件；`self._writer = Writer(session_dir, model)`（model 取 `self._provider.model if self._provider else ""`）；`self._conv = Conversation(self._writer.append, self._writer.replace)`；
   - 最后 `self._write_notice("已清空当前会话。")`。
2. `Conversation` 已在文件顶部导入，无需新增。

**验证：** 运行 `python -m pytest tests/test_tui.py -q`，预期现有测试全部通过（/clear 端到端测试在 T19 补）。

## T15: 记录最近错误与回复

**文件：** `src/endless_code/tui/app.py`（修改）
**依赖：** T12

**步骤：**
1. `_write_error` 开头追加 `self._last_error = err_msg`。
2. `_flush_reply` 中 `final=True` 分支：`_write_assistant_message(text)` 之前追加 `self._last_reply = text`。

**验证：** 运行 `python -m pytest tests/test_tui.py -q`，预期现有测试全部通过。

## T16: Tab 补全

**文件：** `src/endless_code/tui/app.py`（修改）
**依赖：** T12

**步骤：**
1. `BINDINGS` 追加 `("tab", "complete_command", "补全命令")`。
2. 新增 `action_complete_command()`：取 `self._input.value`；不以 `/` 开头或处于 `SELECTING`/`RESUMING` 状态时直接 `return`；调用 `text, candidates = self._dispatcher.complete(value)`；候选非空时 `self._write_notice("候选命令：" + " ".join(candidates))`；`text != value` 时 `self._input.value = text` 并把光标移到末尾（`self._input.cursor_position = len(text)`）。
3. 注意：该绑定会接管输入框内的 Tab；仅命令前缀输入时生效，其他情况不做任何事。

**验证：** 运行 `python -m pytest tests/test_tui.py -q`，预期现有测试全部通过（补全测试在 T19 补）。

## T17: 状态栏命令提示

**文件：** `src/endless_code/tui/app.py`（修改）
**依赖：** T12

**步骤：**
1. `_update_status` 的 `sub_title` 拼接末尾追加 ` | /help 查看命令`。

**验证：** 运行 `python -m pytest tests/test_tui.py -q`，预期现有测试全部通过。

## T18: 删除旧命令模块

**文件：** `src/endless_code/tui/commands.py`（删除）
**依赖：** T12

**步骤：**
1. 全库搜索确认无 `tui.commands` / `dispatch_command` / `BUILTIN_COMMANDS` 残留引用。
2. 删除该文件。

**验证：** 运行 `python -m pytest -q`，预期全部测试通过。

## T19: 界面层集成测试

**文件：** `tests/test_tui.py`（修改）
**依赖：** T13–T17

**步骤：**
1. 复用该文件现有 app 构造与输入模拟方式（参考 `test_plan_and_do_command_switch_tools_and_suffix` 的写法），新增：
   - 输入 `/status` 后 `SessionState.IDLE` 不变、无 Agent 请求、聊天区出现模型与模式文本（断言 `sub_title` 或 chat 内容含 `/help`）；
   - 输入 `/clear` 出现确认提示，再输入 `/clear yes` 后 `app._conv.length()` 为 0；
   - 输入 `/nomatch` 出现「未知命令」提示且不进入对话；
   - 输入框写 `/perm` 后触发 `action_complete_command`，出现候选提示；写 `/permissions` 后触发则输入框变为 `/permissions `。
2. 保持测试异步风格与既有用例一致（`asyncio_mode = "auto"`）。

**验证：** 运行 `python -m pytest tests/test_tui.py -q`，预期全部通过。

## T20: 全量工程检查

**文件：** 无新增
**依赖：** T1–T19

**步骤：**
1. 依次执行与 CI 一致的检查：
   - `python -m ruff format --check .`（失败则运行 `python -m ruff format .` 修复后重跑）；
   - `python -m ruff check .`；
   - `python -m compileall -q src examples`；
   - `python -m pytest -q`。
2. 全部通过后提交代码（一次提交即可，信息说明本次为斜杠命令系统）。

**验证：** 四条命令全部零失败；`python -m pytest -q` 输出末行为全绿统计。

## 执行顺序

```text
T1 -> T2 -> T3 -> T4 -> T5 -> T6 -> T7 -> T8
  -> T9 -> T10 -> T11（command 包完成）
  -> T12 -> T13 -> T14 -> T15 -> T16 -> T17 -> T18（App 接线完成）
  -> T19 -> T20
```
