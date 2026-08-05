# 项目记忆与会话持久化 Tasks

> 包名：`endless_code`（Python 3.12+）。源码位于 `src/endless_code/`。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `src/endless_code/compact/state.py` | session ID 格式变更、`SessionContext` 加 `session_dir`、`open_session_context` |
| 修改 | `src/endless_code/conversation.py` | `from_messages` 类方法、回调触发 |
| 修改 | `tests/test_conversation.py` | 回调测试 |
| 修改 | `src/endless_code/prompt/__init__.py` | `build_system_prompt` 签名变更 |
| 修改 | `src/endless_code/prompt/modules.py` | `optional_modules` 改为接受参数 |
| 修改 | `tests/test_prompt.py` | 新签名测试 |
| 新建 | `src/endless_code/instructions/__init__.py` | 子包标识 |
| 新建 | `src/endless_code/instructions/loader.py` | Loader 类、三层加载、@include 展开 |
| 新建 | `tests/test_instructions_loader.py` | @include 深度/环路/逃逸/缺失文件测试 |
| 新建 | `src/endless_code/session/__init__.py` | 子包标识 |
| 新建 | `src/endless_code/session/writer.py` | Writer、Entry、append、`open_existing` |
| 新建 | `src/endless_code/session/list.py` | `list_sessions`、SessionInfo |
| 新建 | `src/endless_code/session/load.py` | `load_session`、坏行跳过、孤立截断 |
| 新建 | `src/endless_code/session/cleanup.py` | `clean_expired`、ID 时间戳解析 |
| 新建 | `tests/test_session.py` | JSONL 读写、列表、恢复、清理测试 |
| 新建 | `src/endless_code/memory/__init__.py` | 子包标识 |
| 新建 | `src/endless_code/memory/types.py` | NoteType、Note、UpdateAction |
| 新建 | `src/endless_code/memory/store.py` | Store、笔记文件 CRUD、索引读写 |
| 新建 | `src/endless_code/memory/manager.py` | Manager、`load_index`、`update_async` |
| 新建 | `src/endless_code/memory/prompts.py` | 记忆更新 prompt 模板 |
| 新建 | `tests/test_memory.py` | 索引加载、操作执行、截断测试 |
| 修改 | `src/endless_code/agent/__init__.py` | run 末尾触发记忆更新，并复用现有 Agent/SessionRuntime |
| 修改 | `src/endless_code/tui/commands.py` | /resume 命令注册 |
| 修改 | `src/endless_code/tui/app.py` | `RESUMING` 状态集成、App 新增字段与恢复 UI |
| 修改 | `src/endless_code/cli.py` | 启动流程串联 |

## T1: Session ID 格式变更**文件：** `src/endless_code/compact/state.py`
**依赖：** 无
**步骤：**
1. 修改 `_new_session_id()`：格式从 `<unix_ts>-<8hex>` 改为 `YYYYMMDD-HHMMSS-<4hex>`。使用 `datetime.now().strftime("%Y%m%d-%H%M%S")` 拼接 `secrets.token_hex(2)` 生成的 4 字符随机十六进制
2. `SessionContext` 新增 `session_dir: str` 字段，值为 `<workspace>/.endless-code/sessions/<session_id>`
3. 修改 `new_session_context`：先算 `session_dir`，`spill_dir` 改为 `os.path.join(session_dir, "tool-results")`
4. 新增 `open_session_context(workspace: str, session_id: str) -> SessionContext`：定位既有目录并确保 `tool-results/` 存在后填充字段
5. 新增 `parse_session_time(session_id: str) -> datetime`：从 ID 前 15 位解析 `YYYYMMDD-HHMMSS`，供清理和排序使用

**验证：** `pytest tests/test_compact_state.py` 通过；新 session ID 格式形如 `20260601-143022-a1b2`

