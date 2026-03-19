# Agent Loop Plan

## 架构概览

本阶段在现有 `config -> llm/tool -> conversation -> agent -> tui -> cli` 分层上扩展，不拆分现有包，也不引入新的运行时框架。

- `endless_code.llm` 继续负责协议适配，新增统一用量对象、Plan Mode 系统提示后缀和流式用量上报。DeepSeek 与 OpenAI 都沿用 `AsyncOpenAI` 的 Chat Completions 流。
- `endless_code.tool` 增加只读分类、只读定义导出和可靠的命令进程清理。注册中心继续统一负责工具超时和异常值化。
- `endless_code.conversation` 继续保存协议无关历史，并增加末尾角色查询，供异常终止时补齐合法历史。
- `endless_code.agent` 从固定两次请求改为 ReAct 循环，独占 assistant/tool 历史写入，负责停止条件、取消、批次并发和事件顺序。
- `endless_code.tui` 只添加用户消息并消费 Agent 事件，负责模式切换、取消按键、累计用量、迭代展示和输出脱敏。
- `endless_code.security` 提供无状态的敏感文本脱敏和工具参数摘要，避免界面直接显示原始参数内容。

依赖方向保持无环：

```text
config ───────────────┐
prompt ───────────────┤
security ─────────────┤
llm ───────┐          │
tool ──────┼─> agent ─┼─> tui ─> cli
conversation ┘        │
```

`llm` 依赖 `config` 与 `prompt`；`tool` 依赖 `llm` 的工具定义类型；`agent` 依赖 `llm/tool/conversation/prompt`；`tui` 依赖上述公开接口与 `security`。

## 需求归属

| 需求 | 架构归属 |
|---|---|
| F1/F2 | `agent.Agent.run` 的循环与停止分支 |
| F3 | `agent.Event`、`ToolEvent` 与 TUI 事件消费 |
| F4 | 两个 Provider 的分片拼接与 Agent 单轮收集 |
| F5 | 工具只读分类与 Agent 保序分批执行 |
| F6 | `Conversation` 与 Agent 统一历史写入/终止收尾 |
| F7 | TUI 按键、per-turn 取消事件、Agent 流/工具取消 |
| F8 | `llm.Usage`、Provider usage 提取、TUI 累加展示 |
| F9 | Agent 迭代事件与 TUI 动态区 |
| F10 | `Mode`、只读工具定义、系统提示后缀、`/plan`/`/do` |
| F11 | `DeepSeekProvider`、`OpenAIProvider` 及共享协议测试 |
| N1/N5 | `Registry` 超时、`BashTool` 进程树清理、Agent task 清理 |
| N2 | Provider/工具异步执行、TUI 计时器与非阻塞事件消费 |
| N3/N6 | Agent 批次结果槽位与固定事件顺序 |
| N4 | 各工具现有截断逻辑与 TUI 结果摘要截断 |
| N7 | `security` 与所有 TUI 输出入口 |
| N8 | TUI 启动测试、依赖边界、跨平台测试命令 |

## 核心数据结构与接口

### LLM 用量与流事件

```python
@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class StreamEvent:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage | None = None
    done: bool = False
    err: Exception | None = None


class Provider(Protocol):
    def stream(
        self,
        msgs: list[Message],
        tools: list[ToolDefinition],
        system_suffix: str = "",
    ) -> AsyncIterator[StreamEvent]: ...
```

每次 Provider 请求最多产生一个 `usage` 事件。`usage` 在流结束前发出，随后发出 `done=True`。若请求异常，只发 `err`，不伪造用量。

### Agent 模式与事件

```python
class Mode(Enum):
    NORMAL = "normal"
    PLAN = "plan"


@dataclass
class ToolEvent:
    call_id: str
    name: str
    args: str = ""
    phase: Phase = Phase.START
    result: str = ""
    is_error: bool = False


@dataclass
class Event:
    text: str = ""
    tool: ToolEvent | None = None
    usage: Usage | None = None
    iteration: int | None = None
    notice: str = ""
    done: bool = False
    err: Exception | None = None


class Agent:
    def run(
        self,
        conv: Conversation,
        mode: Mode = Mode.NORMAL,
        cancel: asyncio.Event | None = None,
    ) -> AsyncIterator[Event]: ...
```

