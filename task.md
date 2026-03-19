# Agent Loop Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `pyproject.toml` | Textual 版本边界与 pytest 异步配置 |
| 修改 | `README.md` | Agent Loop、Plan Mode、取消和用量说明 |
| 修改 | `src/endless_code/conversation.py` | 末尾角色查询 |
| 修改 | `src/endless_code/prompt.py` | 循环约定、Plan Mode 和执行提示 |
| 新建 | `src/endless_code/security.py` | 密钥脱敏和工具参数摘要 |
| 修改 | `src/endless_code/llm/__init__.py` | Usage、流事件和 Provider 接口 |
| 修改 | `src/endless_code/llm/openai_provider.py` | 系统后缀、流式用量、可关闭响应 |
| 修改 | `src/endless_code/llm/deepseek_provider.py` | DeepSeek 系统后缀、流式用量、thinking 保持 |
| 修改 | `src/endless_code/tool/__init__.py` | 只读分类接口和注册中心查询 |
| 修改 | `src/endless_code/tool/read_file.py` | 标记只读 |
| 修改 | `src/endless_code/tool/write_file.py` | 标记有副作用 |
| 修改 | `src/endless_code/tool/edit_file.py` | 标记有副作用 |
| 修改 | `src/endless_code/tool/glob_tool.py` | 标记只读 |
| 修改 | `src/endless_code/tool/grep_tool.py` | 标记只读 |
| 修改 | `src/endless_code/tool/bash.py` | 标记有副作用、非零退出、进程树清理 |
| 重写 | `src/endless_code/agent/__init__.py` | ReAct 循环、事件、停止、取消和批次执行 |
| 修改 | `src/endless_code/tui/app.py` | Textual 兼容、模式、状态、渲染、取消和脱敏 |
| 新建 | `tests/test_conversation.py` | Conversation 末尾角色测试 |
| 新建 | `tests/test_security.py` | 脱敏和参数摘要测试 |
| 新建 | `tests/test_llm.py` | 两个 Provider 的分片、后缀和用量测试 |
| 新建 | `tests/test_tui.py` | TUI 挂载、模式、历史、渲染和取消测试 |
| 修改 | `tests/test_tool.py` | 只读分类、非零退出和跨平台超时测试 |
| 重写 | `tests/test_agent.py` | 多轮、停止、并发、取消和历史测试 |

## T1: 固定依赖边界与 pytest 异步配置

**文件：** `pyproject.toml`  
**依赖：** 无

**步骤：**

1. 将 Textual 依赖改为 `textual>=0.52,<9`，其余运行时依赖不变。
2. 在 pytest 配置中设置 `asyncio_default_fixture_loop_scope = "function"`。
3. 使用 TOML 解析器读取文件，确认依赖和配置值类型正确。

**验证：** 运行 `python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); assert 'textual>=0.52,<9' in d['project']['dependencies']; assert d['tool']['pytest']['ini_options']['asyncio_default_fixture_loop_scope']=='function'; print('T1 PASS')"`，预期输出 `T1 PASS`。

## T2: 增加循环提示与 Conversation 末尾角色

**文件：** `src/endless_code/prompt.py`、`src/endless_code/conversation.py`、`tests/test_conversation.py`  
**依赖：** 无

**步骤：**

1. 在 `SYSTEM_PROMPT` 增加“持续使用工具直到任务完成再给最终答复”的约定。
2. 新增 `PLAN_MODE_REMINDER` 和 `EXECUTE_DIRECTIVE`，文案与 `plan.md` 一致。
3. 为 `Conversation` 增加 `last_role()`；空历史返回空字符串，否则返回最后消息角色。
4. 新建测试，依次覆盖空历史、user、assistant-with-tool-calls、tool results 和普通 assistant 尾部。

**验证：** 运行 `python -m pytest tests/test_conversation.py -q`，预期全部通过；运行 `python -c "from endless_code.prompt import PLAN_MODE_REMINDER, EXECUTE_DIRECTIVE; assert '/do' in PLAN_MODE_REMINDER; print(EXECUTE_DIRECTIVE)"`，预期输出执行提示。

