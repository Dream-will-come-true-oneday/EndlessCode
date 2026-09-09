# 会话内模型切换与输出样式 Checklist

> 依据：已批准的 `spec.md`（AC1–AC9）、`plan.md`、`task.md`。
> 所有自动化命令使用 `F:\anaconda3\python.exe`（本机 `python` 为 WindowsApps 存根，退出码 9009）；CI 等价命令为 `python -m ...`。
> 手工场景启动方式：`F:\anaconda3\python.exe -m endless_code`（或已安装的 `endless-code`），需 `.endless-code/config.yaml` 中至少配置两个 provider。

## 功能与集成

### 模型切换（F1–F4）

- [ ] **C1 模型清单**（AC1）：空闲态执行 `/model` 不带参数，列出全部已配置模型，每行含编号、名称、模型标识，当前使用项带「（当前）」标记。（验证：自动化 `pytest tests/test_command_builtin.py -q` 中 `/model` 列表用例通过；手工执行 `/model`，看到两行以上候选且恰好一行带「（当前）」）
- [ ] **C2 切换成功且上下文保留**（AC1）：执行 `/model 2`（或 `/model <名称>`）后状态栏与 `/status` 显示新模型，此前聊天内容仍在界面上，消息数不归零。（验证：`pytest tests/test_tui.py -q` 中「切换保留对话」用例通过，断言 `_conv.length()`、`session_id`、`writer.path` 三者不变；手工：提问一轮 → `/model 2` → `/status`，看到模型已变而「消息数」保持原值）
- [ ] **C3 编号与名称两种选择器**（AC1）：`/model 2` 与 `/model deepseek` 都能命中同一目标；`/model nope` 报「未找到模型」并提示查看列表，当前模型不变。（验证：`pytest tests/test_tui.py -q` 中选择器用例通过；手工分别执行三条命令，看到前两条成功提示、第三条红色错误提示且状态栏模型未变）
- [ ] **C4 失败原子性**（AC2）：目标模型密钥缺失或解析失败时给出可读原因，原模型、对话历史与会话状态完全不变，且随后仍能正常对话。（验证：清空目标 provider 的密钥环境变量后执行 `/model 2`，看到含「API key 不可用」的错误提示，状态栏仍是原模型；再发一条消息，看到正常回复且消息数继续累加。自动化：`pytest tests/test_tui.py -q` 中失败原子性用例通过）
- [ ] **C5 小窗口触发既有自动压缩**（AC3）：切换到上下文窗口更小的模型后继续对话，超过新窗口阈值时出现既有压缩提示（「正在压缩上下文...」/「已压缩，token 从 X 降至 Y」），而不是请求失败。（验证：把目标 provider 的 `context_window` 配成较小值（如 8000），切换后连续对话至超限，看到压缩提示且对话可继续；自动化：切换后 `runtime.context_window` 等于新配置值、`usage_anchor` 归零）
- [ ] **C6 忙碌保护**（AC4）：一轮任务进行中（模型输出、工具执行、审批等待、恢复/回滚选择态）执行 `/model` 或 `/style` 均被拒绝并提示「当前任务尚未结束」，本轮结束后模型与样式均未变化。（验证：发一条会触发长任务的消息，在流式输出期间执行 `/model 2`，看到拒绝提示；本轮结束后 `/status` 显示模型与样式仍为原值。自动化：`pytest tests/test_tui.py -q` 中两条忙碌保护用例通过）
- [ ] **C7 切换后能力一致**（F3）：切换后对话请求、上下文压缩、长期记忆更新全部使用新模型，`/compact`、`/rewind`、`/audit`、`/memory` 仍正常工作。（验证：切换模型后依次执行 `/compact`（看到压缩提示）、`/rewind`（看到检查点列表或「尚无 checkpoint」而非报错）、`/audit`（看到审计事件列表）、`/memory`（看到索引或「未配置」提示），四者均无异常）

### 输出样式（F5、F6）