## T2: Conversation 回调机制**文件：** `src/endless_code/conversation.py`、`tests/test_conversation.py`
**依赖：** 无
**步骤：**
1. `Conversation.__init__` 接受 `on_append: Callable[[llm.Message], None] | None = None` 与 `on_replace: Callable[[list[llm.Message]], None] | None = None` 两个可选参数，保存为私有属性
2. 新增 `from_messages` 类方法：用 `list(msgs)` 拷贝初始化消息列表，传入回调
3. 在 `add_user`、`add_assistant`、`add_assistant_with_tool_calls`、`add_tool_results` 末尾（持锁结束后）调用 `self._on_append(msg)`（如果非 None）
4. 在 `replace_history` 实际替换历史后（持锁结束后）调用 `self._on_replace(list(self._messages))`（如果非 None）；内容未变时不写入重复 compact 标记
5. 补充测试：验证回调被正确触发，验证无回调时行为不变

**验证：** `pytest tests/test_conversation.py` 通过

## T3: 项目指令加载器**文件：** `src/endless_code/instructions/__init__.py`、`src/endless_code/instructions/loader.py`、`tests/test_instructions_loader.py`
**依赖：** 无
**步骤：**
1. 定义 `class Loader`：`project_root`、`user_config_dir`、`max_depth`（默认 5）。用户目录缺省为 `~/.config/endless-code/`
2. 实现 `load() -> str`：按优先级扫描三个路径，每个调 `_load_file`，结果用 `"\n\n"` 拼接
3. 实现 `_load_file(path, boundary, depth, visited)`：
   - 检查 `depth > max_depth` → 返回深度警告注释
   - 解析绝对路径（`os.path.realpath`），检查 visited → 环路警告
   - 检查绝对路径在 boundary 下（`os.path.commonpath` 或 `Path.is_relative_to`）→ 逃逸警告
   - 读取文件二进制，检查前 512 字节有 `b"\x00"` → 二进制警告
   - 逐行扫描，正则 `^@include\s+(.+)$` 匹配独占行 → 递归 `_load_file` 展开
   - 返回展开后的完整内容
4. 测试用例：三层加载优先级、@include 正常展开、5 层深度截断、环路检测、路径逃逸、缺失文件跳过、二进制文件跳过

**验证：** `pytest tests/test_instructions_loader.py` 通过

## T4: Session Writer**文件：** `src/endless_code/session/__init__.py`、`src/endless_code/session/writer.py`
**依赖：** T1（`session_dir` 字段）
**步骤：**
1. 直接序列化现有 `llm.Message`、`ToolCall` 和 `ToolResult`，避免额外 DTO 与协议类型漂移
2. 实现 `Writer.__init__(session_dir, model)`：创建目录，以追加文本模式打开 `conversation.jsonl`，保存 `threading.Lock`
3. 实现 `Writer.open_existing(session_dir)` 类方法：不创建目录，直接 append 模式打开
4. 实现 `append(msg, model, is_first)`：构造 Entry，`is_first` 时填充 `model` 字段，加锁 → `json.dumps(asdict(entry), ensure_ascii=False)` + `"\n"` → `file.write(...)` → `file.flush()` + `os.fsync(file.fileno())` → 解锁
5. 实现 `write_compact_marker()`：写入 `{"type":"compact","ts":<unix_ts>}\n`
6. 实现 `append_all(msgs)`：逐条调 `append`（model 空、is_first False）
7. 实现 `close()` + `__enter__` / `__exit__`：关闭文件句柄

**验证：** `python -c "from endless_code.session.writer import Writer"` 不报错

## T5: 会话列表扫描**文件：** `src/endless_code/session/list.py`
**依赖：** T1（`parse_session_time`）
**步骤：**
1. 定义 `@dataclass class SessionInfo`（字段见 plan.md）
2. 实现 `list_sessions(sessions_dir) -> list[SessionInfo]`：
   - `pathlib.Path(sessions_dir).iterdir()` 遍历子目录
   - 对每个目录：尝试 `parse_session_time(dir.name)` → 失败则跳过（旧格式）
   - 检查 `conversation.jsonl` 是否存在 → 不存在则跳过
   - 打开 JSONL，逐行读到第一条 `role == "user"` 的消息 → 取 `content` 作为 title（截断到 50 字符）
   - 从第一条消息的 `model` 字段提取 model
   - `Path.stat()` 获取 `st_size` 和 `st_mtime`
   - 按 `modified_at` 倒序排列返回