## T3: 实现安全输出与工具参数摘要

**文件：** `src/endless_code/security.py`、`tests/test_security.py`  
**依赖：** 无

**步骤：**

1. 实现 `redact_sensitive(text, secrets=())`，覆盖确切密钥、常见 `sk-...` 令牌和 `api_key` 赋值形式。
2. 对空字符串、短秘密值和重复秘密值做稳定处理，不因 `None` 或非字符串输出崩溃。
3. 实现 `summarize_tool_args(tool_name, raw_args, secrets=())`：文件工具显示路径；写/改工具只显示内容长度；搜索工具显示模式和路径；bash 显示脱敏命令；未知工具或非法 JSON 返回脱敏截断摘要。
4. 测试确切密钥、模拟 OpenAI/DeepSeek key、write/edit 内容不回显、bash 命令脱敏和非法 JSON。

**验证：** 运行 `python -m pytest tests/test_security.py -q`，预期全部通过，测试输出中不出现测试密钥原文。

## T4: 为工具和注册中心增加只读分类

**文件：** `src/endless_code/tool/__init__.py`、`src/endless_code/tool/read_file.py`、`src/endless_code/tool/write_file.py`、`src/endless_code/tool/edit_file.py`、`src/endless_code/tool/glob_tool.py`、`src/endless_code/tool/grep_tool.py`、`src/endless_code/tool/bash.py`、`tests/test_tool.py`  
**依赖：** 无

**步骤：**

1. 在 `Tool` Protocol 增加 `read_only: bool`。
2. 将 `read_file/glob/grep` 标记为只读，将 `write_file/edit_file/bash` 标记为有副作用。
3. 为 Registry 增加 `read_only_definitions()`，按注册顺序返回只读定义。
4. 为 Registry 增加 `is_read_only(name)`；未知工具返回 `False`。
5. 扩展注册中心测试，断言完整定义顺序、只读定义顺序和已知/未知分类。

**验证：** 运行 `python -m pytest tests/test_tool.py -q -k "Registry"`，预期注册中心相关测试全部通过，并看到只读名称严格为 `read_file, glob, grep`。

## T5: 修复 bash 非零退出与跨平台进程树清理

**文件：** `src/endless_code/tool/bash.py`、`tests/test_tool.py`  
**依赖：** T4

**步骤：**

1. 创建 shell 子进程时按平台创建独立 session 或进程组。
2. 增加私有清理函数：POSIX 终止进程组，Windows 终止 shell 进程树；终止后等待进程回收，并为温和终止失败保留强制结束分支。
3. 在 `execute` 捕获 `asyncio.CancelledError`，完成进程树清理后重新抛出。
4. 根据 `proc.returncode != 0` 设置 `Result.is_error`，保留 stdout/stderr/退出码正文和截断。
5. 将非零退出测试改为通过 `sys.executable -c` 构造跨平台命令。
6. 将超时测试改为启动延迟写标记文件的 Python 子进程；Registry 返回超时后等待超过子进程原定延迟，断言标记文件仍不存在。

**验证：** 运行 `python -m pytest tests/test_tool.py -q -k "Bash"`，预期 echo 成功、非零退出为错误、超时为错误且延迟标记不存在；再运行 `python -m pytest tests/test_tool.py -q`，预期工具测试全部通过。

## T6: 扩展 LLM 公共事件与 Provider 接口

**文件：** `src/endless_code/llm/__init__.py`、`tests/test_agent.py`  
**依赖：** 无

**步骤：**

1. 新增 `Usage(input_tokens=0, output_tokens=0)` dataclass。
2. 为 `StreamEvent` 增加 `usage: Usage | None`。
3. 为 `Provider.stream` 增加带默认值的 `system_suffix: str = ""` 参数。
4. 更新现有 FakeProvider 的签名，使旧测试在 Provider 实现尚未改完时仍可运行。