事件使用互斥的有效字段；一个事件只表达一种状态。每次 `run` 的所有终止路径最后都发出且只发出一个 `done=True`。错误路径先发 `err`，停止原因先发 `notice`，再以 `done` 收尾。

### 工具只读分类

```python
class Tool(Protocol):
    read_only: bool

    def name(self) -> str: ...
    def description(self) -> str: ...
    def parameters(self) -> dict[str, Any]: ...
    async def execute(self, args: str) -> Result: ...


class Registry:
    def read_only_definitions(self) -> list[ToolDefinition]: ...
    def is_read_only(self, name: str) -> bool: ...
```

分类固定如下：

| 工具 | `read_only` |
|---|---|
| `read_file` | `True` |
| `glob` | `True` |
| `grep` | `True` |
| `write_file` | `False` |
| `edit_file` | `False` |
| `bash` | `False` |

未知工具的 `is_read_only` 返回 `False`，因此形成串行批次边界。

### Conversation 末尾角色

```python
def last_role(self) -> str:
    """空历史返回空字符串，否则返回最后一条消息的 role。"""
```

### 安全输出

```python
def redact_sensitive(text: str, secrets: Collection[str] = ()) -> str: ...

def summarize_tool_args(
    tool_name: str,
    raw_args: str,
    secrets: Collection[str] = (),
) -> str: ...
```

`redact_sensitive` 先替换当前配置中可解析的确切密钥，再处理常见 `sk-...` 和 `api_key=...` 形式。`summarize_tool_args` 使用 JSON 解析：文件工具展示路径，写入/替换工具只展示内容长度，搜索工具展示模式与路径，bash 展示脱敏后的命令；JSON 非法或未知工具只显示脱敏截断摘要。

## 模块设计

### `endless_code.agent`

职责：ReAct 循环、停止条件、事件流、工具执行调度和历史一致性。

内置常量：

```python
MAX_ITERATIONS = 25
MAX_UNKNOWN_RUN = 3
NOTICE_MAX_ITER = "（已达最大迭代轮数 25，自动停止；可继续发消息推进。）"
NOTICE_UNKNOWN_TOOLS = "（连续多轮只请求到未注册的工具，自动停止。）"
NOTICE_STREAM_ERROR = "（请求出错，本轮已中断。）"
NOTICE_CANCELLED = "（已取消。）"
NOTICE_EMPTY_FINAL = "（任务已结束，模型未返回文本。）"
```

`Agent.run` 算法：

1. `cancel is None` 时创建未触发的 `asyncio.Event`。
2. `Mode.NORMAL` 使用全部工具和空系统后缀；`Mode.PLAN` 使用只读工具定义与 `PLAN_MODE_REMINDER`。
3. 对 `1..MAX_ITERATIONS`：
   - 发出 `Event(iteration=n)`。
   - 发起一次 Provider 流，实时转发文本，收集完整文本、工具调用和用量。
   - 收到用量后发出 `Event(usage=...)`。
   - 无工具调用时，由 Agent 把最终文本写入 Conversation，发出 `done` 并返回。
   - 有工具调用时，先写入含工具调用的 assistant 消息，再分批执行工具，最后一次性按原顺序写入所有工具结果。
   - 若执行未取消且未知工具连续计数未触顶，进入下一轮。
4. 达到未知工具阈值或迭代上限时，追加 assistant 停止说明，发出 `notice` 和 `done`。
5. 流错误或取消时按“历史收尾”规则追加消息，发出终止事件并返回。

Agent 是 assistant/tool 历史的唯一写入者。TUI 只在启动一轮前添加 user 消息；TUI 的完成处理不得再次调用 `add_assistant`，从根源上消除重复历史。

#### 可取消的 Provider 流

不能只在收到 chunk 后轮询 `cancel`，否则网络长时间无数据时取消不及时。单轮收集器对每次 `anext(stream)` 与一个长期存在的 `cancel.wait()` task 使用 `asyncio.wait(..., FIRST_COMPLETED)`：

