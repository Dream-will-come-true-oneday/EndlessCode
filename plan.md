# 系统提示工程化 Plan

> 技术栈：Python 3.12+、Textual、OpenAI SDK、Anthropic SDK。保留现有 `endless_code` 包名和 Agent Loop 控制流，新增 Anthropic Provider，并让 OpenAI/DeepSeek 共用兼容协议基础。

## 架构概览

本阶段在现有 Agent Loop 外增加三层：

1. **Prompt 层**：将固定系统指令拆成有优先级的模块；单独构造环境信息；按轮次生成 `<system-reminder>`。
2. **请求层**：使用 `System` 与 `Request` dataclass 区分稳定系统块、动态环境块、持久历史、工具定义和本轮 reminder。
3. **Provider 层**：Anthropic 使用两块 system 与显式缓存断点；OpenAI/DeepSeek 使用稳定 system 前缀和兼容消息；三者统一解析流式文本、工具调用与 usage。

调用链：

```text
Agent.run
  -> build_system_prompt + gather_environment
  -> 根据 mode/iteration 构造 plan reminder
  -> Request(messages, tools, system, reminder)
  -> Anthropic/OpenAI/DeepSeek Provider
  -> StreamEvent(text/tool_calls/usage/done/err)
  -> Agent Loop 与 TUI
```

稳定前缀只包含模块化系统提示和本轮固定工具定义。环境信息、历史与 reminder 在其后动态传递，不写入 `Conversation`。

## 核心数据结构与接口

### `prompt.Module`

```python
@dataclass(frozen=True)
class Module:
    name: str
    priority: int
    content: str
```

`fixed_modules()` 返回七个固定模块，优先级 10 至 70；`optional_modules()` 返回三个空槽，优先级 80 至 100。`assemble_system()` 按 priority 升序排序，跳过空内容，以两个换行连接。

### `prompt.Environment`

```python
@dataclass(frozen=True)
class Environment:
    working_dir: str
    platform: str
    date: str
    git_status: str
    version: str
    model: str

    def render(self) -> str: ...
```

`gather_environment(version, model)` 使用标准库收集目录、平台、日期和版本；Git 状态通过有界的 `git status --porcelain` 获取，失败时为空字符串降级，不读取环境变量。

### `llm.System` 与 `llm.Request`

```python
@dataclass(frozen=True)
class System:
    stable: str = ""
    environment: str = ""


@dataclass
class Request:
    messages: list[Message] = field(default_factory=list)
    tools: list[ToolDefinition] = field(default_factory=list)
    system: System = field(default_factory=System)
    reminder: str = ""
```

`Provider.stream(req: Request) -> AsyncIterator[StreamEvent]` 替换现有位置参数接口。兼容测试 Provider 一并迁移到 Request。

### `llm.Usage`

```python
@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write: int = 0
    cache_read: int = 0
```

Anthropic 读取 `cache_creation_input_tokens` 和 `cache_read_input_tokens`；OpenAI 读取 `prompt_tokens_details.cached_tokens`；DeepSeek 读取兼容响应中的 prompt cache 命中字段。任何字段缺失均为零。

## 模块设计

### Prompt 模块

**职责：** 稳定系统提示、环境段和动态 reminder 的构造，不依赖 `llm` 包。

- `modules.py`：模块类型、七个固定模块、三个空可选模块和固定正文。
- `environment.py`：`Environment`、Git 状态采集和安全渲染。
- `reminder.py`：`system_reminder()`、完整/精简 Plan reminder、`EXECUTE_DIRECTIVE`。
- `__init__.py`：导出 `assemble_system`、`build_system_prompt`、`gather_environment`、`Environment`、reminder API，并兼容导出现有 `SYSTEM_PROMPT`、`PLAN_MODE_REMINDER`、`EXECUTE_DIRECTIVE` 名称，减少外部调用迁移风险。

工具使用模块与 `edit_file`、`bash` 的 description 同时强化专用工具优先和编辑前先读。