**验证：** 运行 `python -c "from endless_code.llm import Usage, StreamEvent; e=StreamEvent(usage=Usage(1,2)); assert e.usage.input_tokens==1; print('T6 PASS')"`，预期输出 `T6 PASS`；运行 `python -m pytest tests/test_agent.py -q`，预期当前 Agent 测试不因签名变化新增失败。

## T7: 适配 OpenAI 流的系统后缀与用量

**文件：** `src/endless_code/llm/openai_provider.py`、`tests/test_llm.py`  
**依赖：** T6

**步骤：**

1. 让 `_to_openai_messages` 接收 `system_suffix`，非空时追加到系统提示。
2. 请求参数加入 `stream_options={"include_usage": True}`。
3. 使用 SDK 流的异步上下文管理器，保证生成器关闭时 HTTP 响应同步关闭。
4. 在遍历每个 chunk 时独立读取 `chunk.usage`，兼容 usage 与 choices 同时存在或 choices 为空。
5. 正常结束时先发一个 Usage 事件，再发工具调用事件和 done；无 usage 时不伪造数据。
6. 新建 FakeStream/FakeChunk 测试，覆盖文本分片、工具 JSON 参数拼接、系统后缀、请求参数、usage 和流关闭。

**验证：** 运行 `python -m pytest tests/test_llm.py -q -k "OpenAI"`，预期 OpenAI 相关测试全部通过，FakeStream 的关闭标记为真。

## T8: 适配 DeepSeek 流的系统后缀与用量

**文件：** `src/endless_code/llm/deepseek_provider.py`、`tests/test_llm.py`  
**依赖：** T6、T7

**步骤：**

1. 复用带 `system_suffix` 的消息转换，保持 DeepSeek 默认 base URL。
2. 请求参数加入兼容的 `stream_options={"include_usage": True}`。
3. 使用 SDK 流异步上下文管理器，并按 OpenAI 兼容字段读取 usage。
4. 保持 `thinking=True` 时既有 `extra_body`，确认 Plan Mode 后缀不会覆盖 thinking 请求体。
5. 扩展 FakeStream 测试，覆盖文本、工具分片、usage、系统后缀、默认 base URL 和 thinking 请求参数。

**验证：** 运行 `python -m pytest tests/test_llm.py -q -k "DeepSeek"`，预期 DeepSeek 相关测试全部通过；运行 `python -m pytest tests/test_llm.py -q`，预期两个 Provider 的测试全部通过。

## T9: 定义 Agent 模式、事件和停止常量

**文件：** `src/endless_code/agent/__init__.py`、`tests/test_agent.py`  
**依赖：** T6

**步骤：**

1. 新增 `Mode.NORMAL/PLAN`。
2. 为 `ToolEvent` 增加 `call_id`，保留 name、args、phase、result、is_error。
3. 为 `Event` 增加 usage、iteration 和 notice；iteration 使用 `None` 表示非进度事件。
4. 加入 `MAX_ITERATIONS=25`、`MAX_UNKNOWN_RUN=3` 和五条停止文案常量。
5. 增加仅验证数据模型默认值、模式值和常量的单测，不改变主循环行为。

**验证：** 运行 `python -m pytest tests/test_agent.py -q -k "event_model or constants"`，预期新增模型测试通过。

## T10: 实现可取消的单轮 Provider 流收集

**文件：** `src/endless_code/agent/__init__.py`、`tests/test_agent.py`  
**依赖：** T7、T8、T9

**步骤：**

1. 新增内部 `_RoundState`，保存完整文本、工具调用、usage、错误和取消状态。
2. 新增 `_stream_events(...)` async generator；转发文本事件，并把完整结果写入传入的 `_RoundState`。
3. 对每次 `anext(provider_stream)` 与长期存在的 `cancel.wait()` task 使用 `asyncio.wait(FIRST_COMPLETED)`。
4. 取消优先时取消并等待 `anext`，关闭 Provider async generator；所有出口在 `finally` 清理辅助 task。
5. 测试文本实时顺序、工具调用收集、usage 收集、provider err 和无 chunk 时取消能及时关闭流。