- chunk 先到则消费并继续；
- cancel 先到则取消并等待 `anext` task，关闭 Provider async generator，标记本轮取消；
- `finally` 中取消并等待辅助 task，确保没有挂起任务。

两个 Provider 在读取 SDK 流时使用异步上下文管理器；Agent 关闭生成器后，适配器退出上下文并关闭 HTTP 响应。

#### 保序分批执行

按调用顺序切分最长连续批次：

- 连续只读工具形成并发批；
- 每个有副作用工具或未知工具单独形成串行批。

每批执行流程：

1. 按调用顺序发出全部 `Phase.START` 事件。
2. 只读批为每个调用创建独立 task；串行批只创建一个 task。
3. 将工具完成 task 与 `cancel.wait()` 竞速。
4. 正常完成后把结果写入预分配的原始下标。
5. 取消时取消并等待未完成 task；已完成结果保留，未完成调用生成 `is_error=True` 的取消结果。
6. 按调用顺序发出全部 `Phase.END` 事件。
7. 所有批次结束或取消后，按原顺序构造 `ToolResult` 列表并一次写入 Conversation。

并发 task 不写 Conversation，只返回自己的 `Result`；主循环负责合并，因此没有共享历史竞争。

#### 历史收尾

- 流在工具调用入历史前取消或出错：追加 `partial_text + notice` 的 assistant 文本；没有 partial text 时只追加 notice。
- 工具执行阶段取消：为本轮每个工具调用补齐实际或取消结果，再追加 assistant 取消说明。
- 未知工具阈值或迭代上限：工具结果已经配对，再追加 assistant 停止说明。
- 自然完成：只追加模型最终文本；空文本使用 `NOTICE_EMPTY_FINAL`。

所有异常终止后历史均以普通 assistant 文本结尾，下一轮可继续使用。

### `endless_code.llm`

职责：协议无关消息/事件定义、DeepSeek/OpenAI 请求转换、工具调用分片拼接和用量上报。

共享行为：

- `_to_openai_messages(msgs, system_suffix)` 在内置系统提示后拼接非空后缀。
- 两个 Provider 请求均启用流与工具定义，并请求流式 usage。
- 每个 chunk 先检查 `chunk.usage`，不能假定 usage chunk 一定没有 choices。
- 工具调用继续按 `index` 缓冲 `id/name/arguments`，流结束后按 index 生成 `ToolCall`。
- SDK 流放入 `async with response`，确保取消时关闭连接。

`OpenAIProvider`：请求加入 `stream_options={"include_usage": True}`，从 `prompt_tokens` 和 `completion_tokens` 生成 `Usage`。

`DeepSeekProvider`：沿用默认 DeepSeek `base_url` 与现有 `thinking` 请求体，同时加入兼容的 `stream_options={"include_usage": True}`，按同一 usage 字段生成 `Usage`。Plan Mode 仅影响工具定义和系统后缀，不改变 `thinking` 配置。

若兼容端点没有返回 usage，适配器不伪造数字；自动化 Provider 测试验证字段解析，真实 DeepSeek/OpenAI 用量由端到端验收确认。

### `endless_code.tool`

职责：工具分类、超时、结构化错误和命令生命周期。

- `Registry.execute` 保留单一 `asyncio.wait_for` 超时层；Agent 不再重复包装超时。
- `BashTool` 根据 `returncode != 0` 设置 `Result.is_error`。
- `BashTool.execute` 捕获 `CancelledError`，终止完整进程树、等待进程退出后重新抛出取消，让 `Registry` 正确返回超时或让 Agent 正确处理用户取消。
- POSIX 创建独立 session，清理时向进程组发送终止信号，必要时升级为强制结束。
- Windows 创建独立进程组，清理时使用系统进程树终止能力结束 shell 及其子进程，并等待 shell 回收。
- stdout/stderr 的现有截断上限不变。

### `endless_code.prompt`

新增：

```python
PLAN_MODE_REMINDER = (
    "You are in PLAN MODE. Use only read_file, glob, and grep to investigate. "
    "Do not write or edit files and do not run shell commands. Produce a clear "
    "step-by-step plan, then stop and wait for /do."
)

EXECUTE_DIRECTIVE = "请按上面的计划开始执行。"
```

