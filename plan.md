# Endless Code 上下文管理 Plan

## 架构概览

新增 `src/endless_code/compact/` 作为上下文管理唯一入口，分为：

- `state.py`：会话目录、替换决策账本、文件追踪、自动熔断器。
- `const.py`：所有硬编码阈值。
- `token.py`：usage 锚点和增量估算。
- `layer1.py`：工具结果落盘、预览、聚合预算和决策冻结。
- `summary_prompt.py`：摘要请求序列化和 `<summary>` 解析。
- `recovery.py`：文件快照、工具定义和边界提示渲染。
- `layer2.py`：近期原文、摘要请求、PTL 自重试、自动/强制摘要。
- `compact.py`：`manage_context` 编排入口和触发类型。

`Agent` 负责每轮选择工具定义、调用 `manage_context`、更新 usage、记录 `read_file` 成功结果和处理主请求 PTL。`EndlessCodeApp` 负责持有 `SessionRuntime`、分发命令和渲染压缩状态。

## 核心数据结构和接口

### `SessionRuntime`

放在 `src/endless_code/agent/__init__.py` 或同包 `runtime.py`，由 TUI 创建并跨轮复用：

```python
@dataclass
class SessionRuntime:
    replacement: ContentReplacementState
    recovery: RecoveryState
    auto_tracking: CompactCircuitBreaker
    session: SessionContext
    context_window: int
    usage_anchor: int = 0
    anchor_msg_len: int = 0
    run_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
```

### `compact.ManageInput/ManageOutput`

`ManageInput` 包含当前 `Conversation`、Provider、模型名、context window、本轮实际工具定义列表、所有 SessionRuntime 状态、锚点和入口估算 token。`ManageOutput` 返回 `before_tokens`、`after_tokens` 和是否执行摘要所需的信息。

```python
class TriggerKind(Enum):
    AUTO = "auto"
    MANUAL = "manual"
    EMERGENCY = "emergency"


async def manage_context(input: ManageInput) -> ManageOutput: ...
```

### 状态接口

```python
def new_session_context(workspace: str) -> SessionContext: ...


class ContentReplacementState: ...


class RecoveryState: ...


class CompactCircuitBreaker: ...


def offload_and_snip(messages, state, session) -> list[Message]: ...
def estimate_tokens(anchor, messages, anchor_msg_len) -> int: ...
```

### Agent 事件

在现有 `Event` 增加：

```python
class CompactPhase(Enum):
    BEFORE_AUTO = "before_auto"
    AFTER_AUTO = "after_auto"
    BEFORE_EMERGENCY = "before_emergency"
    AFTER_EMERGENCY = "after_emergency"


@dataclass
class CompactEvent:
    phase: CompactPhase
    before_tokens: int = 0
    after_tokens: int = 0
    err: Exception | None = None
```

压缩开始和结束均由 Agent yield `Event(compact=...)`，TUI 统一渲染。

## 模块交互

1. CLI 加载 ProviderConfig，计算有效 context window，创建 MCP Manager、Registry 和 TUI。
2. TUI 选择 Provider 后创建 SessionRuntime；每轮复用 runtime，向 Agent 传入同一 runtime。
3. Agent 每轮开头根据 `Mode` 取得一次 `definitions` 或 `read_only_definitions`，用该同一列表构造 `ManageInput` 和主请求 `Request.tools`。
4. AUTO 调用 `manage_context`：第一层替换并写回 Conversation，重新估算，达到阈值时触发摘要；MANUAL 跳过第一层和阈值；EMERGENCY 先强制第一层再摘要。
5. 摘要使用同一 Provider，但 `Request.tools=[]`，解析摘要后以新历史替换 Conversation。
6. 主请求结束后用最后一次主对话 usage 更新 runtime 锚点；工具执行完成后，在 `add_tool_results` 前记录成功的 `read_file` 内容。
7. 主请求出现 `PromptTooLongError` 时执行 EMERGENCY，成功后只重试同一轮一次。

## TUI 适配

当前 UI 只有 `src/endless_code/tui/app.py`，不新增旧项目的 `stream.py`。在 `EndlessCodeApp` 中：

- `on_mount` 创建 SessionRuntime 或在首次 Provider 激活后创建。
- `_consume_agent_events` 使用 runtime，不再让每轮 Agent 丢失状态。
- `_handle_idle_input` 交给 `commands.py` 的 `BUILTIN_COMMANDS`；命令 handler 只操作 TUI 状态或调用 `run_force_compact`。
- `_update_streaming` 或对应事件分支显示“正在压缩上下文…”、“上下文撞墙，自动压缩中…”和 token 变化。
- `/compact` 使用 runtime 的锁，与当前 run 串行。

## 配置适配

保留现有 `src/endless_code/config.py` 的 dataclass 和 YAML 结构，只给 `ProviderConfig` 追加 `context_window: int = 0`。在同文件增加 `effective_context_window`：Anthropic 为 200000，OpenAI/DeepSeek 为 128000，显式正数优先。

`.endless-code/config.yaml.example` 为三个 provider 增加可选字段注释。会话目录使用 `.endless-code/sessions`，并加入 `.gitignore`。

## 技术决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 消息替换 | 深拷贝后生成新列表 | 不污染当前请求对象，便于重试和测试 |
| 并发保护 | `threading.RLock` + async 主循环串行 | 文件追踪测试会使用线程，状态仍需跨线程安全 |
| 摘要调用 | 复用当前 Provider `stream(Request)` | 不新增 Provider 抽象，兼容现有三种协议 |
| PTL 识别 | `PromptTooLongError` 哨兵 | Agent 不依赖 SDK 异常类型 |
| 精度 | usage 锚点 + 字符/3.5 | 不增加 tokenizer 依赖 |
| 会话持久化 | 只持久化工具结果 | 保留可重读原文，避免持久化完整对话和密钥 |
