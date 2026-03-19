# Agent Loop Checklist

## 功能与停止条件

- [ ] 多轮自动推进：需要第二步依赖第一步工具结果的任务，无需用户中途追加消息即可完成并给出最终文本。（验证：运行 `python -m pytest tests/test_agent.py -q -k "multi_round"`，断言 Provider 至少收到两次请求、工具结果进入下一轮、最终只有一个 done；对应 AC1/F1）
- [ ] 纯文本自然完成：首轮没有工具调用时立即停止，不产生额外模型请求。（验证：运行 `python -m pytest tests/test_agent.py -q -k "natural"`，断言 Provider 调用次数为 1；对应 AC2/F2）
- [ ] 迭代上限：模型持续请求工具时只运行 25 轮，展示上限原因且会话可继续。（验证：运行 `python -m pytest tests/test_agent.py -q -k "max_iterations"`，断言调用次数为 25、notice 文案匹配、随后纯文本回合成功；对应 AC3/F2）
- [ ] 连续未知工具停止：连续三轮全部调用未知工具时停止；中间出现已注册工具时计数重置。（验证：运行 `python -m pytest tests/test_agent.py -q -k "unknown"`，两条分支均通过；对应 AC4/F2）
- [ ] 模型流错误恢复：当前回合显示错误并结束，程序不退出，下一回合正常完成。（验证：运行 `python -m pytest tests/test_agent.py tests/test_tui.py -q -k "stream_error"`，断言 err 后有且只有一个 done、TUI 回到 IDLE；对应 AC5/F2）
- [ ] Agent 事件完备：一次多轮工具任务可观察到文本、工具开始/结束、usage、iteration 和 done；停止场景额外有 notice 或 err。（验证：运行 `python -m pytest tests/test_agent.py -q -k "event_sequence"`，断言事件类型集合和顺序；对应 AC6/F3）
- [ ] 流式文本与工具参数双路收集：文本分片实时按序出现，分片工具 JSON 最终完整可解析。（验证：运行 `python -m pytest tests/test_llm.py tests/test_agent.py -q -k "fragment or stream_events"`，断言完整 ToolCall 输入和逐片 text 事件；对应 AC7/F4）
- [ ] 保序分批执行：`只读, 只读, 有副作用, 只读` 中前两个并发，后两段按边界执行，结果顺序不变。（验证：运行 `python -m pytest tests/test_agent.py -q -k "batch"`，断言并发峰值至少为 2、写工具开始晚于前批结束、ToolResult ID 顺序等于调用顺序；对应 AC8/F5）
- [ ] 历史在所有停止路径下合法：自然完成、上限、未知工具、流错误和取消均无悬空工具调用，随后可继续对话。（验证：运行 `python -m pytest tests/test_agent.py -q -k "history"`，逐条比对 assistant tool calls 与 tool result ID，并运行下一回合；对应 AC9/F6）
- [ ] 用户取消键行为：STREAMING 下 Esc 和 Ctrl+C 只取消当前回合并回到 IDLE；IDLE 下 Ctrl+C 退出。（验证：运行 `python -m pytest tests/test_tui.py -q -k "escape or ctrl_c or cancel"`，断言应用存活/退出状态分别正确；对应 AC10/F7）
- [ ] 会话用量累计：每个 usage 事件只累计一次，输入/输出 Token 随模型请求增长并显示。（验证：运行 `python -m pytest tests/test_tui.py -q -k "usage"`，注入两轮已知用量并断言状态文本为精确总和；对应 AC11/F8）
- [ ] 迭代进度：多轮任务从第 1 轮开始递增展示，回合结束后动态轮次清零。（验证：运行 `python -m pytest tests/test_tui.py -q -k "iteration"`，观察测试断言 `1 -> 2 -> 0`；对应 AC12/F9）
- [ ] Plan Mode 只提供只读工具：`/plan` 后模型只收到 read_file、glob、grep 和计划提示。（验证：运行 `python -m pytest tests/test_agent.py tests/test_tui.py -q -k "plan_mode or plan_command"`，断言工具名称、system suffix 和模式状态；对应 AC13/F10）
- [ ] `/do` 恢复执行：`/do` 不作为字面历史消息，系统加入执行指令并立即用全部六个工具启动。（验证：运行 `python -m pytest tests/test_tui.py -q -k "do_command"`，断言最后 user 消息为执行指令、Provider 收到六项定义；对应 AC13/F10）
- [ ] DeepSeek/OpenAI 适配行为一致：两种适配器都拼接系统后缀、工具参数、用量并正确关闭流。（验证：运行 `python -m pytest tests/test_llm.py -q`，同一参数化协议场景两路均通过；对应 AC14/F11）
- [ ] TUI 在声明版本内可挂载和退出：界面组件存在，不覆盖 Textual 内部注册表。（验证：当前 8.x 与最低 Textual 临时环境分别运行 `python -m pytest tests/test_tui.py -q -k "mount"`，均退出 0；对应 AC15/N8）
- [ ] 命令非零退出为错误：stdout、stderr、退出码仍可见，但结果和 UI 状态为失败。（验证：运行 `python -m pytest tests/test_tool.py -q -k "nonzero"`，断言 `is_error is True` 和退出码为测试值；对应 AC16/N1）
- [ ] 命令超时终止进程树：收到超时后，子进程不能继续产生延迟副作用。（验证：运行 `python -m pytest tests/test_tool.py -q -k "timeout"`，超时后等待超过子进程计划写入时间，标记文件仍不存在；对应 AC16/N1/N5）
- [ ] 密钥不回显：assistant、工具参数、工具结果和异常中的模拟密钥均从可见输出移除。（验证：运行 `python -m pytest tests/test_security.py tests/test_tui.py -q -k "redact or redaction"`，断言 RichLog 导出文本不包含任一原始密钥；对应 AC17/N7）