`SYSTEM_PROMPT` 增加持续使用工具直到任务完成的约定。Plan Mode 的只读限制同时由提示词和实际注入的工具集合保证。

### `endless_code.security`

职责：所有用户可见输出的密钥脱敏及工具参数摘要。

- 函数保持无状态，便于单测。
- TUI 初始化时从所有 ProviderConfig 收集明文密钥和当前环境中可解析的密钥引用；未设置的环境变量跳过，不提前阻止应用启动。
- assistant 最终文本、跨工具 preamble、工具参数、工具结果摘要和异常文本在写入 RichLog 前统一脱敏。
- 原始对话历史和完整工具结果仍在内部保留；本阶段只保证用户可见输出不回显密钥，不改变模型上下文语义。

### `endless_code.tui`

职责：输入、模式状态、事件渲染、用户取消与会话级统计。

主要字段：

```python
self._tool_registry: Registry       # 避免覆盖 Textual App._registry
self._mode: Mode = Mode.NORMAL
self._iteration: int = 0
self._usage_in: int = 0
self._usage_out: int = 0
self._turn_cancel: asyncio.Event | None = None
self._cur_tools: list[ToolDisplay] = []
self._secrets: set[str] = ...
```

输入处理：

- `/exit`、`/quit`：退出。
- `/plan`：切换 `Mode.PLAN`，写入脱敏的模式提示，不发起模型请求。
- `/do`：切换 `Mode.NORMAL`，向 Conversation 添加 `EXECUTE_DIRECTIVE` 用户消息并立即启动 Agent；`/do` 字面值不写入历史。
- 普通文本：添加 user 消息并按当前模式启动 Agent。

按键：

- `Ctrl+D`：沿用程序退出；若当前有 task，先取消并等待工具清理。
- `Ctrl+C`：流式状态只 `set()` 当前取消事件；其他状态退出程序。
- `Esc`：流式状态只 `set()` 当前取消事件；其他状态忽略。

事件消费：

- `text`：累积当前文本；工具开始前将其作为 preamble 写入 scrollback。
- `tool START`：使用 `summarize_tool_args` 建立 `ToolDisplay` 并加入动态区。
- `tool END`：按 `call_id` 移除对应动态项，按事件顺序写入工具行和脱敏结果摘要。
- `usage`：累加输入/输出并刷新状态文本。
- `iteration`：更新动态区轮次。
- `notice`：写入系统提示。
- `err`：提交尚未展示的 partial text，再展示脱敏错误块；等待随后的 `done` 统一清理。
- `done`：提交剩余最终文本，停止计时器、恢复输入和空闲态；不得写 Conversation。

Provider 状态、模式和累计用量通过 `App.sub_title` 显示，Footer 继续显示快捷键。流式动态区显示当前轮次、计时和所有运行中的工具。

Provider 构造或环境变量解析失败时在界面显示错误并保持选择/空闲状态，不允许异常逃逸导致 TUI 崩溃。

### `endless_code.cli` 与依赖

- CLI 接线保持 `Config -> Registry -> EndlessCodeApp`。
- TUI 工具字段改名后继续传入同一 Registry 实例。
- `pyproject.toml` 为 Textual 增加当前主版本上界 `<9`，并分别验证最低声明版本与当前 8.x；避免未来主版本自动进入支持范围。
- pytest 明确异步 fixture loop scope，消除当前弃用警告。
- README 更新 Agent Loop、Plan Mode、取消键和用量展示说明。

## 模块交互

```text
用户普通输入或 /do
  -> TUI 添加一条 user 消息
  -> 创建 turn_cancel，启动 Agent.run
     -> iteration 事件
     -> Provider.stream(history, tools(mode), suffix(mode))
        -> text/tool_calls/usage/done 或 err
     -> 无工具：Agent 保存 assistant 最终文本 -> done
     -> 有工具：Agent 保存 assistant(tool_calls)
        -> 按顺序切分只读并发批/副作用串行批
        -> START 事件 -> 执行 -> END 事件
        -> Agent 保存全部 ToolResult
        -> 下一轮 Provider.stream
  -> TUI 仅消费事件并渲染

Esc/Ctrl+C
  -> turn_cancel.set()
  -> Agent 关闭当前 Provider 流或取消工具 task
  -> BashTool 在取消传播时终止进程树
  -> Agent 补齐工具结果与 assistant 取消说明
  -> notice -> done
  -> TUI 回到 IDLE
```