**验证：** `python -c "from endless_code.session.list import list_sessions"` 不报错

## T6: 会话加载恢复**文件：** `src/endless_code/session/load.py`
**依赖：** T4（Entry 类型）
**步骤：**
1. 实现 `load_session(session_dir) -> list[llm.Message]`：
   - 逐行读取 JSONL，`json.loads` 解析为 dict
   - 解析失败的行 `continue`（坏行容错）
   - 记录最后一个 `type == "compact"` 标记的行号
   - 从最后 compact 标记之后开始构建 `list[llm.Message]`
   - 扫描结尾：如果最后一条是 assistant 且有 `tool_calls`，但后面没有 tool 消息 → 截断掉该条
   - 返回 messages
2. 提取 `_truncate_orphaned_tool_calls(msgs) -> list[llm.Message]` 为独立函数便于测试

**验证：** `python -c "from endless_code.session.load import load_session"` 不报错

## T7: 会话过期清理**文件：** `src/endless_code/session/cleanup.py`
**依赖：** T1（`parse_session_time`）
**步骤：**
1. 实现 `clean_expired(sessions_dir, max_age)`：
   - 遍历子目录
   - `parse_session_time(dir.name)` → 失败跳过
   - 时间距今超过 `max_age` → `shutil.rmtree(dir_path, ignore_errors=False)`
   - 单个删除失败 `logging.warning(...)` 继续

**验证：** `python -c "from endless_code.session.cleanup import clean_expired"` 不报错

## T8: Session 子包测试**文件：** `tests/test_session.py`
**依赖：** T4, T5, T6, T7
**步骤：**
1. `test_writer_append_and_read`：写入 3 条消息 → 逐行读回验证 JSON 结构
2. `test_writer_compact_marker`：写入消息 → compact 标记 → 新消息 → `load_session` 只返回 compact 后的
3. `test_load_session_bad_line_skip`：插入坏行 → 被跳过，其余正常
4. `test_load_session_orphaned_tool_calls`：末尾是带 `tool_calls` 的 assistant → 被截断
5. `test_list_sessions`：创建 3 个 session 目录 → 列表返回 3 项，按时间倒序
6. `test_list_sessions_skips_old_format`：混合新旧格式目录 → 只返回新格式
7. `test_clean_expired`：创建一个 31 天前和一个 1 天前的目录 → 只删 31 天前的

**验证：** `pytest tests/test_session.py` 通过

## T9: 笔记类型与存储**文件：** `src/endless_code/memory/__init__.py`、`src/endless_code/memory/types.py`、`src/endless_code/memory/store.py`
**依赖：** 无
**步骤：**
1. `types.py`：定义 `NoteType`（StrEnum）、`Note`（dataclass）、`UpdateAction`（dataclass）
2. `store.py`：
   - `Store.__init__(dir)`：保存 `dir` 与 `threading.Lock()`
   - `ensure_dir()`：`os.makedirs(self._dir, exist_ok=True)`
   - `load_index() -> str`：读取 `MEMORY.md` 内容；不存在返回空字符串
   - `apply(actions)`：
     - create：拼装 frontmatter（用 `yaml.safe_dump` 生成或手写）+ content 写到 `<type>_<slug>.md`，在 `MEMORY.md` 追加一行
     - update：重写文件内容和 frontmatter（保留 created，更新 updated），更新 `MEMORY.md` 对应行
     - delete：`os.remove(...)`，从 `MEMORY.md` 移除对应行

**验证：** `python -c "from endless_code.memory.store import Store"` 不报错