- [ ] **C8 样式清单**（AC5）：`/style` 不带参数时首行显示当前样式，其后列出四个预设（default/concise/explanatory/learning）及各自一句话说明，当前项带「（当前）」。（验证：手工执行 `/style`，看到 5 行输出且恰好一行带「（当前）」；自动化：`pytest tests/test_command_builtin.py -q` 中样式列表用例通过）
- [ ] **C9 样式切换生效**（AC5、AC6）：`/style concise` 后下一轮回答明显更短且不含额外原理说明；`/style explanatory` 后下一轮回答附带原理与被否决方案说明。（验证：同一问题分别在三种样式下提问，对比回答详略；自动化：`pytest tests/test_agent.py -q` 断言新式 provider 收到的 `request.system.stable` 在 concise/explanatory 下含样式正文、默认下不含）
- [ ] **C10 未知样式名**（AC5）：`/style nope` 报错并列出全部可选值，当前样式保持不变。（验证：执行 `/style nope`，看到含「未知输出样式」与 `explanatory` 的错误提示；再执行 `/status`，看到「输出样式」仍是原值）
- [ ] **C11 历史不受影响**（AC6）：样式切换不改写已产生的历史消息，消息数与已显示内容不变。（验证：记录 `/status` 的消息数 → `/style explanatory` → 再看 `/status`，消息数相同且聊天区已有内容未变；自动化：`pytest tests/test_tui.py -q` 断言切换前后 `_conv.length()` 相等）
- [ ] **C12 默认样式零变化**（N1、N3）：未切换样式时系统提示与改动前逐字节相同，不打破既有提示缓存。（验证：`F:\anaconda3\python.exe -c "from endless_code.prompt import SYSTEM_PROMPT; from endless_code.prompt.modules import build_system_prompt as b; print(b()==b('','','default')==b('','','nope')==SYSTEM_PROMPT, 'precedence' not in SYSTEM_PROMPT)"`，预期输出 `True True`）

### 状态可见与可追溯（F7）

- [ ] **C13 `/status` 三项齐全**（AC7）：输出同时包含当前模型、输出样式与上下文窗口。（验证：执行 `/status`，看到「模型：」「输出样式：」「上下文窗口：」三行且值与实际一致；切换样式或模型后重复执行，看到对应行随之变化）
- [ ] **C14 状态栏同步**（AC7）：状态栏一行内可见权限模式、provider、模型、样式标签与上下文窗口。（验证：切换模型与样式后观察窗口顶部副标题，看到样式标签（如「简洁」）与窗口数值同步更新，且首段仍是权限模式名）
- [ ] **C15 会话存档留痕**（AC7）：模型切换与样式切换都在会话存档中留下可追溯记录，且不含密钥。（验证：切换各一次后打开该会话目录的 `conversation.jsonl`，看到 `{"type":"model_switch",...,"model":"<新>","previous":"<旧>"}` 与 `{"type":"style_switch",...,"style":"<名>"}` 各一行，且全文搜索不到 API key 明文）
- [ ] **C16 恢复列表显示最近模型**（AC7）：`/resume` 的会话列表展示该会话最近使用的模型而非最初使用的模型。（验证：在会话中切换模型后执行 `/resume`，看到该会话行的模型为新模型；自动化：`pytest tests/test_session.py -q` 中「最近模型」用例通过）
- [ ] **C17 标记不破坏恢复**（N1）：带切换标记的会话仍可被正常恢复，历史消息完整且标记本身不进入对话。（验证：切换模型与样式后退出程序，重启执行 `/resume` 选中该会话，看到「已恢复会话 <id>，共 N 条消息」且 N 等于切换前消息数，聊天区可继续对话；自动化：`pytest tests/test_session.py -q` 断言 `load_session` 只返回 role 消息）

### 命令可发现（F8）

- [ ] **C18 帮助与建议面板**（AC9）：`/help` 列出 `/model` 与 `/style`（含别名与说明）；输入 `/mod`、`/sty` 时建议面板出现对应候选，↑↓ 可选、Enter 执行；Tab 补全可补出命令名。（验证：手工依次执行 `/help`、输入 `/mod` 观察面板、按 Enter 看到 `/model` 列表被执行、输入 `/sty` 后按 Tab 看到补全为 `/style `；自动化：`pytest tests/test_command_builtin.py -q` 断言 `suggest('/mod')` 与 `suggest('/sty')` 各返回对应命令）