**验证：** 运行 `python -m pytest tests/test_agent.py -q -k "stream_events"`，预期流收集和取消测试全部通过，FakeProvider 的关闭标记为真且无 pending task 警告。

## T11: 实现保序分批工具执行

**文件：** `src/endless_code/agent/__init__.py`、`tests/test_agent.py`  
**依赖：** T4、T5、T9

**步骤：**

1. 新增内部执行状态，按调用数预分配 Result 槽位并保存完成标记。
2. 按调用顺序切分最长连续只读批；有副作用或未知工具形成单调用批。
3. 每批先按序发 START，再执行；只读批创建并发 task，其他批只等待一个 task。
4. 正常结束时按原下标保存结果并按序发 END。
5. 取消时取消并等待未完成 task；保留已完成结果，为当前和后续未完成调用生成取消 Result，并为所有结果发 END。
6. 用插桩工具记录并发峰值、开始/结束时刻和执行顺序，覆盖 `[只读, 只读, 写, 只读]`、未知工具和执行中取消。

**验证：** 运行 `python -m pytest tests/test_agent.py -q -k "batch"`，预期只读并发峰值至少为 2，写工具晚于前一只读批完成，结果与 END 事件顺序等于模型调用顺序。

## T12: 接入 ReAct 主循环与自然/限制停止

**文件：** `src/endless_code/agent/__init__.py`、`tests/test_agent.py`  
**依赖：** T2、T10、T11

**步骤：**

1. 重写 `Agent.run`，默认 NORMAL 和未触发取消事件，Plan Mode 使用只读定义及系统后缀。
2. 每轮先发 iteration，转发 `_stream_events`，随后发 usage。
3. 无工具时保存一次最终 assistant 文本并 done；空文本保存空答复提示。
4. 有工具时保存 assistant-with-tool-calls，转发批次工具事件并保存有序 ToolResult，然后进入下一轮。
5. 连续全未知工具达到 3 轮时追加停止 assistant、notice、done；混入已知工具时重置计数。
6. 25 轮全部含工具时追加迭代上限 assistant、notice、done，不发起第 26 次请求。
7. 重写测试脚本，覆盖纯文本、跨两轮工具任务、Plan 工具集合、未知计数重置和迭代上限。

**验证：** 运行 `python -m pytest tests/test_agent.py -q -k "natural or multi_round or plan_mode or unknown or max_iterations"`，预期所有停止场景通过，Provider 调用次数与预期严格相等。

## T13: 完成 Agent 错误/取消历史收尾

**文件：** `src/endless_code/agent/__init__.py`、`tests/test_agent.py`  
**依赖：** T12

**步骤：**

1. 实现内部 assistant 收尾函数，组合 partial text 与停止说明并避免重复 assistant 写入。
2. 流错误时发 err，补普通 assistant 错误尾部，再发 done。
3. Provider 流取消时补 assistant 取消尾部，发 notice 和 done。
4. 工具阶段取消时先保存当前轮全部实际/取消 ToolResult，再追加 assistant 取消尾部。
5. 对每种异常终止断言工具调用均有同 ID 结果、最后角色为 assistant、done 恰好一次。
6. 每种终止后向同一 Conversation 添加下一条 user 消息并运行纯文本回合，断言能够继续。

**验证：** 运行 `python -m pytest tests/test_agent.py -q -k "cancel or stream_error or history"`，预期全部通过；运行 `python -m pytest tests/test_agent.py -q`，预期 Agent 测试全部通过。

## T14: 修复 TUI 注册中心冲突并增加挂载测试

**文件：** `src/endless_code/tui/app.py`、`tests/test_tui.py`  
**依赖：** T3、T4、T9

**步骤：**