## T10: 记忆管理器**文件：** `src/endless_code/memory/manager.py`、`src/endless_code/memory/prompts.py`
**依赖：** T9
**步骤：**
1. `prompts.py`：定义 `MEMORY_UPDATE_SYSTEM_PROMPT` 字符串常量（中文），包含规则说明和 JSON 输出格式
2. `manager.py`：
   - `Manager.__init__(project_dir, user_dir, provider, model)`：内部建两个 `Store`，记录 `provider/model`，`asyncio.Lock` 保护并发更新
   - `load_index() -> str`：合并项目级和用户级索引（项目级在前、用户级在后），超 25KB 截断并追加 `(index truncated)`
   - `set_provider(provider, model)`：延迟设置（启动时 provider 未选定）
   - `async update_async(recent_msgs)`：
     - 进入 `async with self._lock`（防并发更新）
     - 构造记忆更新请求：system prompt + 最近消息 + 现有索引拼出一条 user 消息
     - 调用 `provider.stream(...)`（不传 tools）
     - 异步收集完整回复，`json.loads` 解析 JSON 数组
     - 按 `level` 字段分发到 `project_store.apply(...)` / `user_store.apply(...)`
     - 任何失败 `logging.exception(...)` 不上抛

**验证：** `python -c "from endless_code.memory.manager import Manager"` 不报错

## T11: Memory 子包测试**文件：** `tests/test_memory.py`
**依赖：** T9, T10
**步骤：**
1. `test_store_create_note`：apply create → 文件存在、frontmatter 正确、`MEMORY.md` 有对应行
2. `test_store_update_note`：apply update → 文件内容更新、`MEMORY.md` 对应行更新
3. `test_store_delete_note`：apply delete → 文件不存在、`MEMORY.md` 对应行消失
4. `test_manager_load_index`：两级各有索引 → 合并返回，项目级在前
5. `test_manager_load_index_truncate`：构造超 25KB 索引 → 截断 + `(index truncated)` 标注
6. `test_manager_update_async_parses_response`：mock provider 返回 JSON → 笔记文件被创建

**验证：** `pytest tests/test_memory.py` 通过

## T12: build_system_prompt 参数化**文件：** `src/endless_code/prompt/__init__.py`、`src/endless_code/prompt/modules.py`、`tests/test_prompt.py`
**依赖：** 无
**步骤：**
1. `prompt/modules.py`：`optional_modules` 改为 `optional_modules(instructions: str, memory: str) -> list[Module]`，用参数填充 content；空字符串时跳过对应模块
2. `prompt/__init__.py`：`build_system_prompt` 改为 `build_system_prompt(instructions: str, memory: str) -> str`，传参给 `optional_modules`
3. 更新 `agent/__init__.py` 的 `_run` 请求组装，在每轮开始时传入启动期指令和最新记忆索引
4. 更新测试：验证非空参数时模块出现在系统提示中，空参数时模块被跳过

**验证：** `pytest tests/test_prompt.py` 通过

## T13: /resume 命令注册**文件：** `src/endless_code/tui/commands.py`
**依赖：** 无
**步骤：**
1. 在 `BUILTIN_COMMANDS` 字典中注册 `"/resume" → handle_resume`
2. `handle_resume(app)` 函数：检查 `app.state == SessionState.IDLE`，调用 `app.begin_resume()` 进入选择列表

**验证：** `python -c "from endless_code.tui.commands import BUILTIN_COMMANDS"` 不报错

## T14: 会话列表 UI**文件：** `src/endless_code/tui/app.py`
**依赖：** T5（`list_sessions`）, T13
**步骤：**
1. `app.py`：
   - `SessionState` 枚举新增 `RESUMING = "resuming"`
   - `EndlessCodeApp` 新增字段：`writer: session.Writer`、`memory_manager: memory.Manager`、`instruction_text: str`、`sessions_dir: str`
   - `__init__` 扩展：接收 `memory_manager` 与 `instruction_text`；会话 Writer 在 Provider 激活后创建
   - `on_key` / 子组件分发：`RESUMING` 状态分发到 `resume.handle_resume_key`
   - `compose` / 显示：`RESUMING` 时显示会话列表
