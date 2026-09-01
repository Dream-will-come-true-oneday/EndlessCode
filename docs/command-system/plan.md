# 斜杠命令系统 Plan

> 依据：已批准的 `spec.md`（F1–F8、N1–N4、AC1–AC9）

## 架构概览

新建独立包 `endless_code.command`，承载命令系统的全部核心逻辑，不依赖 Textual/Rich 渲染：

```text
用户输入 (Enter)
   │
EndlessCodeApp.on_input_submitted
   │  ① 是命令？
   ├─ 是 → Dispatcher.try_dispatch(text) → Registry 查表 → CommandSpec.handler(host, args)
   │                                                  │
   │                                          通过 CommandHost 抽象接口操作 App
   └─ 否 → 原有对话流程 _handle_idle_input → Agent
```

组件划分：

| 组件 | 职责 | 对应需求 |
|---|---|---|
| `types` | 命令元数据、解析结果、会话信息、异常类型 | F1、F2 |
| `parser` | 斜杠输入解析（名称/参数切分、大小写归一） | F2 |
| `registry` | 注册中心：注册、冲突检测、查找、补全、可见列表 | F1、F6 |
| `host` | CommandHost 抽象接口（UI 控制层协议） | F4 |
| `dispatcher` | 分发器：查表、执行、异常兜底、未知命令引导 | F3、F5、F7 |
| `prompts` | AI 类命令的预设提示词模板 | F8（review/fix/explain） |
| `builtin` | 14 条内置命令的 handler 与注册 | F8 |
| `EndlessCodeApp` | 实现 CommandHost；接线分发器；Tab 补全；状态栏提示 | F4–F7 |

## 核心数据结构与接口

### types.py

```python
class CommandKind(Enum):
    """命令执行模式。"""

    LOCAL = "local"  # 纯本地：只读/本地状态，立即返回
    UI = "ui"  # 界面状态：改变界面或触发异步流程
    AI = "ai"  # AI 提示词：送入对话流由 AI 处理


@dataclass(frozen=True)
class CommandSpec:
    """一条命令的完整元数据。"""

    name: str  # 规范名，小写，带 "/"，如 "/help"
    description: str  # 一句话描述
    usage: str  # 用法，如 "/help [命令名]"
    kind: CommandKind
    handler: Callable[["CommandHost", str], None]  # handler(host, args)
    aliases: tuple[str, ...] = ()  # 别名，带 "/"，小写
    arg_hint: str = ""  # 参数提示，如 "[命令名]"
    hidden: bool = False  # 隐藏：不进帮助与补全


@dataclass(frozen=True)
class ParsedCommand:
    """解析结果。"""

    name: str  # 已转小写，不含参数的命令名（含 "/"）
    args: str  # 第一个空格之后的原文（已 strip）


@dataclass(frozen=True)
class SessionInfo:
    """综合状态命令所需快照。"""

    version: str
    provider: str
    model: str
    mode: str
    tokens_in: int
    tokens_out: int
    session_id: str
    message_count: int


class CommandError(Exception):
    """命令系统错误基类（注册冲突等）。"""
```

### parser.py

```python
def parse_command(text: str) -> ParsedCommand | None:
    """非 '/' 开头返回 None；否则按第一个空格切分。
    命令名转小写；'/' 后为空时 name 为空串（由分发器判定非法）。"""
```

### registry.py

```python
class Registry:
    def register(self, spec: CommandSpec) -> None:
        """注册命令。名称或别名（大小写不敏感）与已注册命令冲突时
        抛出 CommandError，消息中指明冲突的命令名。
        名称必须以 '/' 开头且小写，否则抛 CommandError。"""

    def lookup(self, name: str) -> CommandSpec | None:
        """大小写不敏感按名称或别名查找。"""

    def visible(self) -> list[CommandSpec]:
        """按名称排序返回非隐藏命令。"""

    def completions(self, prefix: str) -> list[str]:
        """前缀（大小写不敏感）匹配的全部可见命令名与别名，排序返回。"""
```

### host.py

```python
class CommandHost(Protocol):
    """命令实现可用的全部界面能力；由 EndlessCodeApp 实现。"""

    # 输出
    def show_notice(self, text: str) -> None: ...
    def show_error(self, text: str) -> None: ...
    # 对话流
    def send_user_message(self, text: str) -> None: ...  # AI 命令入口
    def get_last_error(self) -> str: ...  # 最近一次错误文本，无则空串
    def get_last_reply(self) -> str: ...  # 最近一次完整 AI 回复，无则空串
    # 权限模式
    def get_mode(self) -> Mode: ...
    def set_mode(self, mode: Mode) -> None: ...
    # 状态与记忆
    def get_session_info(self) -> SessionInfo: ...
    def get_memory_index(self) -> str: ...  # 长期记忆索引，无则空串
    def show_audit(self) -> None: ...  # 复用现有审计面板逻辑
    # 会话与流程
    def start_compact(self) -> None: ...
    def start_resume(self) -> None: ...
    def enter_plan(self) -> None: ...
    def start_execute(self) -> None: ...
    def clear_session(self) -> None: ...
    def quit_app(self) -> None: ...
```

