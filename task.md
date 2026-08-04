# 系统提示工程化 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | pyproject.toml | 增加 Anthropic SDK 依赖并保持现有版本范围 |
| 删除 | src/endless_code/prompt.py | 迁移为 prompt 子包，避免模块/包同名冲突 |
| 新建 | src/endless_code/prompt/__init__.py | Prompt 公共导出与兼容导出 |
| 新建 | src/endless_code/prompt/modules.py | Module、固定模块和可选空模块 |
| 新建 | src/endless_code/prompt/environment.py | 环境数据结构、Git 降级采集与渲染 |
| 新建 | src/endless_code/prompt/reminder.py | system-reminder、Plan reminder、执行指令 |
| 修改 | src/endless_code/config.py | 支持 anthropic protocol、API key 和 base_url |
| 修改 | src/endless_code/llm/__init__.py | System、Request、缓存 Usage 与 Provider 接口 |
| 新建 | src/endless_code/llm/anthropic_provider.py | Anthropic system/cache/message/stream 适配 |
| 修改 | src/endless_code/llm/openai_provider.py | Request、环境块、reminder、cached_tokens |
| 修改 | src/endless_code/llm/deepseek_provider.py | Request、环境块、reminder、prompt cache usage |
| 修改 | src/endless_code/tool/edit_file.py | 强化编辑前读取描述 |
| 修改 | src/endless_code/tool/bash.py | 强化专用工具优先描述 |
| 修改 | src/endless_code/agent/__init__.py | 构造 Request、环境、轮次 reminder 和缓存透传 |
| 修改 | src/endless_code/tui/app.py | 传入版本并保留既有模式/取消行为 |
| 修改 | src/endless_code/cli.py | 传递应用版本、支持三 Provider 配置选择 |
| 新建 | examples/smoke.py | 打印三协议输入/输出/缓存 usage |
| 新建 | tests/test_config.py | 三协议配置、环境变量和 base_url |
| 新建 | tests/test_prompt.py | 模块装配、环境降级和 reminder |
| 新建 | tests/test_anthropic_provider.py | Anthropic system/cache/reminder/usage |
| 修改 | tests/test_llm.py | OpenAI/DeepSeek Request 接口与缓存 usage |
| 修改 | tests/test_agent.py | FakeProvider、Request、轮次 reminder、缓存透传 |
| 修改 | tests/test_tui.py | 新接口下的 TUI 回归 |
| 修改 | tests/test_tool.py | 工具 description 强化回归 |

## T1: 建立 Prompt 模块化基础

文件：src/endless_code/prompt.py、src/endless_code/prompt/__init__.py、src/endless_code/prompt/modules.py
依赖：无

步骤：

1. 将现有系统提示固定内容拆成身份、系统约束、任务模式、动作执行、工具使用、语气风格、文本输出七个 Module。
2. 增加三个空的可选模块：自定义指令、已激活 Skill、长期记忆。
3. 实现按 priority 升序拼接、跳过空内容并以空行分隔的 assemble_system。
4. 在 prompt/__init__.py 导出 build_system_prompt 和旧名称兼容常量，迁移现有 import。
5. 删除旧的 src/endless_code/prompt.py，保证 from endless_code.prompt import ... 仍可用。

验证：运行 python -m pytest tests/test_prompt.py -q -k 'module or assemble'，预期固定模块顺序正确、空模块不产生多余空行，且旧导入路径可用。

## T2: 实现环境信息和动态提醒

文件：src/endless_code/prompt/environment.py、src/endless_code/prompt/reminder.py、tests/test_prompt.py
依赖：T1

步骤：

1. 实现 Environment 与 gather_environment(version, model)，收集工作目录、平台、日期、Git 状态、版本和模型。
2. Git 命令使用有界执行；非 Git 目录、超时或异常时只返回空 Git 状态，不抛出到 Agent。
3. 实现 Environment.render，不得读取或渲染 API key、环境变量值或其它敏感配置。
4. 实现 system_reminder 标签包裹、完整/精简 plan_reminder 和 EXECUTE_DIRECTIVE。
5. 为固定环境、Git 降级、标签格式、完整/精简提醒增加测试。

验证：运行 python -m pytest tests/test_prompt.py -q，预期包含环境字段、Git 降级和 system-reminder 断言的测试全部通过。

## T3: 扩展配置和依赖

文件：pyproject.toml、src/endless_code/config.py、tests/test_config.py
依赖：无

步骤：

1. 增加 anthropic SDK 依赖，保留 Python、Textual、OpenAI 和 PyYAML 约束。
2. 将 protocol 校验扩展为 anthropic、deepseek、openai。
3. 保留 OpenAI/DeepSeek 的 base_url；Anthropic 缺省使用官方地址，允许显式覆盖。
4. 保持  展开、缺失 key 的 ConfigError 和多 Provider 加载顺序。
5. 为三种协议、环境变量 key、缺省/自定义 base_url 和非法 protocol 增加测试；测试不得打印 key。

验证：运行 python -m pytest tests/test_config.py -q，预期三协议配置均可解析，缺失环境变量得到结构化错误。

## T4: 迁移公共 LLM 请求接口

文件：src/endless_code/llm/__init__.py、tests/test_llm.py
依赖：T1、T2

步骤：

1. 新增 System 和 Request dataclass，区分 stable、environment、messages、tools 和 reminder。
2. 为 Usage 增加 cache_write、cache_read，默认值为 0。
3. 将 Provider Protocol 改为 stream(req: Request)，保留 StreamEvent 的文本、工具、usage、done、err 语义。
4. 更新 FakeProvider 及公共测试构造，确保旧 Agent 行为的测试可以迁移。

验证：运行 python -m pytest tests/test_llm.py -q -k 'request or usage'，预期 Request 字段和缓存 Usage 默认值/透传测试通过。

