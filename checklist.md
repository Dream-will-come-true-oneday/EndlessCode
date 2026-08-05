```Markdown
# 项目记忆与会话持久化 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 编译与测试

- [ ] 包可正常导入：`python -c "import endless_code"`（验证：exit code 0）
- [ ] 所有单元测试通过：`pytest`（验证：exit code 0，无 FAIL）
- [ ] lint 无告警：`ruff check src/endless_code tests`（验证：exit code 0）
- [ ] 格式一致：`ruff format --check src/endless_code tests`（验证：exit code 0）
- [ ] 无线程/协程竞态：`pytest tests/test_conversation.py tests/test_session.py tests/test_memory.py`（验证：在测试中显式构造并发场景，断言写入完整无丢失）

## 项目指令文件

- [ ] 三层加载优先级：在三个路径各放不同内容的 MEWCODE.md，启动进程，检查系统提示 custom-instructions 模块中三份内容按项目根 → .endless-code/ → ~/.config/endless-code/ 顺序排列（验证：打断点或加日志观察 `build_system_prompt` 输出）
- [ ] 缺失文件静默：只在项目根放 MEWCODE.md → 加载成功，不报错（验证：启动无错误日志）
- [ ] @include 正常展开：MEWCODE.md 中写 `@include sub/rules.md`，sub/rules.md 存在且有内容 → 内容替换 @include 行（验证：系统提示中出现 rules.md 的内容）
- [ ] @include 嵌套展开：A include B，B include C → A 的输出中包含 C 的内容
- [ ] 5 层深度截断：构造 6 层嵌套链 → 第 6 层不展开，输出中出现深度警告注释（验证：`"超过最大嵌套深度" in output`）
- [ ] 环路检测：A include B、B include A → 第二次引用不展开，出现环路警告（验证：`"检测到环路" in output`）
- [ ] 路径逃逸：项目级 MEWCODE.md 中 `@include ../../outside.md` → 不加载，出现范围警告（验证：`"路径超出允许范围" in output`）
- [ ] 二进制文件跳过：@include 指向一个含 `\x00` 的文件 → 跳过，出现警告（验证：测试用例）
- [ ] 空指令不影响系统提示：三个路径都没有 MEWCODE.md → `build_system_prompt("", memory)` 中 custom-instructions 模块被跳过（验证：系统提示不含空模块）

## 会话存档

- [ ] Session ID 新格式：启动进程，检查 `.endless-code/sessions/` 下目录名形如 `YYYYMMDD-HHMMSS-xxxx`（验证：`ls .endless-code/sessions/`，正则匹配 `\d{8}-\d{6}-[0-9a-f]{4}`）
- [ ] JSONL 首行带 model：发送第一条消息后，读 `conversation.jsonl` 第一行 → 包含 `"model":"<模型名>"` 字段（验证：`head -1 conversation.jsonl | jq .model`）
- [ ] 消息实时追加：发送 "hello" 并等回复 → JSONL 至少两行（user + assistant），每行有 role、content、ts（验证：`wc -l conversation.jsonl` 且 `jq .role` 每行有值）
- [ ] 工具调用记录：触发一次工具调用（如读文件）→ JSONL 中出现 `tool_calls` 和 `tool_results` 字段（验证：`grep tool_calls conversation.jsonl`）
- [ ] 压缩标记写入：触发压缩 → JSONL 中出现 `"type":"compact"` 行，且之后是压缩后的消息（验证：`grep '"type":"compact"' conversation.jsonl`）
- [ ] fsync 刷盘：每次 append 后文件内容可被外部进程读到（验证：另开终端 `tail -f` 观察实时追加）

## 会话恢复

- [ ] /resume 命令路由：输入 `/resume` → 不发送给 LLM，进入会话列表界面（验证：观察 TUI 状态变为列表选择）
- [ ] 列表展示正确：存在 3 个有效会话 → 列表 3 项，每项有标题、时间、模型、大小（验证：观察 TUI 列表渲染）
- [ ] 上下键导航：按上下键 → 高亮项切换（验证：观察 TUI）
- [ ] 搜索过滤：输入关键词 → 列表只展示匹配项（验证：观察 TUI）
- [ ] Esc 取消：列表中按 Esc → 返回空闲态，当前会话不变（验证：观察 TUI 状态）
- [ ] Enter 恢复：选择一个会话按 Enter → 对话历史恢复，显示"已恢复会话"系统消息（验证：观察 TUI）
- [ ] 坏行跳过：手动在 JSONL 中插入 `{invalid json` → /resume 加载该会话时跳过坏行，其余正常（验证：恢复后消息数 = 有效行数）
- [ ] 孤立工具调用截断：JSONL 最后是带 `tool_calls` 的 assistant 消息 → 恢复后该条不在 conversation 中（验证：`conv.last_role()` 不是带 `tool_calls` 的 assistant）
- [ ] Token 超限自动压缩：构造大量消息使加载后 token 超阈值 → 恢复过程中触发压缩，最终 conversation 消息数小于 JSONL 行数（验证：恢复后 `len(conv.messages())` < JSONL 总行数）
- [ ] 时间跨度提醒：恢复一个最后 ts 超过 6 小时的会话 → conversation 末尾出现时间跨度提醒消息（验证：`conv.messages()` 最后一条 content 包含"暂停"）
- [ ] 恢复后追加：恢复会话后发新消息 → 新消息追加到同一 JSONL 文件（验证：`wc -l` 行数递增）
- [ ] 旧格式不展示：存在旧格式 session ID 目录 → /resume 列表不展示（验证：列表中无旧格式项）
- [ ] 运行中不可 resume：`Agent.run` 执行期间输入 /resume → 返回提示信息，不进入列表（验证：观察 TUI 提示）

## 会话清理

- [ ] 过期清理：手动创建时间戳为 31 天前的 session 目录 → 启动进程后被删除（验证：`ls .endless-code/sessions/` 不含该目录）
- [ ] 新会话不被清理：刚创建的 session 目录 → 启动后保留（验证：目录仍存在）
- [ ] 旧格式保留：旧格式 ID 目录 → 启动后不被删除（验证：目录仍存在）
- [ ] 清理不阻塞启动：清理在后台 asyncio task 执行 → 启动流程不等待清理完成（验证：即使有大量过期目录，启动仍秒级完成）

## 自动笔记

- [ ] 显式记忆触发：对话中说"记住 xxx" → Agent 回复后，memory 目录出现新 .md 文件（验证：`ls .endless-code/memory/` 或 `~/.config/endless-code/memory/`）
- [ ] 每 5 轮自动触发：连续对话 5 轮后检查 memory 目录是否有新增（验证：对比前后 `MEMORY.md`）
- [ ] 项目级分类：说"记住这个项目用中文" → 笔记出现在 `.endless-code/memory/`（项目级），type 为 `project_knowledge`（验证：`ls .endless-code/memory/`）
- [ ] 索引更新：创建笔记后 → `MEMORY.md` 中有该笔记摘要行（验证：`cat MEMORY.md`）
- [ ] 记忆注入系统提示：`MEMORY.md` 有内容 → 系统提示 long-term-memory 模块包含索引（验证：打断点或日志观察）
- [ ] 异步不阻塞：记忆更新执行中发送下一条消息 → 消息立即被处理（验证：无感知延迟）
- [ ] 更新失败静默：mock provider 返回错误 → 主会话不中断，日志有错误记录（验证：对话继续正常；检查日志）
- [ ] 索引截断：构造超 25KB 索引 → 注入时截断到 25KB，末尾有 `(index truncated)`（验证：测试用例）
- [ ] 笔记更新：已有笔记后 LLM 判断需要更新 → 文件内容和 `frontmatter.updated` 时间更新（验证：文件内容变化且 updated 时间新于 created）
- [ ] 笔记删除：LLM 判断某条笔记过时 → 文件被删除，`MEMORY.md` 对应行消失（验证：文件不存在且 `MEMORY.md` 行数减少）
- [ ] 无工具请求：记忆更新发给 provider 的请求 → tools 字段为空（验证：mock provider 检查 `req.tools` 为空）

## 集成

- [ ] `build_system_prompt` 向后兼容：传入空字符串 → 输出与 ch08 完全一致（验证：对比 ch08 的 `build_system_prompt()` 输出和新 `build_system_prompt("", "")` 输出，逐字节一致）
- [ ] Conversation 回调向后兼容：使用原 `Conversation()` 构造（不传回调）→ 所有 add/replace 行为不变（验证：ch08 的 conversation 测试全部通过）
- [ ] 完整启动流程：配置好 provider → 启动 → 能看到 banner → 能输入消息 → 能收到回复 → JSONL 有内容 → 退出后 session 目录保留（验证：tmux 手动测试）
- [ ] /resume 完整流程：启动 → 对话几轮 → 退出 → 重新启动 → `/resume` → 选择上次会话 → 恢复成功 → 继续对话 → JSONL 行数递增（验证：tmux 手动测试）

## 端到端场景（tmux 实跑）

- [ ] 场景 1（首次冷启动）：删掉 MEWCODE.md、`.endless-code/memory/`、`~/.config/endless-code/memory/`，运行 `python -m endless_code`；banner 正常显示；输入 "你好" → 模型回复正常；`cat .endless-code/sessions/*/conversation.jsonl` 可见至少两行（user + assistant），每行 `jq .` 能解析；第一行含 `"model"` 字段；退出后 session 目录保留不删。(AC7/AC8/AC27)
- [ ] 场景 2（项目指令生效）：在项目根创建 `MEWCODE.md` 写入 "所有回复必须以「喵~」开头"；启动 endless_code → 输入 "你好" → 模型回复以"喵~"开头（验证指令注入生效）。再在 `~/.config/endless-code/MEWCODE.md` 写入 "回复使用英文"，重启 → 输入 "你好" → 模型以"喵~"开头但用中文（项目级优先级压过用户级）。(AC1/AC2)
- [ ] 场景 3（@include 展开）：项目根 `MEWCODE.md` 写 `@include .endless-code/rules/style.md`；创建 `.endless-code/rules/style.md` 写入 "代码块必须带语言标记"；启动 → 输入 "写一个 hello world" → 模型输出的代码块带语言标记。(AC3)
- [ ] 场景 4（会话存档 + 工具记录）：启动 → 输入 "读取 pyproject.toml" → 模型调 read_file → 回复内容；`cat .endless-code/sessions/*/conversation.jsonl` 至少 4 行（user → assistant+tool_calls → tool_results → assistant）；`grep tool_calls` 和 `grep tool_results` 各至少一条命中。(AC8/AC9)
- [ ] 场景 5（/resume 完整流程）：启动 session A → 输入 "记住：我在写一个电商系统" → 模型回复 → `/exit` 退出；重新启动（新 session B）→ 输入 `/resume` → 列表中出现 session A（标题含"记住"或"电商"）→ 上下键选中 → Enter → TUI 显示"已恢复会话"系统消息 → 输入 "上次说到哪了" → 模型能引用之前的对话内容；`wc -l .endless-code/sessions/<A_id>/conversation.jsonl` 行数比恢复前多（恢复后新消息追加到旧 JSONL）。(AC11/AC12/AC13/AC18)
- [ ] 场景 6（/resume 搜索过滤）：存在多个历史会话 → `/resume` → 输入搜索关键词（如某个会话标题中的词）→ 列表过滤到匹配项；按 Esc → 返回空闲态，当前会话不受影响。(AC13)
- [ ] 场景 7（恢复坏行容错）：手动打开某个 JSONL，在中间插入一行 `{this is bad json`，保存；启动 → `/resume` → 选该会话 → 恢复成功，消息数 = 有效行数（跳过坏行）。(AC14)
- [ ] 场景 8（崩溃恢复）：启动 → 对话 3-4 轮 → 记下 JSONL 行数 N → 另一个 tmux pane 执行 `kill -9 <pid>`；重新启动 → `/resume` → 选崩溃的会话 → JSONL 前 N 行完整可解析（最后一行可能不完整被跳过）→ 恢复后能继续对话。(AC10/AC14)
- [ ] 场景 9（过期清理）：手动创建 `.endless-code/sessions/20260401-120000-dead/conversation.jsonl`（31 天前的时间戳）和一个旧格式目录 `1717000000-abc12345/`；启动 → `ls .endless-code/sessions/` → 31 天前的目录被删，旧格式目录保留，当前新 session 目录存在。(AC19/AC20)
- [ ] 场景 10（记忆积累与注入）：启动 → 输入 "以后回复都用中文简体，不要用繁体" → 模型回复 → 等 2-3 秒（异步记忆更新）→ `ls .endless-code/memory/` 或 `~/.config/endless-code/memory/` 出现 .md 笔记文件；`cat */MEMORY.md` 有对应摘要行。退出 → 重新启动新会话 → 输入 "你好" → 确认模型回复为简体中文（记忆生效）。(AC21/AC22/AC23)
- [ ] 场景 11（运行中不可 resume）：启动 → 输入一个需要较长时间的任务（如 "读取所有 Python 文件"）→ 在模型思考/工具执行期间输入 `/resume` → TUI 显示"请等待当前任务完成"提示，不进入列表。(AC29)
- [ ] 场景 12（压缩后 JSONL 标记）：配置一个小 `context_window`（如 2000）→ 对话数轮触发自动压缩 → `grep '"type":"compact"' .endless-code/sessions/*/conversation.jsonl` 至少命中一次；`/exit` → 重启 → `/resume` 恢复该会话 → 消息数远小于 JSONL 总行数（从 compact 标记后加载）。(AC9/AC16)
```
