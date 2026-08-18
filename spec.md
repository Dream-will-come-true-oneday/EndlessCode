# 项目记忆与会话持久化 Spec
## 背景

endless_code 当前是无状态的：每次启动都是全新会话，不知道用户是谁、项目有什么规范、上次聊到哪里。ch08 解决了"单进程内长时间工作不崩"的问题，但进程一退出，所有对话历史和工作上下文就全丢了。

实际编码场景里，用户会反复回到同一个项目，有稳定的编码规范、个人偏好和未完成的工作。每次重新解释一遍这些上下文，既浪费时间又容易遗漏。

本章要补的就是"跨会话的记忆"：让 Agent 从"每次失忆"变成"越用越懂你"。靠三套机制——项目指令文件、会话存档、自动笔记——实现工作记忆（会话级）和长期记忆(项目/用户级)的分层管理,进程中断之后也能平滑接着用。

设计思路是三个独立层次。第一层是项目指令文件(ENDLESSCODE.md):用户手写的 Markdown，记技术栈、代码规范、注意事项,三层优先级合并后注入系统提示,让 Agent 从第一轮就遵循项目约定。第二层是会话存档:对话历史以 JSONL 实时追加写入磁盘,崩溃最多丢最后一行;用户可通过 `/resume` 命令从历史会话列表中选择恢复。第三层是自动笔记:每轮 Agent Loop 自然停下后异步调 LLM 提取值得记住的信息,分类存为持久化笔记并在后续会话中自动注入上下文。

三层互相独立但配合作战:指令文件提供静态规范,会话存档提供工作记忆,自动笔记提供演化的长期记忆。

## 目标

- G1：新会话启动时自动加载项目指令和记忆索引，Agent 从第一轮就能遵循项目规范、了解用户偏好。
- G2：对话历史以 JSONL 追加写入磁盘，崩溃最多丢最后一行；恢复时能处理坏行、孤立工具调用、token 超限等异常。
- G3：用户可通过 `/resume` 命令从历史会话列表中选择恢复，交互为上下键选择加搜索过滤。
- G4：Agent 每轮自然停下后自动提取值得记住的信息，分类存储为持久化笔记，无需用户手动管理。
- G5：项目指令支持 `@include` 引用其他文件，有嵌套深度限制和环路检测，防止加载失控。
- G6：所有新增机制对现有 Agent 主循环影响最小，指令加载和记忆注入发生在请求组装阶段，笔记更新异步执行不阻塞交互。
- G7：过期会话（30 天以上）在启动时自动清理，防止磁盘无限增长。
- G8：session ID 格式统一改为 `YYYYMMDD-HHMMSS-xxxx`，新格式同时覆盖 ch08 的工具结果落盘目录和本章的 JSONL 存档。

## 功能需求

### 第 1 层：项目指令文件（ENDLESSCODE.md）

- **F1**：系统启动时按以下顺序扫描三个路径，找到就加载、找不到就跳过：① `<project_root>/ENDLESSCODE.md`（项目级，最高优先级）；② `<project_root>/.endless-code/ENDLESSCODE.md`（项目配置级）；③ `~/.config/endless-code/ENDLESSCODE.md`（用户级，最低优先级）。三份文件的内容按此优先级顺序拼接——高优先级在前，模型优先遵循；各层之间用空行分隔。
- **F2**：`@include` 引用语法：在 ENDLESSCODE.md 中以独占一行的形式写 `@include <relative_path>`（`@include` 后跟一个空格再跟路径）。路径相对于当前文件所在目录解析。`@include` 行被引用文件的完整内容替换；引用文件内部可以继续出现 `@include`。不在独占行上的 `@include`（如出现在段落中间）不做替换，保持原文。
- **F3**：`@include` 嵌套深度限制为 5 层（从 ENDLESSCODE.md 算第 1 层，被它 include 的文件算第 2 层，以此类推）。超过 5 层的 `@include` 行保留原文不展开，并在返回结果中追加一行警告注释 `<!-- @include 超过最大嵌套深度，已跳过: <path> -->`。
- **F4**：`@include` 环路检测：维护一个 visited 集合（已解析为绝对路径的文件集合），同一个绝对路径在一条展开链上不会被加载两次。命中环路时跳过该 `@include` 行并追加警告注释 `<!-- @include 检测到环路，已跳过: <path> -->`。
- **F5**：`@include` 路径逃逸检测：解析后的绝对路径必须仍在当前指令文件所属的"根边界"之内——项目级文件（路径 ① ②）的根边界是 `<project_root>`；用户级文件（路径 ③）的根边界是 `~/.config/endless-code/`。跳出边界的路径不加载，追加警告注释 `<!-- @include 路径超出允许范围，已跳过: <path> -->`。
- **F6**：找不到的文件静默跳过（非错误），空文件产出空内容（不影响拼接）。二进制文件（前 512 字节中包含 `\x00`）视为不可读，跳过并追加警告注释。
- **F7**：加载完成后，拼接结果注入系统提示的 `custom-instructions` 模块槽位（priority 80）。`build_system_prompt` 接受指令文本和记忆文本两个参数，非空时填入对应模块。
- **F8**：指令加载在进程启动时执行一次，结果缓存到 `prompt.Module` 的 content 字段，整个进程生命周期内不变。后续章节可以引入文件监听热更新，本章不做。