### LLM 公共接口

**文件：** `src/endless_code/llm/__init__.py`

新增 `System`、`Request` 与缓存字段；保持 `Message`、`ToolCall`、`ToolResult`、`ToolDefinition` 和 `StreamEvent` 的既有语义。Provider 工厂增加 `anthropic` 分支，保留 `openai` 和 `deepseek`。

### Anthropic Provider

**文件：** `src/endless_code/llm/anthropic_provider.py`

- 使用 `AsyncAnthropic`，配置 `api_key` 和默认 `https://api.anthropic.com`，允许自定义 `base_url`。
- 将 `Request.system.stable` 序列化为带 `cache_control: {"type": "ephemeral"}` 的文本块；环境块作为无缓存控制的第二文本块。
- 工具定义置于 system 之前的请求前缀中，工具顺序遵循 Registry 顺序。
- 将历史消息转换为 Anthropic content blocks；工具调用和工具结果保持 call ID 配对。
- reminder 非空时追加到末条 user/tool content block；末条不是可追加消息时新建一条 user 消息，保证角色合法。
- 沿用 DeepSeek 的 thinking 语义只对 DeepSeek 生效；Anthropic 的本阶段不增加额外思考配置。
- 流式解析文本增量、工具参数分片、usage、完成和错误，并在 generator finally 中关闭响应。

### OpenAI Provider

**文件：** `src/endless_code/llm/openai_provider.py`

- 将稳定 system 放在消息前缀；环境段追加在稳定 system 后，保持稳定块字节不变。
- 将 `Request.messages`、`Request.tools` 和 reminder 映射为 OpenAI Chat Completions 请求。
- reminder 作为尾部 user 消息注入，不写入持久历史。
- 从 `prompt_tokens_details.cached_tokens` 读取缓存命中；字段不存在时使用零。
- 保留工具 JSON 分片拼接、流关闭、错误和 usage 事件行为。

### DeepSeek Provider

**文件：** `src/endless_code/llm/deepseek_provider.py`

- 继续复用 OpenAI-compatible message/tool 序列化和流收集逻辑，默认 base URL 保持 `https://api.deepseek.com`。
- 保留 `thinking` 到 `extra_body` 的配置，不覆盖系统块或 reminder。
- 解析 DeepSeek 可用的 prompt cache usage 字段；缺失时为零。
- 通过同一 `Request` 接口支持稳定系统提示、环境段、工具集和 reminder。

### 配置层

**文件：** `src/endless_code/config.py`、`pyproject.toml`

- `ProviderConfig.protocol` 扩展为 `anthropic | deepseek | openai`。
- 支持 Anthropic 默认 base URL、自定义 base URL、`ANTHROPIC_API_KEY` 环境变量和 model。
- 保留现有 OpenAI `base_url`，使兼容端点可配置；DeepSeek 行为不变。
- `new_provider()` 根据 protocol 构造三种 Provider。
- `pyproject.toml` 增加 `anthropic` 依赖，不改变 Python 和 Textual 版本范围。

### Agent

**文件：** `src/endless_code/agent/__init__.py`

- `Agent` 增加 `version` 字段；运行开始时构造稳定 system 和环境段。
- 增加 `PLAN_REMINDER_INTERVAL = 4`。
- 每轮按 `Mode.PLAN` 与 iteration 生成完整/精简 reminder，组装 `Request` 后调用 Provider。
- 将缓存字段透传到既有 Agent `Event.usage`，不改变 TUI 状态栏的输入/输出展示约定。
- 保留既有取消、错误、工具批次、历史写入和停止条件；动态 reminder 不调用 `Conversation.add_*`。

### TUI 与 smoke

**文件：** `src/endless_code/tui/app.py`、`src/endless_code/cli.py`、`examples/smoke.py`

