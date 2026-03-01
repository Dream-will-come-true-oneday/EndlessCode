# 多协议 LLM 终端对话客户端 Plan## 技术栈
- 语言：Python 3.12+
- TUI：[Textual](https://textual.textualize.io/)（async-first 的 TUI 框架）+ Rich（Markdown/语法高亮渲染）
  + Textual CSS（样式/布局）；内置 widget 包含 `Input`、`RichLog`、`Static`、`LoadingIndicator`、
  `Markdown`、`OptionList` 等。
- 配置：YAML 解析（`pyyaml`，import 名 `yaml`）
- LLM 通信：官方 Python SDK —— `openai`（`AsyncOpenAI`），均原生支持
  async 流式（SDK 内部已处理 SSE）

## 架构概览（分层）
1. 入口层 `endless_code.cli` —— 加载配置、打印 banner、启动 Textual App。
2. 配置层 `endless_code.config` —— 读取并校验 `.endless-code/config.yaml`，给出 providers 列表。
3. LLM 协议层 `endless_code.llm` —— 定义协议无关的 `Provider` Protocol 与统一消息/流式事件类型；
   deepseek、openai 两个适配器各自封装官方 SDK、统一吐出文本增量（思考增量内部丢弃）。
4. 会话层 `endless_code.conversation` —— 进程内维护多轮历史，提供完整上下文。
5. 提示词/资源 `endless_code.prompt` —— 内置 system prompt 与启动 banner（ASCII 猫）。
6. 终端层 `endless_code.tui` —— Textual App，含状态机（选择/空闲/流式）、输入框、对话区、
   loading 计时、provider 选择列表；以 async task 消费 `Provider.stream(...)` 的事件生成器，
   通过 `call_from_thread`/直接 await 把增量写入 UI。

## 数据流（一轮对话）
用户输入 → TUI 提交 → conversation 追加 user 消息 → 调 `Provider.stream(msgs)`
→ 得到 `AsyncIterator[StreamEvent]` → TUI async task 逐个 `async for` 读文本增量并实时
追加（loading 计时同步进行）→ 收到结束事件 → 用 Rich Markdown 渲染整段 → conversation
追加 assistant 消息 → 回到空闲。

> 备注：Python 的 Textual + asyncio 是 async-first 体系，直接 `async for event in
> provider.stream(...)` 即可驱动 UI；没有 goroutine / channel / `tea.Cmd` 的胶水层。

## 核心数据结构与接口

```python
# ───────── config 层 ─────────
from dataclasses import dataclass, field
from typing import Literal

@dataclass
class ProviderConfig:
    name: str                          # 状态栏左侧显示
    protocol: Literal["deepseek", "openai"]
    api_key: str
    model: str                         # 状态栏右侧显示
    base_url: str | None = None        # None 则用 SDK 默认端点
    thinking: bool = False             # 仅 deepseek 生效

@dataclass
class Config:
    providers: list[ProviderConfig] = field(default_factory=list)

def load(path: str) -> Config: ...     # 加载 + 校验

# ───────── llm 层（协议无关）─────────
from typing import Protocol, AsyncIterator

@dataclass
class Message:
    role: Literal["user", "assistant"]
    content: str

@dataclass
class StreamEvent:
    text: str = ""                     # 文本增量
    done: bool = False                 # 本轮正常结束
    err: Exception | None = None       # 出错（与 done 互斥）

class Provider(Protocol):
    @property
    def name(self) -> str: ...         # -> 状态栏左
    @property
    def model(self) -> str: ...        # -> 状态栏右
    # 发起一轮流式对话；内部注入内置 system prompt 与 thinking 配置；
    # 思考增量内部丢弃；以 async generator 吐出 StreamEvent；
    # 调用方 cancel() 该 task 即终止。
    def stream(self, msgs: list[Message]) -> AsyncIterator[StreamEvent]: ...

def new_provider(cfg: ProviderConfig) -> Provider: ...   # 按 protocol 构造适配器

# ───────── conversation 层 ─────────
class Conversation:
    def __init__(self) -> None:
        self._messages: list[Message] = []
    def add_user(self, text: str) -> None: ...
    def add_assistant(self, text: str) -> None: ...
    def messages(self) -> list[Message]: ...    # 返回副本

# ───────── prompt 层 ─────────
SYSTEM_PROMPT: str = "..."             # 内置固定 system prompt
CAT_BANNER: str = "..."                # ASCII 猫
def render_banner(version: str, cwd: str) -> str: ...

# ───────── tui 层 ─────────
from enum import Enum

class SessionState(Enum):
    SELECTING = "selecting"            # 多 provider 时的选择界面
    IDLE = "idle"                      # 等待用户输入
    STREAMING = "streaming"            # 等待/接收模型流（loading + 计时）

class EndlessCodeApp(App):
    # 关键 reactive / 成员
    state: SessionState
    providers: list[ProviderConfig]
    provider: Provider | None
    conv: Conversation
    cur_reply: str                     # 本轮 assistant 增量缓冲（动态区显示，done 后追加到 RichLog）
    turn_start: float                  # time.monotonic() 计时起点
    _stream_task: asyncio.Task | None  # 当前流式消费 task
    _timer: Timer | None               # Textual 内置定时器，用于秒数计时刷新
    # 完成的消息（用户输入 / 渲染后的助手回复 / 错误）通过 RichLog.write(...) 持久追加，
    # 滚回历史用 RichLog 自带的滚动；不再单独保留消息列表。

    async def submit(self, text: str) -> None: ...
    async def _consume_stream(self) -> None: ...     # async for event in provider.stream(...)
```

## 模块设计### 模块 `endless_code.config`
职责：读取并校验 `.endless-code/config.yaml`，产出 providers 列表。
对外接口：`load(path) -> Config`；`Config.providers`。
校验规则：列表非空；每项 `name` / `protocol` / `api_key` / `model` 非空；
         `protocol ∈ {"deepseek", "openai"}`。任一不满足 → 抛出 `ConfigError` 携带可读信息
         （指明哪个 provider 的哪个字段）。
依赖：`pyyaml`、标准库 `pathlib` / `os`。

### 模块 `endless_code.llm`
职责：定义协议无关的 `Provider` Protocol 与统一消息/事件类型；按 protocol 构造适配器。
对外接口：`Provider`（typing.Protocol）、`Message`、`StreamEvent`、`new_provider(cfg) -> Provider`。
子单元：
  - deepseek 适配器：封装 `openai.AsyncOpenAI`。把 `list[Message]` 转为 SDK 的
    messages 入参，注入 `system=SYSTEM_PROMPT`、按 `cfg.thinking` 设 `thinking={"type":"enabled","budget_tokens":...}`；
    使用 `async with client.messages.stream(...) as stream: async for event in stream:` 迭代，
    取 `event.type == "content_block_delta"` 中的 `text` delta → `StreamEvent(text=...)`；
    遇 thinking delta 丢弃；正常结束 yield `StreamEvent(done=True)`；
    异常路径 yield `StreamEvent(err=exc)`。`cfg.base_url` 非空时传 `base_url=...` 构造 client。
  - openai 适配器：封装 `openai.AsyncOpenAI`。把 `list[Message]` 转为 `messages=[...]`，
    首条插入 `{"role":"system","content": SYSTEM_PROMPT}`；
    `async for chunk in await client.chat.completions.create(..., stream=True):` 迭代，
    取 `chunk.choices[0].delta.content` 非空时 yield `StreamEvent(text=...)`；
    正常结束 yield `StreamEvent(done=True)`；异常 yield `StreamEvent(err=exc)`。
    `cfg.base_url` 非空时传 `base_url=...`；`thinking` 字段忽略。
共同点：两适配器都把 `stream(...)` 实现为 `async def stream(...) -> AsyncIterator[StreamEvent]`
       的 async generator；调用方 cancel 对应 task 时，`async for` 自然抛 `CancelledError`，
       SDK 流由 `async with` 上下文自动清理。
依赖：`openai`、本模块 `prompt`、`config`。

### 模块 `endless_code.conversation`
职责：进程内维护单会话多轮历史（user/assistant 交替）。
对外接口：`add_user`、`add_assistant`、`messages()`。
依赖：`llm`（`Message` 类型）。

### 模块 `endless_code.prompt`
职责：提供内置 system prompt 与 ASCII 猫 banner 文本。
对外接口：`SYSTEM_PROMPT` 常量、`CAT_BANNER` 常量、`render_banner(version, cwd) -> str`。
依赖：无。

### 模块 `endless_code.tui`
职责：Textual App，承载选择/对话/流式/错误的全部交互与渲染。
对外接口：`EndlessCodeApp(providers: list[ProviderConfig])`；`App.run()` 或 `await App.run_async()`。
内部职责：
  - 启动时若 `len(providers) > 1` → `SessionState.SELECTING`（`OptionList` 选择）；
    否则直接进 `SessionState.IDLE` 并 `new_provider(providers[0])`。
  - IDLE：`Input` 接收输入；Enter 提交（Alt+Enter 插入换行——`Input` 默认单行，需挂自定义
    binding 或用 `TextArea` 替代以支持多行；本项目用 `TextArea` 承担多行 + Alt+Enter 换行）；
    `/exit` 或 Ctrl+C 退出。
  - 提交：`conv.add_user(text)` → 启动 `self._stream_task = asyncio.create_task(self._consume_stream())`
    → `RichLog.write(user_block(text))` 追加用户块 → `turn_start = time.monotonic()`、
    `cur_reply = ""`、清空输入框 → 切 `STREAMING` → `_timer = self.set_interval(0.1, self._tick)`
    刷新 "Imagining… (Ns)"。
  - `_consume_stream`：`async for event in self.provider.stream(self.conv.messages()):`
    - `event.text` 非空 → `cur_reply += event.text`、更新动态区显示；
    - `event.done` → 用 `rich.markdown.Markdown(cur_reply)` 渲染 → `RichLog.write(...)` 追加
      → `conv.add_assistant(cur_reply)` → 清缓冲、停止 timer、回 `IDLE`；
    - `event.err is not None` → `RichLog.write(error_block(event.err))` → 停止 timer、回 `IDLE`。
  - 窗口尺寸：Textual 自动 reflow，必要时通过 CSS 设置 `Markdown` / `RichLog` 的 `width: 1fr`
    与最大宽度（N6）。
依赖：`textual`、`rich`、本项目 `llm`、`conversation`、`config`、`prompt`。

### 模块 `endless_code.cli`（入口）
职责：装配与启动。
流程：`config.load(...)` → `prompt.render_banner(version, cwd)` 打印 → `EndlessCodeApp(cfg.providers).run()`。
失败处理：配置错误打印可读信息并 `sys.exit(1)`（N4）。
依赖：`config`、`tui`、`prompt`。

## 模块交互### 调用链（启动）
```
main() → config.load(".endless-code/config.yaml")
       → 若 ConfigError：打印可读错误、sys.exit(1)
       → print(prompt.render_banner(version, cwd))
       → EndlessCodeApp(cfg.providers).run()
         → len(providers) == 1：内部 new_provider(cfg[0]) 构造 provider，进 IDLE
         → len(providers)  > 1：进 SELECTING
```

### 时序（多 provider 选择）
```
SELECTING:
  OptionList 显示各 provider 的 name + model
  用户方向键移动、Enter 选定
  → new_provider(选定 cfg) 构造 provider
  → 状态栏更新为 provider.name / provider.model
  → 进 IDLE
```

### 时序（一轮对话，核心）
```
IDLE:
  用户在 TextArea 输入，Enter 提交
  → conv.add_user(text)
  → RichLog.write(user_block(text))
  → turn_start = time.monotonic()；cur_reply = ""
  → self._stream_task = asyncio.create_task(self._consume_stream())
  → self._timer = self.set_interval(0.1, self._tick)
  → 切 STREAMING

STREAMING（async 循环）:
  _tick → 更新动态区底部 "Imagining… (Ns)"
  _consume_stream 内 async for event:
    - event.text → cur_reply += event.text；更新动态区显示
    - event.done → Rich Markdown 渲染 → RichLog.write(...) → conv.add_assistant → 停 timer → 回 IDLE
    - event.err  → RichLog.write(error_block) → 停 timer → 回 IDLE（不退出）
  期间输入框不接受提交（N1：UI 仍响应，可滚动 RichLog 回看完成内容）
```

### 时序（退出）
```
任意状态：输入 "/exit"（IDLE 识别）或 Ctrl+C
  → 若存在 self._stream_task：task.cancel() 终止进行中的流
  → App.exit() → Textual 还原终端 raw mode（N7）
```

### 数据流图
```
config.yaml ──load──> list[ProviderConfig] ──new_provider──> Provider
用户输入 ──> conversation(+user) ──messages()──> Provider.stream
Provider.stream ──async generator──> StreamEvent (text / done / err)
                                         │
                                         └──async for──> EndlessCodeApp._consume_stream
                                                            │
                                                            ├── text  → 动态区追加
                                                            └── done  → rich.Markdown 渲染
                                                                       → RichLog.write(...)
                                                                       → conversation(+assistant)
```

## 文件组织
```
endless_code/
├── pyproject.toml                  — PEP 621 项目元数据、依赖、脚本入口
├── README.md
├── .endless-code/
│   └── config.yaml                 — 运行配置（providers 列表）；附 config.yaml.example
├── src/
│   └── endless_code/
│       ├── __init__.py
│       ├── __main__.py             — 允许 `python -m endless_code`
│       ├── cli.py                  — 入口：加载配置、打印 banner、启动 TUI
│       ├── config.py               — Config / ProviderConfig 类型、load 与校验
│       ├── prompt.py               — SYSTEM_PROMPT、CAT_BANNER、render_banner
│       ├── conversation.py         — 单会话多轮历史
│       ├── llm/
│       │   ├── __init__.py         — Provider Protocol、Message、StreamEvent、new_provider 工厂
│       │   ├── deepseek_provider.py  — deepseek 适配器（封装 AsyncOpenAI（DeepSeek 兼容））
│       │   └── openai_provider.py     — openai 适配器（封装 AsyncOpenAI）
│       └── tui/
│           ├── __init__.py
│           ├── app.py              — EndlessCodeApp、状态机、Run
│           ├── stream.py           — _consume_stream、_tick 计时
│           ├── select.py           — provider 选择（OptionList）
│           └── view.py             — 各状态的渲染拼装、状态栏、错误样式、markdown 定型
└── tests/
    ├── test_config.py
    └── test_conversation.py
```
说明：
- 依赖版本预期：`textual`、`rich`、`openai`、`pyyaml`。在 `pyproject.toml` 中
  以 `dependencies = [...]` 声明，锁文件用 `uv.lock`（推荐 `uv`）或 `pip-compile` 生成的
  `requirements.txt`。
- `tui/` 拆 4 个文件按职责切分；若实现时过碎可合并，不影响接口。
- `.endless-code/config.yaml` 含真实密钥，应在 `.gitignore` 忽略；提交一份 `config.yaml.example`。
- `pyproject.toml` 里通过 `[project.scripts] endless_code = "endless_code.cli:main"` 暴露 CLI 入口；
  装好后既可 `endless_code` 也可 `python -m endless_code`。

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 语言 | Python 3.12+ | 项目既定（endless_code python 线）；3.12 的 typing/`asyncio.TaskGroup` 等更舒服 |
| TUI 框架 | Textual | async-first，原生跑在 asyncio 上；CSS 样式、widget 丰富；与流式 SDK 天然契合 |
| markdown 渲染 | Rich 的 `rich.markdown.Markdown` | Textual 内部即用 Rich；代码块语法高亮、列表、强调齐全；宽度自适应（N6） |
| LLM 通信 | 官方 Python SDK（`openai`） | DeepSeek 和 OpenAI 都使用 OpenAI 兼容 API；SDK 内置 SSE 解析与 async 流，省去手写；`AsyncOpenAI` 即可 |
| 协议抽象 | 统一 `Provider` Protocol + 两适配器 | 满足 F3/N3；上层不感知协议 |
| 流式接入 TUI | `async for event in provider.stream(...)` 直跑在 Textual 的事件循环里 | Python async-first，无需 channel/Cmd 胶水；界面不阻塞（N1） |
| 流式渲染策略 | 流式纯文本 + done 后 `rich.markdown.Markdown` 定型 | markdown 需完整块；增量渲染会抖动（F8） |
| 渲染模型 | inline + `RichLog.write(...)` 追加（Claude Code 风格） | 完成消息持久写入 RichLog，可滚动回看；仅"输入框 + 正在流式的回复 + 状态栏"为动态重绘区 |
| thinking | 仅 deepseek 生效（`thinking={"type":"enabled",...}`）；openai 忽略 | OpenAI reasoning 不经 chat.completions 返回正文；思考内容本就丢弃 |
| 计时 | `turn_start = time.monotonic()` + `set_interval(0.1, ...)` 计算 elapsed | 自请求即计时，Textual 内置 timer 驱动（F12） |
| provider 选择 | 单份直进 / 多份 `OptionList` 选择 | 满足 F2 |
| 历史 | 进程内 `list[Message]`，单会话 | 满足 F6；不持久化 |
| system prompt | 内置常量，适配器注入 | 满足 F4；conversation 保持纯 user/assistant |
| 配置 | `.endless-code/config.yaml` + `pyyaml`；密钥入 `.gitignore` | 用户既定路径；N5 密钥安全 |
| 错误处理 | 运行时错误经 `StreamEvent.err` 显示，不退出 | 满足 F11 |
| 类型/质量 | `typing.Protocol` + `dataclass`；`ruff format` + `ruff check` + 可选 `mypy` | 简洁，无运行时依赖（vs pydantic）；ruff 一站式格式化/lint |