### 第 2 层：会话存档（JSONL）#### Session ID 与目录- **F9**：session ID 格式改为 `YYYYMMDD-HHMMSS-xxxx`，其中 `YYYYMMDD-HHMMSS` 取进程启动时刻的本地时间，`xxxx` 为 4 字符随机十六进制后缀（防同秒碰撞）。此格式同时适用于 ch08 的工具结果落盘和本章的 JSONL 存档。修改 `compact/state.py` 的 `_new_session_id()` 函数。
- **F10**：`SessionContext` 新增 `session_dir` 字段（`<workspace>/.endless-code/sessions/<session_id>`），原有 `spill_dir` 改为 `session_dir + "/tool-results"`。JSONL 存档路径为 `session_dir + "/conversation.jsonl"`。

#### JSONL 格式- **F11**：每条消息序列化为一行 JSON，写入 `conversation.jsonl`。字段：
  - `role`（string，必需）：`"user"` / `"assistant"` / `"tool"`
  - `content`（string，可选）：消息正文
  - `tool_calls`（array，可选）：仅 assistant 消息，结构同 `llm.ToolCall`
  - `tool_results`（array，可选）：仅 tool 消息，结构同 `llm.ToolResult`
  - `ts`（int，必需）：写入时刻的 Unix 时间戳（秒）
  - `model`（string，可选）：仅第一条消息携带，记录当前 provider 的模型名，供会话列表展示
- **F12**：压缩标记行：当 `Conversation.replace_history` 实际替换历史时（ch08 的第 2 层摘要完成后），先追加一行 `{"type":"compact","ts":<unix_ts>}`，然后逐条追加新的压缩后消息。恢复时从最后一个 compact 标记之后开始加载。
- **F13**：追加时机：`Conversation` 每次 `add_user`、`add_assistant`、`add_assistant_with_tool_calls`、`add_tool_results` 执行后，以及 `replace_history` 实际替换历史后，通过回调将消息追加到 JSONL。回调由 `Conversation` 构造时注入。
- **F14**：崩溃安全性：JSONL 只做追加写，不重写已有内容。进程崩溃最多丢失最后一行不完整的写入。

#### 会话写入器- **F15**：`session.Writer` 持有打开的文件句柄和一把 `asyncio.Lock`（或 `threading.Lock`），保证多协程/线程追加的原子性。每次 append 后调用 `file.flush()` + `os.fsync(file.fileno())` 刷盘。
- **F16**：进程退出时 `Writer.close()` 关闭文件句柄。`Writer` 实现 `__enter__` / `__exit__` 上下文管理协议。

### 第 3 层：会话恢复- **F17**：TUI 新增 `/resume` 内置命令，仅在 `SessionState.IDLE` 状态可用（Agent 不在运行中）。
- **F18**：`/resume` 触发后，扫描 `.endless-code/sessions/` 下所有子目录，找到包含 `conversation.jsonl` 的有效会话。按最后修改时间倒序排列（最新在前）。
- **F19**：会话列表 UI 复用 Textual `OptionList`（或 `SelectionList`）组件：上下键导航、输入搜索过滤、Enter 选择、Esc 取消。TUI 新增 `SessionState.RESUMING` 状态。
- **F20**：每条列表项展示四项信息：
  - 标题：第一条 role=user 消息的 content，截断到 50 个字符（含省略号）
  - 相对时间：如 "1 day ago"、"3 hours ago"
  - 模型标签：从第一条消息的 `model` 字段读取
  - 文件大小：`conversation.jsonl` 的磁盘大小