1. 将应用工具注册中心字段从 `_registry` 改为 `_tool_registry`，更新所有读取点。
2. 初始化模式、迭代、用量、取消事件、运行工具列表和已配置密钥集合。
3. 收集所有可解析 provider key；缺失环境变量只跳过，不在 mount 时抛出。
4. 新建 Textual `run_test()` 挂载用例，使用多个假配置避免真实网络，断言 Header、RichLog、Input、Footer 均存在。
5. 退出测试上下文，断言没有 Textual `_registry` 类型错误。

**验证：** 运行 `python -m pytest tests/test_tui.py -q -k "mount"`，预期挂载和退出测试通过；运行带占位环境变量的 `python -m endless_code`，预期 TUI 能挂载而非立即抛 `TypeError`，随后退出。

## T15: 接入 TUI 模式命令、状态与轮次/用量

**文件：** `src/endless_code/tui/app.py`、`tests/test_tui.py`  
**依赖：** T2、T12、T14

**步骤：**

1. 抽取统一的 turn 启动方法，负责 user 消息、状态、计时器、取消事件和 Agent 消费 task。
2. `/plan` 只切换 PLAN、写模式提示并刷新状态，不调用 Provider。
3. `/do` 切换 NORMAL，把 `EXECUTE_DIRECTIVE` 作为 user 消息并立即启动 turn；不把 `/do` 字面值写入历史。
4. 普通文本按当前 mode 启动，空白输入不创建消息。
5. 用 `sub_title` 显示 provider、model、PLAN 标识和累计输入/输出 Token。
6. 消费 iteration/usage 事件并刷新动态区和状态文本。
7. 测试模式跨轮保持、`/do` 历史、iteration 更新和 usage 累加。

**验证：** 运行 `python -m pytest tests/test_tui.py -q -k "plan or do_command or usage or iteration"`，预期全部通过，FakeProvider 收到的 Plan 工具集合只有三项只读工具。

## T16: 接入 TUI 工具/文本/错误渲染与脱敏

**文件：** `src/endless_code/tui/app.py`、`tests/test_tui.py`  
**依赖：** T3、T13、T15

**步骤：**

1. text 事件累积；工具 START 前提交脱敏 preamble，并用安全参数摘要加入动态工具列表。
2. 工具 END 按 call_id 移除动态项；即使没有匹配 START 也使用 END 自带参数渲染脱敏工具行和结果。
3. notice 写系统提示；err 先提交尚未显示的 partial text，再写脱敏错误块。
4. done 提交剩余最终文本并统一清理计时器、取消事件、动态工具、task 和输入状态。
5. 删除 TUI 中任何 `Conversation.add_assistant` 调用，确保 Agent 是唯一 assistant/tool 历史写入者。
6. 测试纯文本回合历史为 `user, assistant` 而非重复 assistant；测试多轮 preamble/工具/最终文本顺序。
7. 注入测试密钥到 assistant 文本、工具参数、结果和错误，断言 RichLog 导出的可见文本均不含原文。

**验证：** 运行 `python -m pytest tests/test_tui.py -q -k "render or history or redaction"`，预期全部通过；历史角色断言严格为预期序列，UI 文本中没有模拟密钥。

## T17: 接入 Esc/Ctrl+C 取消与退出清理

**文件：** `src/endless_code/tui/app.py`、`tests/test_tui.py`  
**依赖：** T13、T16

**步骤：**

1. 增加 Ctrl+C action：STREAMING 时只触发本轮取消事件，其他状态退出程序。
2. 增加 Esc action：STREAMING 时触发取消，其他状态不处理。
3. 保留 Ctrl+D 退出；退出时取消并等待当前消费 task，使 BashTool 能完成 cancellation cleanup。
4. 确保重复按取消键幂等，不创建新 task、不重复写取消历史。
5. 用阻塞 FakeProvider 和阻塞 FakeTool 分别测试 Esc/Ctrl+C；断言回到 IDLE、应用仍运行、无 pending task，随后正常消息可完成。