## 模块集成

- [ ] Agent 是 assistant/tool 历史的唯一写入者，TUI 不重复追加最终答复。（验证：运行 `python -m pytest tests/test_tui.py -q -k "history"`，纯文本历史角色严格为 `user, assistant`，内容仅出现一次；对应 F6）
- [ ] 多轮工具历史按协议顺序携带：`user -> assistant(tool_calls) -> tool results -> assistant`。（验证：运行 `python -m pytest tests/test_agent.py -q -k "multi_round and history"`，逐项比较消息角色、call ID 和内容；对应 F1/F6）
- [ ] Plan Mode 具有双重限制：系统提示要求只读，实际工具定义也不含写/改/命令。（验证：运行 `python -m pytest tests/test_agent.py -q -k "plan_mode"`，同时断言 suffix 与工具集合；对应 F10）
- [ ] Provider 取消会关闭底层流：无新 chunk 时触发取消也能及时返回。（验证：运行 `python -m pytest tests/test_agent.py tests/test_llm.py -q -k "cancel and stream"`，FakeStream 关闭标记为真且耗时低于测试上限；对应 F7/N5）
- [ ] 工具取消会完成资源回收：阻塞工具 task 被等待，没有 pending task 警告。（验证：运行 `python -m pytest tests/test_agent.py tests/test_tui.py -q -k "cancel" -W error::ResourceWarning`，退出码为 0；对应 F7/N5）
- [ ] 并发批不直接修改 Conversation：批次结束前历史不出现部分 ToolResult，结束后一次性按序写入。（验证：运行 `python -m pytest tests/test_agent.py -q -k "batch_history"`，插桩快照断言通过；对应 F5/F6/N6）
- [ ] 工具结果体量受控：大文件、长输出和超过 100 条的查找/搜索都带截断标记。（验证：运行 `python -m pytest tests/test_tool.py -q -k "truncate or limit"`，分别断言行/字符/条目上限；对应 N4）
- [ ] UI scrollback 顺序稳定：跨轮 preamble、工具行、结果摘要和最终答复按事件顺序出现，并发工具不交错。（验证：运行 `python -m pytest tests/test_tui.py -q -k "render_order"`，比较 RichLog 导出块顺序；对应 N3）
- [ ] UI 在慢流和慢工具期间持续响应：计时器与迭代状态继续变化。（验证：运行 `python -m pytest tests/test_tui.py -q -k "responsive"`，用 Pilot 在阻塞任务期间推进时钟并断言状态更新；对应 N2）
- [ ] Provider 初始化错误留在界面内：缺失环境变量或无效选择不会产生未捕获堆栈。（验证：运行 `python -m pytest tests/test_tui.py -q -k "provider_error"`，断言错误块可见且应用仍运行；对应 N8）

