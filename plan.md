# 工具系统 Plan

> 基于已批准的 spec.md。本文档与语言相关（Python 3.12+）。SDK 调用方式已对 `anthropic`（`AsyncAnthropic`，支持 tool_use streaming）、`openai`（`AsyncOpenAI`，支持 chat.completions tool_calls streaming）实测核对。

## 架构概览

在 ch02「provider → conversation → tui」三件套之上，新增两个包并扩展三处：

- **mewcode.tool（新建）**：统一工具抽象 `Tool`、执行结果 `Result`、注册中心 `Registry`、6 个核心工具。零外部依赖，不感知 LLM 协议。
- **mewcode.agent（新建）**：承载「单轮闭环」编排——请求#1（带工具）→ 收集工具调用 → 注册中心执行 → 结果回灌进 `Conversation` → 请求#2（续答）→ 最终文本 → 停。对外吐出一条 `Event` async generator 供 TUI 渲染。只依赖 `llm`、`tool`、`conversation`，不 import anthropic/openai，保持协议无关。
- **mewcode.llm（扩展）**：`Message`/`StreamEvent` 增加工具字段；新增协议无关类型 `ToolCall`/`ToolResult`/`ToolDefinition` 与 `ROLE_TOOL` 常量；`Provider.stream` 增加 `tools` 参数；两个适配器注入工具定义、解析流式工具调用、回灌工具结果。
- **mewcode.conversation（扩展）**：新增「assistant 工具调用回合」与「工具结果回合」的追加方法。
- **mewcode.prompt（扩展）**：`SYSTEM_PROMPT` 增补 Agent 角色与工具使用约定。
- **mewcode.tui（扩展）**：`submit` 改走 `Agent.run`；事件消费 task 处理工具事件；渲染 Claude Code 风格工具行与执行指示。
- **cli.py（扩展）**：构造 `tool.new_default_registry()` 并注入 `MewCodeApp`。

依赖方向（无环）：`tool → llm`；`conversation → llm`；`agent → {llm, tool, conversation}`；`tui → {agent, tool, conversation, llm, prompt}`；`llm → {config, prompt}`。

## 核心数据结构### llm 包（`__init__.py` 扩展）

```python
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol

# 消息角色——新增 ROLE_TOOL。
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"  # 携带工具执行结果的回合

@dataclass
class ToolCall:
    """协议无关地承载模型发起的一次工具调用（流式拼接完成后）。"""
    id: str            # provider 侧调用 id；回灌结果时配对
    name: str          # 工具名（注册中心按名查找）
    input: str         # 拼接完成的 JSON 参数字符串（raw JSON）

@dataclass
class ToolResult:
    """协议无关地承载一次工具执行结果。"""
    tool_call_id: str  # 对应 ToolCall.id
    content: str       # 执行产出（成功内容或结构化错误文本）
    is_error: bool = False  # 是否为错误结果（F9）

@dataclass
class ToolDefinition:
    """注册中心导出的协议无关工具定义。"""
    name: str
    description: str
    input_schema: dict[str, Any]  # 完整 JSON Schema：type/properties/required

# Message 扩展：assistant 回合可带 tool_calls；ROLE_TOOL 回合带 tool_results。
@dataclass
class Message:
    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)    # 仅 assistant
    tool_results: list[ToolResult] = field(default_factory=list) # 仅 ROLE_TOOL

# StreamEvent 扩展：在 text/done/err 之外，turn 结束时一次性上抛 tool_calls。
@dataclass
class StreamEvent:
    text: str = ""                       # 文本增量
    tool_calls: list[ToolCall] = field(default_factory=list)  # 非空：本轮模型请求执行这些工具（done 之前发出）
    done: bool = False
    err: Exception | None = None
```

`Provider.stream` 签名变更：

```python
class Provider(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def model(self) -> str: ...
    def stream(
        self,
        msgs: list[Message],
        tools: list[ToolDefinition],
    ) -> AsyncIterator[StreamEvent]: ...
```

`tools` 为空表示本次请求不带工具。续答请求（请求#2）仍传入 `tools`（与真实协议一致），但编排层忽略其再次返回的工具调用（单轮）。

### tool 包（新建）