**验证：** 运行 `python -m pytest tests/test_tui.py -q -k "escape or ctrl_c or cancel"`，预期取消测试全部通过且测试结束无 asyncio task 泄漏警告。

## T18: 更新用户文档

**文件：** `README.md`  
**依赖：** T15、T17

**步骤：**

1. 将“单轮闭环”改为多轮 Agent Loop，说明连续工具调用和停止条件。
2. 增加 `/plan`、`/do`、Esc、流式 Ctrl+C、空闲 Ctrl+C 行为。
3. 说明状态区的模式、迭代轮次和累计输入/输出 Token。
4. 保持供应商为 DeepSeek/OpenAI，项目结构与实际新增 `security.py` 一致。
5. 删除与实现不一致的快捷键或单轮上限描述。

**验证：** 运行 `rg -n "Agent Loop|/plan|/do|DeepSeek|OpenAI|security.py" README.md`，预期每个主题至少命中一次；运行 `rg -n "Anthropic|mewcode|单轮闭环" README.md`，预期无命中。

## T19: 全量格式、静态检查与自动化测试

**文件：** 所有本阶段修改文件  
**依赖：** T1-T18

**步骤：**

1. 运行 `python -m ruff --version`；若模块缺失，执行 `python -m pip install "ruff>=0.4"` 安装 `pyproject.toml` 已声明的开发工具后重试。
2. 运行 Ruff 格式化，再运行格式检查。
3. 运行 Ruff 静态检查并修复全部告警，包括旧测试中的未使用导入。
4. 运行 compileall 和全量 pytest。
5. 检查测试输出无 pytest-asyncio 弃用警告、pending task、未回收子进程或 ResourceWarning。
6. 检查 git diff，确认没有修改真实 `.endless-code/config.yaml`、没有密钥或测试临时文件进入变更。

**验证：** 依次运行 `python -m ruff format .`、`python -m ruff format --check .`、`python -m ruff check .`、`python -m compileall -q src`、`python -m pytest -q`，预期所有命令退出码为 0。

## T20: Textual 版本矩阵与本地集成冒烟

**文件：** 无（验证）  
**依赖：** T19

**步骤：**

1. 在工作区外的临时虚拟环境依次安装 editable 项目、`pytest`、`pytest-asyncio` 和最低 Textual 版本，运行 `tests/test_tui.py`。
2. 在当前 Textual 8.x 环境运行同一测试，并运行带占位 key 的 TUI 启动/退出冒烟。
3. 运行无网络 FakeProvider 集成场景：普通文本、两轮工具、Plan -> `/do`、流错误和取消。
4. 再次确认每个场景 history 末尾为 assistant、TUI 回到 IDLE、累计用量与迭代事件可见。
5. 删除工作区外临时虚拟环境，不在仓库留下矩阵测试产物。

**验证：** 两个 Textual 环境下 `python -m pytest tests/test_tui.py -q` 均退出 0；当前环境的 FakeProvider 集成测试全部通过，仓库 `git status --short` 不出现临时环境或探针文件。

## 执行顺序

```text
T1 ───────────────────────────────────────────────────────────────┐
T2 ───────────────┐                                               │
T3 ───────────────┼─────────────────────────────┐                 │
T4 ─> T5 ─────────┼──────────────┐              │                 │
T6 ─> T7 ─> T8 ─> T10 ──────────┼─> T12 ─> T13 ┼─> T15 ─> T16 ─> T17 ─> T18
      T9 ──────────┼─> T11 ──────┘              │                 │
                   └───────────────> T14 ────────┘                 │
T1-T18 ───────────────────────────────────────────────> T19 ─> T20
```

依赖补充：T9 依赖 T6；T10 依赖 T7/T8/T9；T11 依赖 T4/T5/T9；T12 依赖 T2/T10/T11；T13 依赖 T12；T14 依赖 T3/T4/T9；T15 依赖 T2/T12/T14；T16 依赖 T3/T13/T15；T17 依赖 T13/T16。