- **F21**：选择会话后进入恢复流程：
  1. 逐行读取 JSONL，从最后一个 `compact` 标记之后开始构建消息列表
  2. 跳过 JSON 解析失败的坏行（静默）
  3. 如果最后一条 assistant 消息包含 `tool_calls` 但之后没有对应的 tool 消息，截断到该 assistant 消息之前
  4. 估算加载后的 token 数：若超过 `context_window - summary_reserve - auto_safety_margin`，先执行一次压缩
  5. 如果最后一条消息的 ts 距当前时间超过 6 小时，在对话末尾追加一条 user 消息作为时间跨度提醒：`"[系统提示] 本会话已暂停 <duration>。部分上下文可能已过时，如需最新信息请重新读取相关文件。"`
- **F22**：恢复完成后，当前会话切换为被恢复的会话：重建 `Conversation`、重新打开该会话的 `Writer`（追加模式）、替换 `SessionContext`（使用被恢复会话的 ID 和目录）。后续新消息追加到同一个 JSONL 文件。
- **F23**：恢复过程中 TUI 显示加载提示，恢复完成后显示系统消息：`"已恢复会话 <session_id>，共 <N> 条消息"`。
- **F24**：原来的新会话的 JSONL 保留在磁盘上（可能已有几行），不删除。

#### 会话清理

- **F25**：进程启动时扫描 `.endless-code/sessions/`，删除 session ID 中的时间戳距当前超过 30 天的会话目录（整个子目录，含 JSONL 和 tool-results）。
- **F26**：清理在后台 asyncio task 执行，不阻塞启动流程。清理失败的单个目录跳过不影响其他目录。

### 第 4 层：自动笔记（Memory）#### 笔记存储- **F27**：笔记分四类：`user_preference`（用户偏好）、`correction_feedback`（纠正反馈）、`project_knowledge`（项目知识）、`reference_material`（参考资料）。
- **F28**：每条笔记是一个独立的 Markdown 文件，带 YAML frontmatter：
  ```yaml
  ---
  type: user_preference
  title: 简洁回复，不要尾部摘要
  created: 2026-06-01T10:30:00+08:00
  updated: 2026-06-01T10:30:00+08:00
  ---
  用户偏好简洁回复，每次完成后不要在结尾重述刚做了什么。
  ```
- **F29**：笔记分两级存放——项目级 `.endless-code/memory/`，用户级 `~/.config/endless-code/memory/`。项目级笔记记录与当前项目相关的信息（项目知识、参考资料），用户级笔记记录跨项目通用的信息（用户偏好、纠正反馈）。具体分级由 LLM 判断。
- **F30**：每级有一个索引文件 `MEMORY.md`，每行一条笔记摘要。格式：`- [<type>] <title> — <一句话描述>`。索引文件不超过 200 行 / 25KB。超出时由 LLM 在更新时决定合并或淘汰旧条目。
- **F31**：文件名由 LLM 生成，格式为 `<type>_<short_slug>.md`（如 `user_preference_terse_replies.md`、`project_knowledge_api_conventions.md`）。slug 全小写、下划线分隔。

#### 记忆注入- **F32**：启动时和每次笔记更新后，读取两级索引文件的内容拼接（项目级在前、用户级在后），注入系统提示的 `long-term-memory` 模块槽位（priority 100）。
- **F33**：注入内容为索引文件的纯文本，不是笔记全文。模型通过索引了解"记住了什么"，需要详情时可用文件读取工具读取完整笔记文件。
- **F34**：注入前检查拼接后的总大小：超过 25KB 时截断到 25KB 并追加 `(index truncated)` 标注。

#### 记忆更新- **F35**：触发时机：`Agent.run` 完整执行结束后（模型最终回复无工具调用，事件流发出 Done），满足以下任一条件时异步发起记忆更新：① 每 5 轮自动触发（`SessionRuntime.turn_count % 5 == 0`）；② 本轮用户消息包含显式记忆请求关键词（"记住""记忆""别忘""remember""memo"）。两个条件为"或"关系。
- **F36**：异步执行：更新在独立 asyncio task 中运行，不阻塞用户的下一次输入。更新过程中用户可以继续对话。
- **F37**：更新输入：将本轮对话的最近消息（从最后一条 user 消息到最终 assistant 回复）和两级现有索引内容打包成一个记忆更新请求，发送给当前会话的同一个 provider。
- **F38**：更新请求不传工具定义（与摘要请求类似），模型不允许调用工具。
- **F39**：LLM 返回结构化 JSON 数组，每个元素描述一个操作：
  ```json
  [
    {"action":"create","level":"project","type":"project_knowledge","title":"...","slug":"...","content":"..."},
    {"action":"update","level":"user","filename":"user_preference_terse_replies.md","title":"...","content":"..."},
    {"action":"delete","level":"project","filename":"project_knowledge_old_api.md"}
  ]
  ```
  返回空数组 `[]` 表示无需更新。