```python
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

@dataclass
class Result:
    """工具执行结果——永远以值类型返回，从不抛 Python 异常给上层。"""
    content: str          # 回灌给模型的文本（已截断/带行号等）
    is_error: bool = False  # True 表示结构化错误，content 即错误描述

@runtime_checkable
class Tool(Protocol):
    """统一工具抽象（F1）。"""
    def name(self) -> str: ...               # 模型看到的工具名，如 "read_file"
    def description(self) -> str: ...        # 给模型的用途说明
    def parameters(self) -> dict[str, Any]: ...  # 手写 JSON Schema（type/properties/required/description）
    async def execute(self, args: str) -> Result: ...
    # 注：args 是 raw JSON 字符串；超时由外部 asyncio.wait_for 控制

class Registry:
    """集中登记、按名查找、导出定义、按名执行。"""
    def __init__(self) -> None:
        self._order: list[str] = []      # 保持注册顺序，导出稳定
        self._tools: dict[str, Tool] = {}

    def register(self, t: Tool) -> None: ...
    def get(self, name: str) -> Tool | None: ...
    def definitions(self) -> list["ToolDefinition"]: ...  # F3/AC1：按序导出
    async def execute(self, name: str, args: str, timeout: float) -> Result: ...
    # F5/F9：未知工具兜底为 is_error；超时由 asyncio.wait_for 抛 TimeoutError → 转 Result

def new_default_registry() -> Registry:
    """构造并注册 6 个工具，固化 bash 超时与各上限常量。"""
    ...

DEFAULT_TIMEOUT: float = 30.0  # 单个工具执行的默认超时秒数（N1，不可配）
```

每个工具用 `@dataclass` + 手写 `from_json` 解析入参，或直接 `json.loads` 后用 `dict.get`；解析失败转为 `Result(is_error=True, ...)`。

| 工具名 | 参数（JSON Schema） | 成功结果 | 错误结果 |
|--------|--------------------|---------|---------|
| `read_file` | `path`(必填) | 带行号文本（`f"{n:6d}\t{line}"` 风格，≤2000 行 / ≤256KB，超出截断标注 `[truncated]`） | 不存在/不可读/是目录 |
| `write_file` | `path`(必填)、`content`(必填) | `Path.parent.mkdir(parents=True, exist_ok=True)` 后覆盖写，返回路径与字节数 | 写入失败 |
| `edit_file` | `path`、`old_string`、`new_string`(均必填) | `content.count(old)==1` 时唯一替换并写回 | 0 处→「未找到匹配」；>1 处→「匹配到 N 处，old_string 不唯一，请提供更长上下文」 |
| `bash` | `command`(必填) | `asyncio.create_subprocess_shell(..., stdout=PIPE, stderr=PIPE)` 执行，返回 stdout/stderr/exit_code（合并视图截断 ~30000 字符） | 超时（is_error）；命令非零退出按结果回灌 |
| `glob` | `pattern`(必填，如 `**/*.py`)、`path`(可选，默认 cwd) | `pathlib.Path(root).rglob(pattern)` 或 `glob.glob(recursive=True)` 匹配（≤100，排序） | 无匹配返回空说明（非 is_error） |
| `grep` | `pattern`(必填，Python 正则)、`path`(可选)、`glob`(可选文件名过滤) | `re.compile` + 逐行扫，`file:line:content` 列表（≤100，超出标注） | 正则非法（is_error）；无命中返回空说明 |

### agent 包（新建）

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator

class Phase(Enum):
    START = "start"  # 工具开始执行
    END = "end"      # 工具执行完毕

@dataclass
class ToolEvent:
    """一次工具调用的开始/结束（供 TUI 渲染工具行与结果摘要）。"""
    name: str
    args: str = ""        # 参数预览（用于 ● name(args)）
    phase: Phase = Phase.START
    result: str = ""      # phase=END：结果摘要
    is_error: bool = False  # phase=END：是否错误

@dataclass
class Event:
    """单轮闭环对外事件流元素，TUI 据非 None 字段分派渲染。"""
    text: str = ""          # 文本增量（preamble 或最终答复）
    tool: ToolEvent | None = None  # 工具调用开始/结束
    done: bool = False      # 本轮结束
    err: Exception | None = None  # 出错（不中断会话）

class Agent:
    """持有 provider 与注册中心，执行单轮闭环。"""
    def __init__(self, provider: Provider, registry: Registry) -> None:
        self._provider = provider
        self._registry = registry

    async def run(self, conv: Conversation) -> AsyncIterator[Event]:
        """执行单轮闭环，async generator 吐出事件流。调用方 cancel() 该 task 即终止。"""
        ...
