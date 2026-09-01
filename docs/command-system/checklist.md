# 斜杠命令系统 Checklist

> 依据：已批准的 `spec.md`（AC1–AC9）、`plan.md`、`task.md`。
> 环境：Windows PowerShell（用 `;` 分隔命令）；工作目录为仓库根目录。

## 功能与集成

### 注册中心与解析（AC1、AC2）

- [x] 重复注册同名或别名冲突立即失败（验证：运行 `python -m pytest tests/test_command_registry.py -q`，冲突用例断言 `CommandError` 且消息含冲突命令名，全部通过）
- [x] 命令名大小写不敏感、参数为第一个空格后的原文（验证：运行 `python -m pytest tests/test_command_parser.py -q`，全部通过）
- [x] 仅输入 `/` 判定非法且不送入 AI（验证：运行 `python -m pytest tests/test_command_builtin.py -q`，对应用例通过；`try_dispatch("/")` 返回 True 且 host 收到错误提示）

### 三类执行模式与内置命令（AC3、AC8）

- [x] LOCAL/UI/AI 三类命令各有内置命令可执行成功（验证：运行 `python -m pytest tests/test_command_builtin.py -q`，`/status`（LOCAL）、`/clear yes`（UI）、`/review`（AI）用例全部通过）
- [x] `/help` 无参列出全部可见命令、带参输出单命令详情，隐藏命令不出现（验证：同上测试文件中 help 相关用例通过）
- [x] `/clear` 需二次确认，确认后内存对话与持久化记录被重置（验证：运行 `python -m pytest tests/test_tui.py -q`，/clear 用例中 `app._conv.length()` 为 0 且 `conversation.jsonl` 被截断）
- [x] `/permissions` 无参展示当前模式、带参切换模式、非法参数提示合法取值（验证：运行 `python -m pytest tests/test_command_builtin.py -q`，三个分支用例通过）
- [x] `/memory`、`/status` 输出与注入状态一致；`/fix`、`/explain` 在无上下文时给出提示（验证：同上测试文件相关用例通过）

### 抽象层与解耦（AC4）

- [x] 全部命令逻辑测试不导入渲染框架（验证：运行 `python -c "import ast,sys; tree=ast.parse(open('tests/test_command_builtin.py',encoding='utf-8').read()); print([n.names[0].name if hasattr(n,'names') else n.module for n in ast.walk(tree) if isinstance(n,(ast.Import,ast.ImportFrom))])"`，输出的导入列表中不含 `textual` 与 `rich`）
- [x] `command` 包不依赖 `tui`（验证：对 `src/endless_code/command` 目录执行文本搜索 `from endless_code.tui`，结果为空）

### 分流与兼容（AC5）

- [x] 非命令输入照常进入对话流程，命令输入不产生 AI 请求（验证：运行 `python -m pytest tests/test_tui.py -q`，`/status` 用例无 Agent 请求、普通消息用例照常流式输出）
- [x] 现有 7 个命令行为不变，原有界面测试全部通过（验证：运行 `python -m pytest tests/test_tui.py -q`，改造前已存在的用例全部通过）

### Tab 补全（AC6）

- [x] 唯一前缀直接补全、多匹配给出候选列表（验证：运行 `python -m pytest tests/test_command_builtin.py -q`，`complete` 用例通过；运行 `python -m pytest tests/test_tui.py -q`，`/perm` 出候选、`/permissions` 补全为带尾随空格）

### 状态栏与未知命令（AC7）

- [x] 状态栏显示当前权限模式与命令提示（验证：运行 `python -m pytest tests/test_tui.py -q`，`sub_title` 含模式名与 `/help` 字样）
- [x] 未知命令统一引导帮助（验证：同上测试文件 `/nomatch` 用例，提示中出现 `/help`）

### 异常隔离（AC9）

- [x] handler 抛异常仅产生错误提示且不中断后续输入（验证：运行 `python -m pytest tests/test_command_builtin.py -q`，注入抛 `RuntimeError` 的测试命令用例通过：异常不向外传播、错误提示被记录、之后可继续分发）

### 架构集成点（来自 plan.md）

- [x] 输入入口只有唯一的命令分流调用（验证：对 `src/endless_code/tui/app.py` 搜索 `try_dispatch`，仅出现在 `on_input_submitted` 与 `_handle_idle_input`；搜索 `dispatch_command` 结果为空）
- [x] 旧命令模块已删除（验证：`src/endless_code/tui/commands.py` 不存在；全库搜索 `BUILTIN_COMMANDS` 结果为空）

## 工程检查

- [x] 代码格式合规（验证：运行 `python -m ruff format --check .`，无差异）
- [x] lint 通过（验证：运行 `python -m ruff check .`，无告警）
- [x] 编译通过（验证：运行 `python -m compileall -q src examples`，无输出）
- [x] 全部测试通过（验证：运行 `python -m pytest -q`，无失败）

## 端到端

- [ ] 完整使用旅程：启动应用 → 输入 `/help` 看到 14 条命令列表（含别名与描述）→ 输入 `/status` 看到版本、模型、模式、token、消息数 → 发送一条普通消息得到 AI 回复 → 输入 `/explain` 看到针对最近回复的追问进入对话流 → 输入 `/permissions plan` 看到模式切换提示且状态栏更新 → 输入 `/clear` 看到确认提示、输入 `/clear yes` 后消息数归零（验证：`python -m endless_code` 启动真实应用手工执行上述序列，每步界面输出与预期一致；无法接入真实模型时，用 `examples/smoke.py` 同款 provider 配置替代验证）
- [ ] 补全与错误路径旅程：输入框输入 `/pe` 按 Tab 看到候选列表 → 输入 `/per` 按 Tab 输入框变为 `/permissions ` → 输入 `/unknown` 回车看到未知命令提示并引导 `/help`（验证：真实应用手工执行，行为与描述一致）