### 架构与集成约束（来自 plan.md）

- [ ] **C19 提示层依赖单向无环**：样式注册表不反向依赖系统提示组装模块，单独导入不会拉起它。（验证：`F:\anaconda3\python.exe -c "import sys, endless_code.prompt.style; print('endless_code.prompt.modules' in sys.modules)"`，预期输出 `False`）
- [ ] **C20 切换不新建会话**（N2）：切换模型不新建会话目录、不清空历史与审计、不重置权限规则与权限模式。（验证：切换前后对比 `/status` 的「会话标识」相同、`/audit` 仍能看到切换前的审计事件、权限模式标签未变；自动化：`pytest tests/test_tui.py -q` 断言 `session_id`、`writer.path`、`mode` 三者不变）
- [ ] **C21 权限链不受切换影响**（AC8、N4）：切换到任一其它模型后，危险命令与项目外路径写入仍被拦截，人在回路审批仍正常弹出，提示文案与原模型一致。（验证：切换模型后让 AI 执行 `rm -rf /` 类命令与写 `../outside.txt`，看到「命中危险命令黑名单」与「路径在项目之外」的拒绝提示；在 DEFAULT 模式下让 AI 改一个项目内文件，看到审批面板含风险/目标/规则来源/影响与变更预览。自动化：`pytest tests/test_permission.py tests/test_tui.py -q` 全绿）
- [ ] **C22 错误提示脱敏**（N4）：切换失败原因与会话存档记录均不泄露密钥。（验证：用含真实密钥前缀的配置触发一次失败切换，检查聊天区错误文本与 `conversation.jsonl`，看到密钥被替换为 `[REDACTED]` 或完全不出现）
- [ ] **C23 host 接口完整实现**：命令层新增的四项 host 能力在 TUI 中全部实现，命令层测试无需渲染框架即可运行。（验证：`pytest tests/test_command_builtin.py -q` 通过，且该文件不导入 textual；`F:\anaconda3\python.exe -c "from endless_code.tui.app import EndlessCodeApp as A; print(all(hasattr(A,n) for n in ['get_model_options','switch_model','get_style_options','set_output_style']))"`，预期 `True`）

## 工程检查

- [ ] **C24 格式检查通过**（验证：`F:\anaconda3\python.exe -m ruff format --check src tests`，预期末行 `N files already formatted`，退出码 0）
- [ ] **C25 lint 通过**（验证：`F:\anaconda3\python.exe -m ruff check src tests`，预期 `All checks passed!`，退出码 0）
- [ ] **C26 编译通过**（验证：`F:\anaconda3\python.exe -m compileall -q src examples`，预期无输出，退出码 0）
- [ ] **C27 单元测试全绿且零回归**（AC9、N1）（验证：`F:\anaconda3\python.exe -m pytest tests -q`，预期 `0 failed`、passed 数 ≥ 278 + 本项目新增用例数、skipped 保持 1；基线为 278 passed / 1 skipped）
- [ ] **C28 无新增依赖与配置面变更**（验证：`git diff --stat` 不含 `pyproject.toml`；`.endless-code/config.yaml.example` 未被修改——本项目不引入任何新配置项）
- [ ] **C29 范围外既有噪声未被触碰**（验证：`git status` 显示 `test_modifications.py`、`closure_demo.py`、`CHANGES_SUMMARY.md` 均无改动；`F:\anaconda3\python.exe -m pytest tests -q` 不包含仓库根脚本）

## 端到端