### dispatcher.py

```python
class Dispatcher:
    def __init__(self, registry: Registry, host: CommandHost) -> None: ...

    def try_dispatch(self, text: str) -> bool:
        """命令分流入口：
        1. parse_command 返回 None → 返回 False（不是命令）；
        2. 命令名为空 → show_error 提示非法输入，返回 True；
        3. 未找到命令 → show_notice「未知命令 …，输入 /help 查看帮助」，返回 True；
        4. 执行 handler；捕获 Exception → show_error，返回 True。"""

    def complete(self, text: str) -> tuple[str, list[str]]:
        """Tab 补全。返回 (补全后的输入框文本, 候选列表)。
        候选唯一 → 文本补全为规范名加一个空格，候选列表为空；
        候选多个 → 文本不变，候选列表按序返回；
        无匹配或非命令输入 → 文本不变，候选列表为空。"""
```

### prompts.py

```python
REVIEW_PROMPT: str  # 代码审查提示词：要求 AI 用工具查看当前改动
# （如 git diff）并从正确性、风险、可读性给出审查意见；
# 有用户参数时拼接到末尾作为审查重点
FIX_PROMPT = "…{error}…"  # 修复提示词模板：携带最近错误文本，要求定位原因并修复
EXPLAIN_PROMPT = "…{context}…"  # 解释提示词模板：携带最近回复/工具结果，要求逐段解释


def build_review_prompt(args: str) -> str: ...
def build_fix_prompt(error: str, args: str) -> str: ...
def build_explain_prompt(context: str, args: str) -> str: ...
```

### builtin.py

```python
def register_builtin_commands(registry: Registry) -> None:
    """注册全部 14 条内置命令（见下表）。"""
```

| 命令 | 别名 | 类型 | handler 要点 |
|---|---|---|---|
| `/help` | `/?` | LOCAL | 无参：列出 `registry.visible()` 的名称、别名、描述；有参：`lookup` 后输出描述、用法、参数提示、类型；查不到则提示 |
| `/exit` | `/quit` | LOCAL | `host.quit_app()` |
| `/compact` | — | UI | `host.start_compact()` |
| `/clear` | — | UI | 参数非 `yes` 时 `show_notice` 要求输入 `/clear yes` 确认；参数为 `yes` 时 `host.clear_session()` |
| `/plan` | — | UI | `host.enter_plan()` |
| `/do` | — | UI | `host.start_execute()` |
| `/resume` | — | UI | `host.start_resume()` |
| `/audit` | — | LOCAL | `host.show_audit()` |
| `/memory` | `/mem` | LOCAL | `host.get_memory_index()`；空则提示未配置记忆；否则面板展示 |
| `/permissions` | `/perm` | UI | 无参：`show_notice` 当前模式与四档可选名；有参：`parse_mode` 成功则 `set_mode` 并提示，失败则提示合法取值 |
| `/status` | — | LOCAL | `host.get_session_info()` 格式化输出全部字段 |
| `/review` | — | AI | `host.send_user_message(build_review_prompt(args))` |
| `/fix` | — | AI | `host.get_last_error()` 为空提示「没有可修复的最近错误」；否则 `send_user_message(build_fix_prompt(...))` |
| `/explain` | — | AI | `host.get_last_reply()` 为空提示「没有可解释的最近回复」；否则 `send_user_message(build_explain_prompt(...))` |

`parse_mode` 直接复用 `endless_code.permission.parse_mode`。

## 模块设计

### command 包（新建）

- **职责：** 命令注册、解析、分发、补全与全部内置命令逻辑。
- **对外接口：** `Registry`、`Dispatcher`、`parse_command`、`register_builtin_commands`、`CommandHost`、`CommandSpec`、`CommandKind`、`SessionInfo`、`CommandError`。
- **依赖：** 仅 `endless_code.permission`（`Mode`、`parse_mode`）与标准库；不依赖 `tui`、`agent`、`llm`。

### tui/app.py（修改）