```

## 模块设计### `mewcode.tool`**职责：** 提供 6 个工具的统一抽象与执行；集中登记与导出；所有失败包成 `Result(is_error=True)` 而非抛异常（F1/F2/F9/N4）。
**对外接口：** `Tool`、`Result`、`Registry`、`new_default_registry`、`DEFAULT_TIMEOUT`。
**依赖：** 标准库（`pathlib`、`asyncio`、`re`、`fnmatch`、`json`）、`mewcode.llm`（仅为 `definitions()` 返回 `list[ToolDefinition]`）。
**关键实现点：**
- Schema 手写为 `dict[str, Any]`：OpenAI 直接用整对象；Anthropic 由 llm 适配器取 `["properties"]`/`["required"]`。
- `read_file` 带行号、行/字节上限、`[truncated]` 标注（N5/AC2）。
- `edit_file` 唯一匹配语义 + 含计数的可区分错误（AC4）。
- `bash` 用 `asyncio.create_subprocess_shell(cmd, stdout=PIPE, stderr=PIPE)`，外层 `asyncio.wait_for(..., timeout=DEFAULT_TIMEOUT)` 控制超时；超时则 `proc.kill()` 并返回结构化错误（AC5/N1）。`shell=True` 自带 pipe/redirect 支持，跨平台用 `/bin/sh -c` 或 `cmd /C`（asyncio 自动按 OS 选）。
- `glob` 用 `pathlib.Path(root).glob(pattern)` 或自实现 `**` 段匹配；遍历期间 `await asyncio.sleep(0)` 让出 event loop。`grep` 用 `re` + `Path.rglob` + 异步友好的分批读取。
- 空 `args`（OpenAI 可能给空串而非 `{}`）归一为 `"{}"` 处理，避免误报参数错误。

### `mewcode.agent`**职责：** 单轮闭环编排（F5/F6），保证 AC9 单轮上限；把 provider 的 `StreamEvent` 与工具执行翻译成统一 `Event` 异步流。
**对外接口：** `Agent`、`Event`、`ToolEvent`、`Phase`。
**依赖：** `mewcode.llm`、`mewcode.tool`、`mewcode.conversation`、`asyncio`。
**run 算法：**
1. `defs = self._registry.definitions()`。
2. **请求#1**：`async for ev in self._stream_once(conv, defs):` 转发 `text` 增量给调用方、累积完整 preamble 文本、收集 `tool_calls`；出错则 `yield Event(err=...)` 后结束。
3. 若无 `tool_calls`：`conv.add_assistant(preamble)`，`yield Event(done=True)`，结束（纯文本回合，与 ch02 等价）。
4. 有 `tool_calls`：`conv.add_assistant_with_tool_calls(preamble, calls)`。
5. 顺序执行每个 call：`yield Event(tool=ToolEvent(name, args, Phase.START))` → `r = await self._registry.execute(call.name, call.input, timeout=tool.DEFAULT_TIMEOUT)` → `yield Event(tool=ToolEvent(name, phase=Phase.END, result=r.content, is_error=r.is_error))` → 收集 `ToolResult(tool_call_id=call.id, content=r.content, is_error=r.is_error)`。
6. `conv.add_tool_results(results)`。
7. **请求#2**：`async for ev in self._stream_once(conv, defs):` 转发最终答复 `text`、累积 final 文本；**忽略**其返回的任何 `tool_calls`（单轮，AC9）。
8. `conv.add_assistant(final)`，`yield Event(done=True)`。
- 调用方 `cancel()` 此 task（退出/Ctrl+C）时 `async for` 自然抛 `CancelledError`，沿向上传播终止；工具执行经 `asyncio.wait_for` 受 `DEFAULT_TIMEOUT` 约束（N1）。

### `mewcode.llm`（扩展）**职责：** 协议无关请求/响应抽象 + 两协议工具调用全流程（F3/F4/F6/F7）。
**`anthropic_provider.py` 关键改动：**
- 请求构造加 `params["tools"] = to_anthropic_tools(tools)`：每项 `{"name": d.name, "description": d.description, "input_schema": d.input_schema}`（SDK 直接接受 `input_schema` 完整对象）。
- 流循环用 `async with self._client.messages.stream(**params) as stream:`；按 `event.type` 分派：`content_block_delta` + `delta.type == "text_delta"` → `yield StreamEvent(text=delta.text)`；`thinking_delta` / `input_json_delta` 跳过（SDK 内部累加器会保留完整 input JSON）。
- 流结束后取 `final_message = await stream.get_final_message()`：若 `final_message.stop_reason == "tool_use"`，遍历 `final_message.content`，对 `ToolUseBlock` 类型块收集 `ToolCall(id=block.id, name=block.name, input=json.dumps(block.input))`，`yield StreamEvent(tool_calls=calls)`。
- `to_anthropic_messages` 扩展：assistant 回合若有 `tool_calls`，content 用 `[{"type": "text", "text": preamble}, {"type": "tool_use", "id": ..., "name": ..., "input": json.loads(call.input)}]` 数组；`ROLE_TOOL` 回合把每个 `ToolResult` 用 `{"type": "tool_result", "tool_use_id": id, "content": content, "is_error": is_error}` 拼进**一条 user 消息**的 content 数组。

**`openai_provider.py` 关键改动：**
- 请求构造加 `params["tools"]`：每项 `{"type": "function", "function": {"name": d.name, "description": d.description, "parameters": d.input_schema}}`。
- 流循环 `async for chunk in await self._client.chat.completions.create(..., stream=True):`；按 index 维护 `tool_calls_buf: dict[int, dict]`，把 `delta.tool_calls` 中每片 `{index, id?, function.name?, function.arguments?}` 累加合并（id/name 取首次出现，arguments 拼接）；正文 `delta.content` 仍 yield 文本增量。
- 流结束后（`finish_reason == "tool_calls"` 或 buf 非空）按 index 排序组 `ToolCall(id, name, input=arguments_buf)`（空 arguments 归一为 `"{}"`），`yield StreamEvent(tool_calls=calls)`。
- `to_openai_messages` 扩展：assistant 回合若有 `tool_calls`，发 `{"role": "assistant", "content": preamble or None, "tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.input}} for c in calls]}`；`ROLE_TOOL` 回合每个 `ToolResult` 发一条 `{"role": "tool", "tool_call_id": r.tool_call_id, "content": r.content}`。

### `mewcode.conversation`（扩展）

```python
def add_assistant_with_tool_calls(self, text: str, calls: list[ToolCall]) -> None:
    """assistant 工具调用回合。"""
    self._messages.append(Message(role=ROLE_ASSISTANT, content=text, tool_calls=list(calls)))

def add_tool_results(self, results: list[ToolResult]) -> None:
    """ROLE_TOOL 结果回合。"""
    self._messages.append(Message(role=ROLE_TOOL, tool_results=list(results)))
```
保留 `add_user`/`add_assistant`/`messages`/`__len__` 不变。

### `mewcode.tui`（扩展）**职责：** 渲染 `agent.Event`（文本/工具行/结果摘要/错误/结束），保持非阻塞（N2）。
- `MewCodeApp.__init__(self, providers, version, registry)`：存 `self._registry`。
- 新增 reactive / 成员：`self._cur_tool: ToolDisplay | None`（执行中指示：name/args，非 None 即在 `#streaming` 渲染执行行）。
- `submit`：`conv.add_user(text)` 后 `self._stream_task = asyncio.create_task(self._consume_agent_events())`，task 内构造 `agent = Agent(self.provider, self._registry)` 后 `async for ev in agent.run(self.conv):` 分派。
- `_consume_agent_events` 分派：
  - `ev.text` 非空：`cur_reply += ev.text`，更新动态区显示；
  - `ev.tool` 且 `phase==START`：若 `cur_reply` 非空，先把 preamble 经 markdown 渲染后 `RichLog.write(...)` 提交并清空 `cur_reply`；置 `self._cur_tool`；
  - `ev.tool` 且 `phase==END`：依次 `RichLog.write(tool_line(name, args))` + `RichLog.write(tool_result_summary(result, is_error))` 顺序提交；清 `self._cur_tool`；
  - `ev.done`：把 `cur_reply`（最终答复）经 `rich.markdown.Markdown` 渲染并写入 `RichLog`；`_finish_turn()`；
  - `ev.err`：`RichLog.write(error_block(err))`；`_finish_turn()`。
- `view.py` 新增：`tool_line(name, args) -> RenderableType`（青/绿 `●` + `name(args)`，用 `Text(..., style="bold cyan")`）、`tool_result_summary(result, is_error) -> RenderableType`（缩进 `  ⎿ `、灰/红、UI 截断 ~8 行）。
- `View.compose`（或 `_render_streaming`）在 `self._cur_tool is not None` 时渲染「`● name(args)` + spinner Running…」到 `#streaming`，否则沿用「Imagining… (Ns)」。
- `RichLog.write` 同步追加保证顺序——Python 这边只有一个 event loop，不存在 Go `tea.Batch` 那种并发乱序问题，无需特殊同步原语。

## 模块交互

```
用户提交
  └─ MewCodeApp.submit: conv.add_user(text); self._stream_task = asyncio.create_task(_consume_agent_events())
       └─ _consume_agent_events:
            └─ agent = Agent(provider, registry); async for ev in agent.run(conv):
                 ├─ 请求#1: async for se in provider.stream(conv.messages(), registry.definitions()):
                 │     └─ 适配器: 注入 tools → 流式拼接 → StreamEvent{text…} / StreamEvent{tool_calls}
                 │     → agent 转发 Event{text}（preamble），收集 calls
                 ├─ 无 calls → conv.add_assistant(preamble); yield Event(done=True)
                 └─ 有 calls:
                      ├─ conv.add_assistant_with_tool_calls(preamble, calls)
                      ├─ for call: yield Event(tool=START) → await registry.execute(name, args, timeout=DEFAULT_TIMEOUT) → yield Event(tool=END)
                      ├─ conv.add_tool_results(results)
                      ├─ 请求#2: async for se in provider.stream(...) → yield Event(text)（最终答复）
                      │     （适配器把 conv 里的 tool_use/tool_result 回合映射为各自线格式）
                      └─ conv.add_assistant(final); yield Event(done=True)
  └─ _consume_agent_events 按 Event 类型渲染（cur_reply 动态区 / RichLog.write 进 scrollback）
```

并发：`conv` 仅在单个 event loop 上被消费 task 触碰——`submit` 在 create_task 前 `add_user`，之后只读；`run` 协程独占后续所有 `conv` 变更。`messages()` 返回副本。Textual UI 渲染回到主协程序列化执行，与 `conv` 互不干扰（N2）。

## 文件组织

```
mewcode/
├── pyproject.toml                          — 不变（已含 anthropic/openai/textual/rich/pyyaml）
├── src/mewcode/
│   ├── cli.py                              — 修改：new_default_registry() 注入 MewCodeApp
│   ├── llm/
│   │   ├── __init__.py                     — 修改：新增 ToolCall/ToolResult/ToolDefinition/ROLE_TOOL；扩展 Message/StreamEvent；Provider.stream 加 tools 参数
│   │   ├── anthropic_provider.py           — 修改：to_anthropic_tools；stream 解析 tool_use blocks；to_anthropic_messages 支持 tool_use/tool_result
│   │   └── openai_provider.py              — 修改:to_openai_tools；按 index 拼 tool_calls；to_openai_messages 支持 assistant.tool_calls/tool 消息
│   ├── tool/                               — 新建
│   │   ├── __init__.py                     — Tool Protocol、Result、Registry、new_default_registry、DEFAULT_TIMEOUT、_truncate 辅助
│   │   ├── read_file.py / write_file.py / edit_file.py / bash.py / glob_tool.py / grep_tool.py
│   ├── agent/                              — 新建
│   │   └── __init__.py                     — Agent、Event、ToolEvent、Phase、run、_stream_once
│   ├── conversation.py                     — 修改：add_assistant_with_tool_calls、add_tool_results
│   ├── prompt.py                           — 修改：SYSTEM_PROMPT 增 Agent 角色与工具约定
│   └── tui/
│       ├── app.py                          — 修改：__init__ 接 registry；新增 _cur_tool 字段
│       ├── stream.py                       — 修改：submit 走 Agent.run；_consume_agent_events 分派工具事件
│       └── view.py                         — 修改：tool_line/tool_result_summary；执行指示
└── tests/
    ├── test_tool.py                        — 新建：注册中心 + 各工具单测
    └── test_agent.py                       — 新建：单轮闭环（fake provider）：AC8 链路、AC9 单轮
```

注意：`.mewcode/config.yaml` 与 ch02 完全一致——跨章节不变。

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 工具调用循环放哪 | 新建 `mewcode.agent` 包，TUI 退化为渲染器 | 循环（请求#1→执行→请求#2）无法塞进 ch02 的单个 `_consume_stream` 协程；独立包可无 UI 单测（AC8/AC9），只依赖 llm+tool+conversation，不泄漏 SDK 类型。命名 `agent` 而非 `runner`：概念即 Agent，本章恰为单轮。 |
| 是否用 SDK 的高级 tool-runner | 不用，坚持手写 streaming + 手动单轮 | anthropic Python SDK 暂无自动 tool runner；openai 的 helper 自动连环到完成，违反 F6/AC9。手写迭代更可控且与 ch02 stable 风格一致。 |
| 工具定义传入哪一层 | `Provider.stream` 第二参数 `list[ToolDefinition]` | 两 SDK 都把 tools 放 per-request params；续答仍需带；保持 Provider 无状态。 |
| 工具参数 Schema 生成 | 每工具手写 `dict[str, Any]` | OpenAI `parameters` 与 Anthropic `input_schema` 都直接吃 JSON Schema dict；6 个固定工具手写最直白，描述对模型可读性最关键；不引入 `pydantic` 反射（schema 还要剥 `$defs`/`additionalProperties` 噪音）。 |
| 流式工具参数拼接 | Anthropic 用 `stream.get_final_message()` 拿汇总；OpenAI 按 `delta.tool_calls[i].function.arguments` 按 index 累加 | Anthropic SDK 自带累加器，避免手写 PartialJSON 边界；OpenAI 必须按 index 拼接（多工具下同时分片）。 |
| Glob/Grep 实现 | 纯标准库（`pathlib.glob`/`re` + 异步 `await asyncio.sleep(0)` 让出） | 零额外依赖、跨平台；spec 要求保持简单、不引入配置。 |
| Bash 实现与超时 | `asyncio.create_subprocess_shell` + `asyncio.wait_for(..., DEFAULT_TIMEOUT)` | `shell=True` 自带管道/重定向；asyncio 原生超时 + `proc.kill()` 终止；30s 内置不可配（spec：超时不配置化）。跨平台兼容（Win 上 asyncio 走 ProactorEventLoop）。 |
| 工具失败的表达 | `execute` 返回 `Result(content, is_error)`，从不抛异常给上层 | F9/N4：所有失败包成结构化结果回灌，程序不崩，上层无需区分 try/except 路径。 |
| 工具结果在 Message 的形态 | 平铺字段（assistant 加 `tool_calls`，`ROLE_TOOL` 加 `tool_results`） | 两 SDK 工具语义本就是 id 关联的 tool_use/tool_result 列表；通用 content-block 联合属过度设计（本章结果均文本）。适配器吸收差异（Anthropic 结果进 user 消息、OpenAI 用 tool 角色）。 |
| UI 截断 vs 回灌截断 | 两者分离：UI 摘要 ~8 行；回灌为工具级上限（read 2000 行 / bash 30000 字符 等） | AC11/N5 要界面截断，但模型需较完整内容；尾部统一加 `[truncated]` 标注。 |
| 续答请求是否带 tools | 带，但忽略其返回的工具调用 | 与真实协议一致（OpenAI assistant+tool 后不带 tools 也可，但带更稳）；F6/AC9 由 agent 不再触发执行来保证单轮。 |
| thinking 与工具组合 | 历史含工具交互的请求（续答）不启用 thinking | Anthropic 在 thinking 启用时要求回灌带 tool_use 的 assistant 回合附原 thinking 块（含 signature），而本章按 spec 丢弃 thinking 增量、不留签名；故对这类请求关闭 thinking 以避免 400。 |
| 空最终答复 | 续答为空时用单轮提示占位并推给 UI | 空 assistant 回合会破坏下一轮请求（Anthropic 要求非空内容 + 角色交替）；占位提示同时满足 AC9 的"单轮上限提示"。 |
| 空参数归一 | OpenAI 侧空 arguments 归一为 `"{}"` | 无参工具的 arguments 可能为空串，回灌时须是合法 JSON，否则严格兼容端点对 `"arguments": ""` 返回 400。 |
| grep 超长行 | 显式标注未完整搜索 | `for line in file` 遇超长行可能阻塞或读爆内存；用 `read(chunk)` + 手动分割或 `iter(..., '')` 加最大长度判定，超出标注「该行过长，未完整搜索」避免假"无命中"误导模型。 |
| scrollback 顺序提交 | 单 event loop 内 `RichLog.write` 同步追加 | Python 的 asyncio 单线程模型天然保证顺序；不存在 Go `tea.Batch` 并发乱序问题。 |
| 工具命名 | `read_file`/`write_file`/`edit_file`/`bash`/`glob`/`grep` | 符合 OpenAI 函数名规则（`a-zA-Z0-9_-`）与 Claude Code 习惯；TUI 工具行显示 `● name(关键参数)`。 |