- **F40**：执行操作：create 时创建新文件并在索引中追加一行；update 时重写文件内容和 frontmatter 并更新索引中对应行；delete 时删除文件并移除索引中对应行。所有文件操作发生在对应级别的 memory 目录下。
- **F41**：去重完全交给 LLM 判断：更新请求中包含完整索引，LLM 自行判断是否已有相似笔记需要合并或跳过。
- **F42**：更新失败（LLM 错误、JSON 解析失败、文件写入失败）静默记录日志，不影响主会话。不做重试。

### 集成与生命周期- **F43**：`build_system_prompt(instructions: str, memory: str)` 接受两个新参数：非空时分别填入 `custom-instructions`（priority 80）和 `long-term-memory`（priority 100）模块的 content。
- **F44**：`Conversation` 构造时接受可选的 `on_append` 和 `on_replace` 回调。`on_append(msg: llm.Message)` 在每次追加消息后调用；`on_replace(msgs: list[llm.Message])` 在整体替换后调用。回调由 session Writer 实现。未设置回调时行为与现有完全一致。
- **F45**：`cli.py` 启动流程新增步骤（在现有步骤之间插入）：① 加载项目指令 → ② 初始化记忆管理器并加载索引 → ③ 后台启动会话清理 → ④ 将指令文本和记忆文本传入 TUI 用于系统提示组装。
- **F46**：`/resume` 与 Agent 主循环互斥：`RESUMING` 期间不允许发起新的 `Agent.run`；`Agent.run` 期间不响应 `/resume`（返回提示"请等待当前任务完成"）。
- **F47**：记忆更新与 `/compact` 可并发：记忆更新只读 conversation 快照、只写 memory 目录，不修改 conversation 本身，与压缩操作无冲突。

## 非功能需求- **N1（性能）**：项目指令加载（含 @include 展开）必须在 200ms 内完成。JSONL 单次 append（序列化 + 写入 + fsync）不超过 10ms。会话列表扫描（读首行提取标题）50 个会话不超过 500ms。
- **N2（并发安全）**：session Writer 的 append 在主循环和 TUI 路径并发调用时无竞态。记忆更新的文件写操作（memory 目录）用锁保护，防止两次连续更新的读-写冲突。
- **N3（向后兼容）**：没有 ENDLESSCODE.md 的项目、没有 memory 目录的项目、旧格式 session ID 的会话目录，都不影响启动和运行。旧 session ID 格式的目录在 `/resume` 列表中不展示（无法解析时间戳），也不被自动清理（避免误删）。
- **N4（可测性）**：@include 展开、JSONL 解析与恢复、记忆索引拼接、会话列表构建等核心逻辑可脱离真实 provider 单元测试。记忆更新的 LLM 调用通过 provider 接口可 mock。
- **N5（错误隔离）**：指令文件加载失败（权限、格式）降级为空指令，不阻塞启动。JSONL 写入失败记录日志但不中断对话。记忆更新失败静默跳过。会话恢复中的任何单点错误（坏行、孤立调用、压缩失败）都有对应降级策略，不让一个错误拖垮整个恢复流程。

## 不做的事- **不做向量数据库或 RAG 检索**：记忆索引直接注入上下文，约 2-3K tokens，不需要语义检索。
- **不做团队记忆同步**：笔记只在本机存储和读取，不做多人协作同步。
- **不做启动时自动恢复最近会话**：启动永远开新会话，只能通过 `/resume` 手动恢复。
- **不做会话合并**：每个会话独立存档，不支持合并两个会话的历史。
- **不做记忆质量反馈优化**：记忆更新 prompt 固定，不做 A/B 测试或用户评分回流。
- **不做指令文件热更新**：进程启动时加载一次，运行期间不监听文件变化。
- **不做笔记全文搜索**：模型通过索引感知记忆概况，需要详情时用文件读取工具按路径读取。
- **不清理旧格式 session ID 的目录**：只清理能解析出时间戳的新格式目录，避免误删 ch08 遗留数据。

## 验收标准

### 项目指令