- **职责：** 实现 `CommandHost`，接线分发、补全与状态栏。
- **改动点：**
  1. `__init__`：构建 `Registry`（`register_builtin_commands`）与 `Dispatcher(self)`；新增 `_last_error: str = ""`、`_last_reply: str = ""`。
  2. `on_input_submitted` / `_handle_idle_input`：现有 `dispatch_command(self, text)` 调用替换为 `self._dispatcher.try_dispatch(text)`。
  3. 新增 host 方法：`show_notice`/`show_error` 复用 `_write_notice`/`_write_error`；`send_user_message` 委托 `_start_turn(text)`；`quit_app` 委托 `action_quit`；`start_compact`→`_command_compact`；`start_resume`→`_command_resume`；`enter_plan`→`_command_plan`；`start_execute`→`_command_do`；`show_audit`→`_command_audit`；`get_mode`/`set_mode` 读写 `_mode` 并 `_update_status`；`get_session_info` 汇总现有字段；`get_memory_index` 委托 `self._memory_manager.load_index()`（无 manager 返回空串）。
  4. `clear_session`：仅 `IDLE` 状态允许；关闭当前 `Writer`，截断其 `conversation.jsonl`，重建 `Writer` 与 `Conversation`，`show_notice` 报告已清空。
  5. `_write_error`：记录 `_last_error`；`_flush_reply(final=True)`：记录 `_last_reply`。
  6. Tab 补全：`BINDINGS` 增加 `("tab", "complete_command", "补全命令")`；`action_complete_command` 仅在输入以 `/` 开头时调用 `dispatcher.complete`，唯一匹配写回输入框，多匹配将候选列表以 `show_notice` 展示。
  7. 状态栏：`_update_status` 的 `sub_title` 末尾追加 ` | /help 查看命令`。
  8. 删除私有方法 `_command_exit`（由 `quit_app` 取代），保留 `_command_plan`/`_command_do`/`_command_compact`/`_command_resume`/`_command_audit` 作为 host 方法的实现体。

### tui/commands.py（删除）

- 现有 `BUILTIN_COMMANDS` 与 `dispatch_command` 全部被 `command` 包取代；该文件无其他引用方。

## 模块交互

### 普通命令执行（以 `/permissions plan` 为例）

```text
Enter → on_input_submitted → dispatcher.try_dispatch("/permissions plan")
  → parse_command → ParsedCommand("/permissions", "plan")
  → registry.lookup → CommandSpec(kind=UI)
  → handler(host, "plan")
      → parse_mode("plan") → Mode.PLAN
      → host.set_mode(Mode.PLAN) → app._mode 更新 + _update_status()
      → host.show_notice("已切换到 PLAN 模式")
```

### AI 命令执行（以 `/review` 为例）

```text
handler → build_review_prompt(args) → host.send_user_message(prompt)
  → app._start_turn(prompt) → 正常对话流（面板显示用户消息、Agent 运行）
```

### Tab 补全

```text
输入 "/pe" + Tab → action_complete_command → dispatcher.complete("/pe")
  → completions 返回 ["/permissions", "/perm"]
  → 多匹配 → show_notice 展示候选；若输入 "/per" 则唯一匹配
  → 输入框写回 "/permissions "
```

## 文件组织

```text
src/endless_code/command/
├── __init__.py    # 导出公共接口
├── types.py       # CommandKind / CommandSpec / ParsedCommand / SessionInfo / CommandError
├── parser.py      # parse_command
├── registry.py    # Registry（注册、冲突检测、查找、补全）
├── host.py        # CommandHost Protocol
├── dispatcher.py  # Dispatcher（分流、异常兜底、补全编排）
├── prompts.py     # AI 命令预设提示词与构造函数
└── builtin.py     # register_builtin_commands（14 条命令）

修改：
├── src/endless_code/tui/app.py          # 实现 host、接线、Tab 补全、状态栏
删除：
├── src/endless_code/tui/commands.py     # 被 command 包取代
新增测试：
├── tests/test_command_registry.py       # 注册、冲突、查找、补全
├── tests/test_command_parser.py         # 解析规则
├── tests/test_command_builtin.py        # 内置命令逻辑（FakeHost，无 Textual）
└── tests/test_tui.py                    # 增补：分流、/clear、/status、Tab 补全
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 包位置 | 独立 `endless_code/command/` | 命令核心与渲染无关，独立包满足 N1 纯单测，避免 `tui` 语义污染 |
| 抽象方式 | `CommandHost` Protocol + handler 函数 `(host, args)` | handler 为纯函数便于测试；Protocol 无需 App 显式继承，保持现有类结构 |
| `/clear` 确认 | 二次输入 `/clear yes` | 无需模态弹窗与状态机，实现成本最低且满足确认要求 |
| `/clear` 持久化 | 截断当前 `conversation.jsonl` 并重建 Writer | 复用现有 Writer 语义（首条写 model），不引入新持久化概念 |
| 补全多匹配展示 | 聊天区候选列表通知 | 不引入新的浮层组件，符合现有视觉模式；隐藏命令天然被 `completions` 过滤 |
| AI 命令上下文 | App 记录 `_last_error` / `_last_reply` 供 host 读取 | `/fix`、`/explain` 需要最近上下文，现有事件循环已持有这些数据，零额外开销 |
| 旧 `tui/commands.py` | 直接删除 | 仅被 `app.py` 引用，保留会造成双套命令系统并存 |
| `kind` 字段用途 | 元数据展示与分类，不改变分发路径 | 三类命令都经同一 `handler(host, args)` 执行，差异体现在 handler 对 host 的调用；kind 用于帮助输出与测试断言，避免过度设计 |