## 文件组织

```text
endless-code/
├── pyproject.toml                         # Textual 上界、pytest 异步配置
├── README.md                              # Agent Loop 与交互说明
├── src/endless_code/
│   ├── cli.py                             # 保持接线，适配 TUI 构造参数
│   ├── conversation.py                    # 增加 last_role
│   ├── prompt.py                          # Plan/执行提示和循环约定
│   ├── security.py                        # 新增脱敏与安全参数摘要
│   ├── llm/
│   │   ├── __init__.py                    # Usage、StreamEvent、Provider 签名
│   │   ├── openai_provider.py             # system_suffix、usage、可关闭流
│   │   └── deepseek_provider.py           # system_suffix、usage、thinking 保持
│   ├── tool/
│   │   ├── __init__.py                    # read_only 接口、Registry 查询
│   │   ├── bash.py                        # 非零错误、取消时清理进程树
│   │   └── 其余五个工具                    # read_only 属性
│   ├── agent/
│   │   └── __init__.py                    # ReAct 循环、事件、取消、分批执行
│   └── tui/
│       └── app.py                         # 模式、状态、事件渲染、脱敏、按键
└── tests/
    ├── test_agent.py                      # 重写为多轮/停止/并发/取消测试
    ├── test_tool.py                       # 分类、非零退出、跨平台超时清理
    ├── test_conversation.py               # 新增 last_role 测试
    ├── test_llm.py                        # 新增两 Provider 分片/usage/suffix 测试
    ├── test_security.py                   # 新增脱敏与参数摘要测试
    └── test_tui.py                        # 新增 Textual 挂载/历史/命令/取消测试
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 循环归属 | 重写 `Agent.run` | 编排与 UI 解耦，Conversation 写入只有一个所有者 |
| 迭代上限 | 内置 25 | 足够覆盖多步任务，同时限制失控请求和成本 |
| 未知工具阈值 | 连续 3 个“全为未知工具”的迭代 | 给模型纠偏机会；混入已知工具表示仍有进展并重置 |
| 取消信号 | per-turn `asyncio.Event` + `asyncio.wait` 竞速 | 既能保留历史收尾，又能在无新 chunk 时及时打断 |
| 工具并发 | 仅最长连续只读批并发 | 满足顺序语义，不跨副作用边界重排 |
| 工具超时 | Registry 维持唯一 `wait_for` | 避免双重超时；工具负责 cancellation cleanup |
| 命令清理 | 独立进程组/树，取消时显式终止并等待 | 单纯取消 `communicate()` 不会停止子进程 |
| 历史所有权 | Agent 写 assistant/tool，TUI 只写 user | 消除当前重复 assistant 消息并保持协议历史一致 |
| Plan Mode 防线 | 只读工具集合 + 系统提示后缀 | 即使模型忽略提示，也拿不到写入/执行工具 |
| 用量来源 | Provider 从最终流式 usage 块上报 | 输入/输出口径来自服务端实际计费统计 |
| DeepSeek 适配 | 保持 OpenAI 兼容流并保留 `thinking` 请求体 | 与现有实现和配置兼容，改动集中在 usage/suffix |
| 输出脱敏 | TUI 所有输出入口统一处理，工具参数结构化摘要 | 不污染内部历史，同时覆盖用户可见泄漏面 |
| TUI 注册中心字段 | `_tool_registry` | 避免覆盖 Textual 的 `_registry` 私有字段 |
| Textual 版本 | 保留最低版本并增加 `<9` 上界 | 最低声明版本与当前 8.x 都列为显式验证目标，未来主版本需重新验证 |
| 文件拆分 | 仅新增 `security.py`，其余沿用当前单文件 TUI | 避免把本阶段扩大成无关重构 |