## 工程检查

- [ ] TOML 配置可解析，Textual 依赖范围为 `>=0.52,<9`，pytest 异步 fixture scope 为 function。（验证：运行 T1 的 `python -c` TOML 断言，输出 `T1 PASS`）
- [ ] 所有源码可编译。（验证：运行 `python -m compileall -q src`，退出码为 0）
- [ ] Ruff 格式检查通过。（验证：运行 `python -m ruff format --check .`，输出所有文件已格式化）
- [ ] Ruff 静态检查无告警。（验证：运行 `python -m ruff check .`，输出 `All checks passed!`）
- [ ] 全量自动化测试通过。（验证：运行 `python -m pytest -q`，失败数为 0）
- [ ] 测试无异步/资源清理警告。（验证：运行 `python -m pytest -q -W error::ResourceWarning`，退出码为 0，输出无 pending task、未关闭流或子进程警告）
- [ ] 已安装依赖关系一致。（验证：运行 `python -m pip check`，输出 `No broken requirements found.`）
- [ ] Git diff 无空白错误。（验证：运行 `git diff --check`，无输出且退出码为 0）
- [ ] 没有修改或跟踪真实密钥配置。（验证：运行 `git status --short .endless-code/config.yaml` 无输出；运行 `git check-ignore -v .endless-code/config.yaml` 显示由 `.gitignore` 忽略）
- [ ] README 与实现一致且不含旧项目残留。（验证：运行 `rg -n "Agent Loop|/plan|/do|DeepSeek|OpenAI|security.py" README.md` 有预期命中；运行 `rg -n "Anthropic|mewcode|单轮闭环" README.md` 无命中）

## 端到端

- [ ] OpenAI 多轮场景：启动 TUI，要求读取一个临时源文件并根据内容写入临时摘要文件；无需中途催促即可看到 read_file、write_file、最终答复，摘要内容正确，用量和轮次增长。（验证：使用有效 OpenAI 配置运行 `python -m endless_code`，操作后在另一个终端读取临时摘要并核对；对应 AC1/AC11/AC12/AC14）
- [ ] DeepSeek 多轮场景：使用同一临时输入和任务重跑，工具触发、结果回灌、最终文件、用量与取消行为和 OpenAI 一致。（验证：使用有效 DeepSeek 配置运行 `python -m endless_code`，按同样步骤核对；对应 AC14）
- [ ] Plan -> 执行场景：`/plan` 后要求调研临时输入并规划生成摘要，确认只出现 read/glob/grep；输入 `/do` 后出现写入工具并生成文件。（验证：检查 scrollback 工具名和临时文件内容；对应 AC13）
- [ ] 用户取消场景：发起包含慢命令的多步任务，分别用 Esc 和 Ctrl+C 取消；界面回到空闲、不退出，慢命令没有延迟副作用，随后普通消息正常完成。（验证：观察状态、检查标记文件不存在并继续对话；对应 AC9/AC10/AC16）
- [ ] 流错误恢复场景：使用测试注入的错误 Provider 启动 headless TUI，首轮产生错误、第二轮恢复纯文本。（验证：运行 `python -m pytest tests/test_tui.py -q -k "stream_error_recovery"`，断言错误块、IDLE 状态和第二轮答复；对应 AC5）
- [ ] 密钥可见面场景：使用模拟 key 的测试配置，让 FakeProvider 在文本、工具参数、结果和异常中回传该值。（验证：运行 `python -m pytest tests/test_tui.py -q -k "redaction_e2e"`，导出的完整界面文本无模拟 key；对应 AC17）

## 验收记录模板

```markdown
## 验收报告

### 通过（N/M）
- [x] 条目 — 证据：命令与关键输出

### 未通过
- [ ] 条目 — 预期：...；实际：...；修复：...

### 端到端
- [x] 场景 — Provider：...；结果：...
```