- [ ] **E2E-1 完整工作流（主场景）**：一次会话内完成「选模型 → 对话 → 换模型 → 继续对话 → 换样式 → 再对话 → 查状态 → 退出 → 恢复」。
  验证步骤与预期：
  1. 启动 `F:\anaconda3\python.exe -m endless_code`，配置含两个 provider → 看到选择列表，输入 `1` → 状态栏显示模型 A，进入空闲态
  2. 提问「读一下 README 的第一行」→ 看到工具调用与回复，`/status` 消息数 > 0
  3. `/model` → 看到两项且 A 带「（当前）」；`/model 2` → 看到「已切换到 B（<模型名>）。」，状态栏变为 B，聊天区历史仍在，`/status` 消息数未归零
  4. 再提问同一问题 → 看到由 B 产生的回复（`/status` 的 provider/模型为 B，token 计数继续累加）
  5. `/style` → 看到四个预设且 default 带「（当前）」；`/style explanatory` → 看到「已切换到 讲解 样式，下一轮回答生效。」，状态栏出现「讲解」
  6. 提问「为什么这样改」→ 看到回答附带原理与权衡说明
  7. `/status` → 看到模型 B、「输出样式：讲解」、「上下文窗口：<B 的窗口>」三项同时正确
  8. `/exit` 退出 → 打开该会话 `conversation.jsonl`，看到 `model_switch` 与 `style_switch` 标记各至少一行
  9. 重启程序 → `/resume` → 看到该会话行的模型为 **B**（最近使用），选中恢复 → 看到「已恢复会话 <id>，共 N 条消息」，N 与退出前一致
  10. 恢复后继续提问 → 看到正常回复（标记未混入对话，历史未被破坏）
- [ ] **E2E-2 边界与失败路径**：非法输入与不可用目标全部得到可读提示且状态零变更。
  验证步骤与预期：
  1. `/model nope` → 看到「未找到模型：nope。输入 /model 查看可用列表。」，状态栏模型不变
  2. `/model 99`（越界编号）→ 同上「未找到模型」提示，状态不变
  3. 清空模型 B 的密钥环境变量后 `/model 2` → 看到「API key 不可用」类错误，状态栏仍为 A，随后提问仍由 A 正常回复
  4. `/style nope` → 看到「未知输出样式：nope。可选：default / concise / explanatory / learning」，`/status` 的样式仍为原值
  5. 发一条长任务消息，在流式输出期间执行 `/model 2` 与 `/style concise` → 两次都看到「当前任务尚未结束」提示；本轮结束后 `/status` 显示模型与样式均未变化
  6. `/model`（当前已是唯一目标时）→ 看到「当前已在使用 …」而非报错，状态不变
- [ ] **E2E-3 安全一致性**：换模型后权限五层拦截与审计行为不变。
  验证步骤与预期：
  1. 切换到模型 B，在 BYPASS 之外的 DEFAULT 模式下要求 AI 执行 `rm -rf /` → 看到「命中危险命令黑名单」拒绝提示，命令未执行
  2. 要求 AI 写入项目外路径（如 `../outside.txt`）→ 看到「路径在项目之外」拒绝提示
  3. 要求 AI 修改项目内一个文件 → 看到审批面板（风险/目标/规则来源/影响 + 变更预览），选「拒绝本次」→ 看到文件未被修改
  4. `/audit` → 看到上述三次事件的 `permission_checked` / `approval_responded` 记录，`target` 与 `decision` 正确，且不含密钥明文
  5. `/permissions plan` 后要求 AI 改文件 → 看到只读工具集下无法写入（与切换前行为一致）

## 覆盖对照

| spec 验收标准 | 对应检查项 |
|---|---|
| AC1 模型清单与切换保留上下文 | C1、C2、C3、E2E-1 步骤 3 |
| AC2 密钥缺失时原子失败 | C4、E2E-2 步骤 3 |
| AC3 小窗口触发既有自动压缩 | C5 |
| AC4 忙碌态拒绝 | C6、E2E-2 步骤 5 |
| AC5 样式清单/切换/未知值 | C8、C9、C10 |
| AC6 历史不变且讲解样式生效 | C9、C11 |
| AC7 状态与存档可追溯 | C13、C14、C15、C16、C17 |
| AC8 换模型后权限拦截一致 | C21、E2E-3 |
| AC9 建议面板与零回归 | C18、C27 |
| N1 零回归 | C12、C17、C27 |
| N2 切换无副作用 | C20 |
| N3 缓存友好 | C12 |
| N4 安全一致 | C21、C22 |
| F3 切换后一致性 | C5、C7、C20 |
| F8 命令可发现 | C18、C23 |