- **AC1（三层加载）**：在三个路径各放一份 ENDLESSCODE.md → 系统提示的 custom-instructions 模块中包含三份内容，项目根的在最前面。
- **AC2（缺失文件静默）**：只在项目根放 ENDLESSCODE.md，其余两个路径无文件 → 加载成功，只包含项目根的内容。
- **AC3（@include 展开）**：ENDLESSCODE.md 中写 `@include rules/style.md` → 对应文件内容替换该行。
- **AC4（嵌套深度）**：构造 6 层嵌套的 @include 链 → 第 6 层不展开，出现深度警告注释。
- **AC5（环路检测）**：A include B、B include A → 第二次 include 不展开，出现环路警告注释。
- **AC6（路径逃逸）**：项目级 ENDLESSCODE.md 中 `@include ../../etc/passwd` → 不加载，出现范围警告注释。

### 会话存档

- **AC7（Session ID 格式）**：启动进程 → session ID 形如 `20260601-143022-a1b2`，`.endless-code/sessions/` 下能找到对应目录。
- **AC8（JSONL 写入）**：发送一条消息、得到回复 → `conversation.jsonl` 包含至少两行（user + assistant），每行可解析为合法 JSON，包含 role、content、ts 字段。第一行额外包含 model 字段。
- **AC9（压缩标记）**：触发一次压缩 → JSONL 中出现 `{"type":"compact","ts":...}` 标记行，其后跟压缩后的消息。
- **AC10（崩溃安全）**：模拟 Writer 写入中途被 kill → 重新打开 JSONL，除最后一行可能不完整外，之前的行全部可正常解析。

### 会话恢复- **AC11（/resume 路由）**：在 TUI 输入 `/resume` → 不发送给 LLM，进入会话选择列表；输入 Esc → 返回空闲态。
- **AC12（列表展示）**：存在 3 个有效会话 → 列表展示 3 项，每项有标题、相对时间、模型标签、文件大小。
- **AC13（搜索过滤）**：在列表中输入搜索关键词 → 列表只展示标题匹配的会话。
- **AC14（坏行跳过）**：在 JSONL 中手动插入一行无效 JSON → 恢复时该行被跳过，其余消息正常加载。
- **AC15（孤立工具调用截断）**：JSONL 最后是一条带 tool_calls 的 assistant 消息、没有后续 tool 消息 → 恢复时该条被截断，conversation 以上一条完整消息结尾。
- **AC16（Token 超限压缩）**：构造一个 JSONL 使加载后估算 token 超过阈值 → 恢复过程中自动执行一次压缩后再进入空闲态。
- **AC17（时间跨度提醒）**：恢复一个最后消息 ts 距当前超过 6 小时的会话 → conversation 末尾追加时间跨度提醒消息。
- **AC18（追加写入）**：恢复后发送新消息 → 新消息追加到同一个 JSONL 文件，行号递增。

### 会话清理- **AC19（过期清理）**：手动创建一个 31 天前时间戳的 session 目录 → 启动进程后该目录被删除。
- **AC20（新格式保护）**：手动创建一个旧格式 session ID（如 `1717000000-abc12345`）的目录 → 启动后不被删除也不在 /resume 列表中出现。

### 自动笔记

- **AC21（笔记创建）**：在对话中明确表达一个偏好（如"回复简洁点"）→ Agent 回复后，`.endless-code/memory/` 或 `~/.config/endless-code/memory/` 下出现对应类型的 .md 文件，frontmatter 包含 type、title、created。
- **AC22（索引更新）**：创建一条笔记后 → 对应级别的 `MEMORY.md` 中出现该笔记的摘要行。
- **AC23（记忆注入）**：MEMORY.md 有内容时启动新会话 → 系统提示的 long-term-memory 模块包含索引内容。
- **AC24（异步不阻塞）**：记忆更新正在执行时用户发送下一条消息 → 消息立即被处理，不等待记忆更新完成。
- **AC25（更新失败静默）**：mock provider 对记忆更新请求返回错误 → 主会话不受影响，日志记录错误。
- **AC26（索引大小限制）**：构造一个超过 25KB 的索引文件 → 注入系统提示时被截断到 25KB 并出现 truncated 标注。

### 集成- **AC27（build_system_prompt 参数化）**：传入非空 instructions 和 memory → 系统提示中 custom-instructions 和 long-term-memory 模块都有内容且按正确优先级排列。传入空字符串 → 对应模块被跳过，与 ch08 行为一致。
- **AC28（Conversation 回调）**：设置 on_append 和 on_replace 回调后，add_user/add_assistant/add_tool_results/replace_history 各调用一次 → 回调被触发的次数和参数正确。未设置回调 → 行为与 ch08 完全一致。
- **AC29（互斥）**：`Agent.run` 执行期间输入 `/resume` → 返回提示信息，不进入列表。`RESUMING` 期间不允许发起新的 run。