- TUI 构造 Agent 时传入应用版本，保留 `/plan`、`/do`、Esc、Ctrl+C 和 Provider 选择行为。
- TUI 不显示缓存字段；只消费既有 usage/iteration 事件。
- 新增 smoke 脚本，以配置 Provider 连续发起两轮请求，打印 `input/output/cache_write/cache_read`，用于验证真实端点字段，不写入对话历史。

## 模块交互

1. CLI 加载 YAML 配置，Provider 工厂按 protocol 构造 Anthropic/OpenAI/DeepSeek。
2. TUI 将用户消息写入 Conversation，Agent 只负责 assistant/tool 历史。
3. Agent 首次运行构造稳定 system 和 Environment；每轮选择全量或只读工具。
4. Agent 依据模式与轮次生成 reminder，组装 Request。
5. Provider 按协议序列化 system、environment、history、tools 和 reminder，发起流式请求。
6. Provider 生成 StreamEvent；Agent 累积完整响应并继续现有 ReAct 循环。
7. Usage 的输入/输出/缓存字段通过 Agent Event 对外；smoke 额外打印缓存字段。

## 文件组织

```text
project/
├── pyproject.toml                         # anthropic 依赖与现有版本范围
├── spec.md / plan.md / task.md / checklist.md
├── examples/smoke.py                      # 三 Provider usage/cache 冒烟
├── src/endless_code/
│   ├── prompt/
│   │   ├── __init__.py                    # Prompt 公共导出
│   │   ├── modules.py                     # Module 与模块常量
│   │   ├── environment.py                 # Environment 与 Git 降级采集
│   │   └── reminder.py                    # system-reminder 与 Plan reminder
│   ├── llm/
│   │   ├── __init__.py                    # System/Request/Usage/Provider
│   │   ├── anthropic_provider.py           # Anthropic 缓存 system 与流解析
│   │   ├── openai_provider.py              # OpenAI/兼容端点
│   │   └── deepseek_provider.py            # DeepSeek 兼容端点
│   ├── agent/__init__.py                   # Request 组装与 Agent Loop
│   ├── config.py                           # 三 Provider 配置
│   ├── tui/app.py                          # TUI
│   └── tool/                               # 既有工具与描述强化
└── tests/
    ├── test_config.py                      # 三协议配置与 base_url
    ├── test_prompt.py                      # 模块、环境与 reminder
    ├── test_anthropic_provider.py          # system/cache/reminder 序列化
    ├── test_llm.py                         # OpenAI/DeepSeek Request/usage
    ├── test_agent.py                       # Request、轮次 reminder、缓存透传
    ├── test_tui.py                          # 既有 TUI 回归
    └── test_tool.py                         # 描述与工具回归
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| Prompt 组织 | `prompt` 子包 + 固定模块 | 让新增指令模块局部接入，并保持稳定顺序 |
| Provider 请求接口 | `Request` dataclass | 避免继续扩展位置参数，明确稳定/动态边界 |
| Anthropic 缓存 | stable system block 使用 `cache_control: ephemeral` | 让工具定义和稳定指令形成可复用前缀，环境块不影响前缀 |
| OpenAI 缓存 | stable system 位于消息前缀 | 兼容官方和自定义 `base_url`，不强制端点一定返回缓存字段 |
| DeepSeek 缓存 | 复用 OpenAI-compatible 请求并尽力解析 prompt cache 字段 | 保留现有适配器和 thinking 行为 |
| reminder 位置 | Anthropic 并入末条可追加 content；OpenAI/DeepSeek 追加 user 消息 | 保持协议消息合法，且不污染持久历史 |
| 环境采集 | 标准库 + 有界 git 命令 | 不依赖敏感环境变量，失败可降级 |
| TUI 缓存展示 | 不改变现有状态栏，仅 smoke 打印 | 遵循 Spec 中不新增状态栏缓存展示的范围边界 |
| 配置选择 | protocol 分支 + 可选 base_url | 同时覆盖 Anthropic、OpenAI 兼容端点和 DeepSeek |