2. 在 `EndlessCodeApp` 内部维护 `OptionList`、搜索输入和过滤后的 `SessionInfo`；`_restore_session` 负责 token 检查、时间跨度提醒、Writer/Conversation/SessionContext 切换。

**验证：** `pytest tests/test_tui.py -q` 通过，`/resume` 可选择并恢复 JSONL 会话

## T15: Agent 记忆更新触发**文件：** `src/endless_code/agent/__init__.py`、`src/endless_code/agent/__init__.py`
**依赖：** T10（Manager）
**步骤：**
1. `agent/__init__.py`：Agent 接受 `memory_manager: memory.Manager | None = None`、`instruction_text: str = ""`、`memory_text: str = ""` 三个构造参数
2. `agent/__init__.py`：在 `run` 协程的 Done 分支（模型回复无工具调用），`conv.add_assistant(text)` 之后：
   - 提取最近一轮消息（从最后一条 user 到当前 assistant）
   - 递增 `runtime.turn_count`，满足任一条件时 `asyncio.create_task(mem_mgr.update_async(recent_msgs))`：① `turn_count % 5 == 0`；② `_has_memory_signal(recent_msgs)` 检测到"记住/记忆/别忘/remember/memo"关键词
3. `agent/__init__.py`：在 `_run` 开始时调用 `build_system_prompt(self._instruction_text, mem_mgr.load_index())`；未配置 Manager 时回退构造参数中的 memory 文本

**验证：** `pytest tests/test_agent.py` 通过

## T16: cli.py 启动流程串联**文件：** `src/endless_code/cli.py`
**依赖：** T1, T2, T3, T4, T10, T12, T14, T15
**步骤：**
1. 在 `config.load(...)` 之后、构建工具注册表之前插入：
   - `instructions.Loader(root).load()` → `instruction_text`
   - `memory.Manager(project_mem_dir, user_mem_dir, provider=None, model="")` → `mem_mgr`
   - `mem_mgr.load_index()` → `memory_text`
2. 在 `permission.Engine()` 之后：
   - `asyncio.create_task(session.clean_expired(sessions_dir, timedelta(days=30)))`
3. 修改 `EndlessCodeApp(...)` 调用：传入 `mem_mgr` 和 `instruction_text`
4. 在 TUI 的 provider 选定回调中创建 `SessionRuntime`、`Writer` 和带回调的 `Conversation`，随后调用 `mem_mgr.set_provider(provider, model)`

**验证：** `python -m endless_code` 能启动；`ruff check src/endless_code` 无告警；`mypy src/endless_code` 无报错（可选）

## T17: 集成验证**文件：** `tests/test_tui.py`、`tests/test_mcp_cli.py`
**依赖：** T14、T16
**步骤：**
1. 覆盖 `/resume` 对既有 JSONL 会话的恢复、搜索和 Enter 选择。
2. 确认 CLI 传入新启动依赖时，旧测试替身仍可按旧构造签名工作。

**验证：** `pytest tests/test_tui.py tests/test_mcp_cli.py -q` 通过

## 执行顺序

```
T1（Session ID）─┐
T2（Conv 回调）──┤
T3（指令加载）──┤
T9（笔记存储）──┤── 独立基础模块，可并行
T12（Prompt 参数化）─┤
T13（/resume 注册）──┤
T17（配置示例）──────┘

T4（Session Writer）── 依赖 T1
T5（会话列表）──────── 依赖 T1
T6（会话加载）──────── 依赖 T4
T7（会话清理）──────── 依赖 T1
T8（Session 测试）──── 依赖 T4,T5,T6,T7

T10（记忆管理器）──── 依赖 T9
T11（Memory 测试）─── 依赖 T9,T10

T14（会话列表 UI）─── 依赖 T5,T13
T15（Agent 记忆触发）── 依赖 T10,T12
T16（cli.py 串联）─── 依赖 T1,T2,T3,T4,T10,T12,T14,T15
```