## T5: 实现 Anthropic Provider

文件：src/endless_code/llm/anthropic_provider.py、tests/test_anthropic_provider.py
依赖：T4

步骤：

1. 使用 AsyncAnthropic 和 ProviderConfig 创建客户端；支持默认和自定义 base_url。
2. 将 stable system 序列化为带 cache_control.type=ephemeral 的文本块，environment 序列化为无缓存控制的第二文本块。
3. 按 Anthropic content block 协议转换历史、工具调用和工具结果，保持 call ID 配对。
4. 将 reminder 追加到末条可追加消息的 content block；必要时创建合法 user 消息，不修改传入 Conversation。
5. 解析文本分片、工具 JSON 分片、完成/错误和 cache_creation_input_tokens、cache_read_input_tokens。
6. 在异步 generator 的 finally 中关闭响应和辅助任务。
7. 用 Fake AsyncAnthropic/FakeStream 覆盖 system 顺序、缓存断点、reminder、usage、工具调用和关闭。

验证：运行 python -m pytest tests/test_anthropic_provider.py -q，预期请求 payload、缓存标记、提醒注入和流关闭断言全部通过。

## T6: 迁移 OpenAI 与 DeepSeek Provider

文件：src/endless_code/llm/openai_provider.py、src/endless_code/llm/deepseek_provider.py、tests/test_llm.py
依赖：T4

步骤：

1. 将两个 Provider 的输入改为 Request，stable system 放在请求前缀，environment 位于稳定块之后。
2. OpenAI/DeepSeek reminder 使用尾部 user 消息，不写入 Conversation。
3. 保持工具定义顺序、thinking/extra_body、分片工具参数和异步关闭逻辑。
4. 解析 OpenAI prompt_tokens_details.cached_tokens 与 DeepSeek 可用 prompt cache 字段；缺失时为零。
5. 更新 FakeStream 测试，覆盖两 Provider 的同一 Request 语义、base_url、usage 和 reminder。

验证：运行 python -m pytest tests/test_llm.py -q，预期 OpenAI、DeepSeek 测试全部通过且缺省缓存字段不报错。

## T7: 强化工具约定

文件：src/endless_code/tool/edit_file.py、src/endless_code/tool/bash.py、tests/test_tool.py
依赖：T1

步骤：

1. 在 edit_file 描述中明确编辑前必须先读取目标文件。
2. 在 bash 描述中明确优先使用 read/write/edit/glob/grep 专用工具。
3. 保持工具执行行为、超时、退出码和安全摘要不变。
4. 增加 description 文本回归断言。

验证：运行 python -m pytest tests/test_tool.py -q -k 'description or Registry'，预期工具注册顺序和强化文本通过。

## T8: 接入 Agent Request 和 Plan reminder

文件：src/endless_code/agent/__init__.py、tests/test_agent.py
依赖：T2、T4、T5、T6

步骤：

1. Agent 构造函数接收并保存应用 version。
2. 每次 run 开始构造稳定 system 和 environment，按 mode 选择全量或只读 tools。
3. 增加 PLAN_REMINDER_INTERVAL=4；首轮和间隔轮次使用完整提醒，其余轮次使用精简提醒。
4. 每轮组装 Request 调用 Provider，透传缓存 usage 到 Event.usage。
5. 确保 reminder 不写入 Conversation，不改变既有 assistant/tool 历史、取消、错误、批量执行和停止条件。
6. 更新 FakeProvider 记录 Request，增加 stable/environment/reminder/tools/cache usage 断言。

验证：运行 python -m pytest tests/test_agent.py -q，预期原有 Agent 场景和新增 Request、Plan reminder、缓存透传测试全部通过。

## T9: 接入配置、TUI 和 smoke

文件：src/endless_code/cli.py、src/endless_code/tui/app.py、examples/smoke.py
依赖：T3、T8

步骤：

1. Provider 工厂和 CLI 传入应用版本，保留多 Provider 选择与缺失 key 降级。
2. TUI 构造 Agent 时传入 version；保留 /plan、/do、Esc、Ctrl+C、状态和历史行为。
3. 新建 smoke 脚本，加载配置，连续执行两轮最小请求，打印输入/输出/cache_write/cache_read，不打印密钥。
4. smoke 对不存在缓存字段按 0 展示，对 provider 错误返回非零并保留可读错误。

验证：运行 python -m pytest tests/test_tui.py -q；使用 FakeProvider 运行 smoke 测试入口，预期 TUI 测试通过且 smoke 输出不含 key。

## T10: 更新回归测试和工程检查

文件：tests/test_config.py、tests/test_prompt.py、tests/test_anthropic_provider.py、tests/test_llm.py、tests/test_agent.py、tests/test_tui.py、tests/test_tool.py
依赖：T1-T9

步骤：

1. 执行每个前置任务的验证命令并修复失败。
2. 增加跨协议请求装配测试：stable 相同、environment/reminder 动态、tools 顺序稳定。
3. 增加既有 Agent Loop 多轮、取消、流错误、Plan/Do、历史和脱敏回归。
4. 检查测试输出、Git diff 和配置忽略规则，不允许真实 key 或临时文件进入变更。

验证：依次运行 python -m ruff format .、python -m ruff format --check .、python -m ruff check .、python -m compileall -q src、python -m pytest -q 和 python -m pytest -q -W error::ResourceWarning，预期全部退出码为 0。

## 执行顺序

T1 -> T2
T3 -> T4 -> T5
       |     |
       +----> T6
T1 -> T7
T2,T4,T5,T6 -> T8 -> T9
T1-T9 -> T10

所有任务完成后，逐项执行 checklist.md；未通过项必须修复并重跑，不以静态推断代替验证。