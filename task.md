# 工具系统 Tasks

> 基于已批准的 spec.md + plan.md。任务有序，每步留绿（`python -m mewcode` 可启动 / `pytest` 通过 / `ruff check` 无告警）。验证一律「先跑命令看输出，再下结论」。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `src/mewcode/llm/__init__.py` | 新增 ToolCall/ToolResult/ToolDefinition/ROLE_TOOL；扩展 Message/StreamEvent；Provider.stream 加 tools 参数 |
| 修改 | `src/mewcode/llm/anthropic_provider.py` | 注入 tools、stream 解析 tool_use blocks、tool_use/tool_result 回灌 |
| 修改 | `src/mewcode/llm/openai_provider.py` | 注入 tools、按 index 拼 tool_calls、assistant.tool_calls/tool 消息回灌 |
| 新建 | `src/mewcode/tool/__init__.py` | Tool Protocol、Result、Registry、new_default_registry、DEFAULT_TIMEOUT、_truncate |
| 新建 | `src/mewcode/tool/{read_file,write_file,edit_file,bash,glob_tool,grep_tool}.py` | 6 个核心工具 |
| 新建 | `tests/test_tool.py` | 注册中心 + 各工具单测 |
| 新建 | `src/mewcode/agent/__init__.py` | Agent、Event、ToolEvent、Phase、run（单轮闭环） |
| 新建 | `tests/test_agent.py` | fake provider 驱动单轮闭环（AC8/AC9） |
| 修改 | `src/mewcode/conversation.py` | add_assistant_with_tool_calls、add_tool_results |
| 修改 | `src/mewcode/prompt.py` | SYSTEM_PROMPT 增 Agent 角色与工具约定 |
| 修改 | `src/mewcode/tui/{app,stream,view}.py` | 接入 Agent.run、工具事件渲染、工具行/执行指示 |
| 修改 | `src/mewcode/cli.py` | 构造 new_default_registry 注入 MewCodeApp |

---

## T1: 扩展 llm 协议无关类型**文件：** `src/mewcode/llm/__init__.py`
**依赖：** 无
**步骤：**
1. 新增 `import json`（如未导入）。
2. 增加常量 `ROLE_TOOL = "tool"`（同时把字面量 `"user"`/`"assistant"` 提到 `ROLE_USER`/`ROLE_ASSISTANT` 也行）。
3. 新增 dataclass：`ToolCall(id: str, name: str, input: str)`、`ToolResult(tool_call_id: str, content: str, is_error: bool = False)`、`ToolDefinition(name: str, description: str, input_schema: dict[str, Any])`（各带中文 docstring）。
4. 给 `Message` 增字段 `tool_calls: list[ToolCall] = field(default_factory=list)`、`tool_results: list[ToolResult] = field(default_factory=list)`，并把 `role` 字面量扩展为 `Literal["user", "assistant", "tool"]`；`content` 给默认值 `""`（纯增量，不破坏现有构造）。
5. 给 `StreamEvent` 增字段 `tool_calls: list[ToolCall] = field(default_factory=list)`；更新 docstring 为四态语义说明。

**验证：** `python -c "from mewcode.llm import ToolCall, ToolResult, ToolDefinition, ROLE_TOOL, Message, StreamEvent; print(Message(role='tool').tool_results)"` 输出 `[]`；`ruff check src/mewcode/llm/__init__.py` 无告警。

## T2: tool 包骨架（Tool Protocol、Result、Registry、_truncate）**文件：** `src/mewcode/tool/__init__.py`
**依赖：** T1
**步骤：**
1. 定义 `@dataclass class Result(content: str, is_error: bool = False)`。
2. 定义 `@runtime_checkable class Tool(Protocol)`：`name() -> str` / `description() -> str` / `parameters() -> dict[str, Any]` / `async def execute(self, args: str) -> Result`。
3. 定义 `_truncate(s: str, max_lines: int, max_chars: int) -> str`：超出尾部追加 `\n[truncated]` 标注。
4. 定义 `class Registry`：`__init__` 初始化 `_order: list[str] = []` / `_tools: dict[str, Tool] = {}`；`register(t)`（按 `t.name()` 入表，重复名后注册覆盖前一项但 `_order` 保留首次顺序，或抛 `ValueError`——本项目取后者）；`get(name)`；`definitions() -> list[ToolDefinition]`（按 `_order` 把每工具 `name/description/parameters` 组成 `ToolDefinition`）；`async def execute(self, name, args, timeout=DEFAULT_TIMEOUT) -> Result`（`get` 未命中返回 `Result(is_error=True, content=f"未知工具: {name}")`；命中则 `try: return await asyncio.wait_for(tool.execute(args), timeout) except asyncio.TimeoutError: return Result(is_error=True, content=f"工具 {name} 执行超时（{timeout}s）") except Exception as e: return Result(is_error=True, content=f"工具 {name} 异常: {e}")`）。
5. 常量 `DEFAULT_TIMEOUT: float = 30.0`。**暂不写** `new_default_registry`。

**验证：** `python -c "from mewcode.tool import Tool, Result, Registry, DEFAULT_TIMEOUT; print(Registry().definitions())"` 输出 `[]`；`ruff check src/mewcode/tool/` 无告警。

## T3: read_file 工具**文件：** `src/mewcode/tool/read_file.py`
**依赖：** T2
**步骤：**
1. 定义 `class ReadFileTool` 实现 `Tool` Protocol。
2. `parameters()` 返回手写 schema：`{"type": "object", "properties": {"path": {"type": "string", "description": "要读取的文件路径"}}, "required": ["path"]}`。
3. `async def execute(args)`：空 args 当 `"{}"`；`data = json.loads(args)`；`path = data.get("path")` 缺失 → `is_error`；用 `pathlib.Path(path)` 读取——`is_dir()` / 不存在 / `PermissionError` → `is_error`；成功 `text.splitlines()` 后按行加行号（`f"{n:6d}\t{line}"`），经 `_truncate` 限 2000 行 / 256KB。

**验证：** `python -c "import asyncio; from mewcode.tool.read_file import ReadFileTool; print(asyncio.run(ReadFileTool().execute('{\"path\":\"pyproject.toml\"}')).content[:80])"` 出现行号；读不存在文件得 `is_error=True`（T9 后补单测）。

## T4: write_file 工具**文件：** `src/mewcode/tool/write_file.py`
**依赖：** T2
**步骤：**
1. `class WriteFileTool`。
2. `parameters()`：`path` 与 `content` 均必填。
3. `execute`：解析 `path` / `content`；`Path(path).parent.mkdir(parents=True, exist_ok=True)` 后 `Path(path).write_text(content)`（覆盖）；成功返回 `Result(content=f"已写入 {path}（{len(content.encode())} 字节）")`；任何 `OSError` → `is_error`。

**验证：** `ruff check`；T9 后单测写嵌套路径检查磁盘。

## T5: edit_file 工具**文件：** `src/mewcode/tool/edit_file.py`
**依赖：** T2
**步骤：**
1. `class EditFileTool`。
2. `parameters()`：`path` / `old_string` / `new_string` 三字段必填，描述说明唯一匹配语义。
3. `execute`：读文件失败 → `is_error`；`n = content.count(old_string)`；`n == 0` → `Result(is_error=True, content="未找到匹配的内容")`；`n > 1` → `Result(is_error=True, content=f"匹配到 {n} 处，old_string 不唯一，请提供更长上下文使其唯一")`；`n == 1` → `content.replace(old_string, new_string, 1)` 写回，返回成功。

**验证：** `ruff check`；T9 后单测覆盖 0/1/多三情形。

## T6: bash 工具**文件：** `src/mewcode/tool/bash.py`
**依赖：** T2
**步骤：**
1. `class BashTool`。
2. `parameters()`：`command` 必填。
3. `execute`：`proc = await asyncio.create_subprocess_shell(cmd, stdout=PIPE, stderr=PIPE)`；`stdout_b, stderr_b = await proc.communicate()`（外层 `Registry.execute` 已加 `asyncio.wait_for`，但 BashTool 内可再嵌一层短超时供测试时注入——本项目沿用 Registry 层超时即可）；超时由 Registry 捕获并返回结构化错误；正常返回组装文本：`f"exit_code: {proc.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}"`，经 `_truncate(s, max_lines=10000, max_chars=30000)`，非零退出**不**设 `is_error`（按结果回灌让模型判断）。

**验证：** `ruff check`；T9 后单测 `echo hi` 与超时命令（用极短超时跑 `sleep 10`）。

## T7: glob 工具**文件：** `src/mewcode/tool/glob_tool.py`
**依赖：** T2
**步骤：**
1. `class GlobTool`。
2. `parameters()`：`pattern` 必填（如 `**/*.py`），`path` 可选（默认 `.`）。
3. `execute`：`root = Path(args.get("path") or ".")`；用 `root.glob(pattern)`（`**` 由 `pathlib` 原生支持，含跨层级），过滤出文件（非目录）；收集相对路径并 `sorted` 后取前 100；循环中每 100 个 `await asyncio.sleep(0)` 让出 event loop；无匹配返回 `Result(content="无匹配")`（非 `is_error`）。

**验证：** `ruff check`；T9 后单测 `**/*.py` 能命中 `src/mewcode/` 下文件。

## T8: grep 工具**文件：** `src/mewcode/tool/grep_tool.py`
**依赖：** T2
**步骤：**
1. `class GrepTool`。
2. `parameters()`：`pattern` 必填（Python 正则，`re.compile`，描述注明），`path` / `glob` 可选。
3. `execute`：`try: rx = re.compile(pattern) except re.error as e: return Result(is_error=True, content=f"正则非法: {e}")`；`root = Path(args.get("path") or ".")`；遍历 `root.rglob("*")`（若 `glob` 非空则 `root.rglob(glob)`），对每个文件 `with open(file, errors="replace") as f: for lineno, line in enumerate(f, 1):`，匹配则收集 `f"{file}:{lineno}:{line.rstrip()}"`；遇 `OSError`/`UnicodeDecodeError` 跳过该文件；≤100 命中，超出尾部标注；每文件结束 `await asyncio.sleep(0)`；无命中返回 `Result(content="无命中")`（非 `is_error`）。注意单行长度超过 ~1MB 时显式标注「该行过长，未完整搜索」（用 `f.read(1024*1024)` 分块或检查 `len(line)`）。

**验证：** `ruff check`；T9 后单测搜一个已知关键字命中。

## T9: new_default_registry 与 tool 单测**文件：** `src/mewcode/tool/__init__.py`、`tests/test_tool.py`
**依赖：** T3–T8
**步骤：**
1. `__init__.py` 增 `new_default_registry()`：依次 `register` 6 个工具，返回 `Registry`。
2. `tests/test_tool.py`（pytest-asyncio）：测 `definitions()` 返回恰好 6 条且名称有序（AC1）；`read_file` 存在/不存在；`write_file` 新建 + 嵌套路径（用 `tmp_path` fixture）检查磁盘；`edit_file` 0/1/多三情形错误可区分；`bash` echo 与超时（注入极短 timeout 跑 `sleep 5`）；`glob` `**/*.py`；`grep` 关键字。所有 `@pytest.mark.asyncio` + `async def test_...`。
3. 若未装 pytest-asyncio：`pyproject.toml` 的 `[dependency-groups].dev` 加 `pytest-asyncio>=0.23`，`[tool.pytest.ini_options]` 加 `asyncio_mode = "auto"`。

**验证：** `pytest tests/test_tool.py -v` 全通过；输出确认 6 条定义、edit 三情形文案不同。

## T10: Provider.stream 加 tools 参数（注入定义，暂不解析）**文件：** `src/mewcode/llm/__init__.py`、`src/mewcode/llm/anthropic_provider.py`、`src/mewcode/llm/openai_provider.py`、`src/mewcode/tui/stream.py`
**依赖：** T1
**步骤：**
1. `__init__.py`：`Provider.stream` 签名改为 `stream(self, msgs: list[Message], tools: list[ToolDefinition]) -> AsyncIterator[StreamEvent]`，更新 Protocol docstring。
2. `anthropic_provider.py`：`stream` 加 `tools` 形参；新增 `_to_anthropic_tools(tools)` 转 `[{"name", "description", "input_schema"}]` 并设入请求参数；流解析暂不变。
3. `openai_provider.py`：同理，新增 `_to_openai_tools(tools)` 转 `[{"type": "function", "function": {"name", "description", "parameters"}}]` 入参。
4. `tui/stream.py`：`submit` / `_consume_stream` 中 `provider.stream(conv.messages())` 暂改为传 `[]` 第二参数（T16 会替换为 `Agent.run`）。

**验证：** `python -m mewcode` 发一条纯文本仍正常（工具定义已随请求发送，模型未必调用）；`ruff check src/mewcode/llm/` 无告警。

## T11: anthropic 适配器解析工具调用 + 回灌**文件：** `src/mewcode/llm/anthropic_provider.py`
**依赖：** T10
**步骤：**
1. 流循环用 `async with self._client.messages.stream(**params) as stream: async for event in stream:`；`event.type == "content_block_delta"`：若 `event.delta.type == "text_delta"` → `yield StreamEvent(text=event.delta.text)`；`thinking_delta`/`input_json_delta` 跳过（SDK 内部已累加 input JSON）。
2. 流正常结束后：`final_message = await stream.get_final_message()`；若 `final_message.stop_reason == "tool_use"`，遍历 `final_message.content`，对每个 `block`：若 `block.type == "tool_use"`，收集 `ToolCall(id=block.id, name=block.name, input=json.dumps(block.input))`；非空则 `yield StreamEvent(tool_calls=calls)`；随后 `yield StreamEvent(done=True)`。
3. `_to_anthropic_messages` 扩展：assistant 有 `tool_calls` 时 content 用数组 `[{"type": "text", "text": preamble}] + [{"type": "tool_use", "id": c.id, "name": c.name, "input": json.loads(c.input)} for c in calls]`；`ROLE_TOOL` 消息把每个 `ToolResult` 用 `{"type": "tool_result", "tool_use_id": r.tool_call_id, "content": r.content, "is_error": r.is_error}` 拼成一条 `{"role": "user", "content": [...]}`。
4. 含工具历史的请求关闭 thinking：检查 `msgs` 中若存在 `tool_results` 或 assistant `tool_calls`，请求 params 不加 `thinking` 字段（避免 400）。

**验证：** `python -m mewcode` 启动正常；`ruff check src/mewcode/llm/anthropic_provider.py` 无告警。

## T12: openai 适配器解析工具调用 + 回灌**文件：** `src/mewcode/llm/openai_provider.py`
**依赖：** T10
**步骤：**
1. 流循环维护 `tool_calls_buf: dict[int, dict[str, str]]`（按 `delta.tool_calls[i].index` 累加）；每片 `if tc.id: buf[idx]["id"] = tc.id`、`if tc.function.name: buf[idx]["name"] = tc.function.name`、`if tc.function.arguments: buf[idx]["args"] = buf[idx].get("args", "") + tc.function.arguments`；正文 `delta.content` 仍 `yield StreamEvent(text=...)`。
2. 流结束后（`finish_reason == "tool_calls"` 或 `tool_calls_buf` 非空）：按 index 排序构造 `ToolCall(id=v["id"], name=v["name"], input=v.get("args") or "{}")`，`yield StreamEvent(tool_calls=calls)`；再 `yield StreamEvent(done=True)`。
3. `_to_openai_messages` 扩展：assistant 有 `tool_calls` 时发 `{"role": "assistant", "content": preamble or None, "tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.input or "{}"}} for c in calls]}`；`ROLE_TOOL` 消息每个 `ToolResult` 发 `{"role": "tool", "tool_call_id": r.tool_call_id, "content": r.content}`。

**验证：** `python -m mewcode` 启动正常；`ruff check src/mewcode/llm/openai_provider.py` 无告警。

## T13: conversation 扩展**文件：** `src/mewcode/conversation.py`、`tests/test_conversation.py`
**依赖：** T1
**步骤：**
1. 新增 `add_assistant_with_tool_calls(self, text: str, calls: list[ToolCall])`：`self._messages.append(Message(role=ROLE_ASSISTANT, content=text, tool_calls=list(calls)))`。
2. 新增 `add_tool_results(self, results: list[ToolResult])`：`self._messages.append(Message(role=ROLE_TOOL, tool_results=list(results)))`。
3. 保留现有方法不变。
4. `tests/test_conversation.py` 补一条断言：依次 `add_user`、`add_assistant_with_tool_calls`、`add_tool_results`、`add_assistant` 后 `messages()` 长度=4、role 序列正确、`tool_calls`/`tool_results` 内容正确。

**验证：** `pytest tests/test_conversation.py -v` 通过。

## T14: agent 单轮闭环**文件：** `src/mewcode/agent/__init__.py`、`tests/test_agent.py`
**依赖：** T9, T11, T12, T13
**步骤：**
1. `agent/__init__.py`：定义 `Phase`(START/END)、`ToolEvent`、`Event`、`class Agent`、`__init__(provider, registry)`、`async def run(self, conv) -> AsyncIterator[Event]`（按 plan 的 run 算法）。`_stream_once(conv, defs)` 内部 helper：`async for ev in self._provider.stream(conv.messages(), defs):` 转发 text 并累积 preamble、收集 tool_calls；err 直接 raise 或返回。`args` 预览取 `input` 简短串（如截断到 80 字符）。
2. `tests/test_agent.py`（pytest-asyncio）：用实现 `Provider` Protocol 的 `FakeProvider` 编排两种脚本——
   (a) 请求#1 yield 1 个 ToolCall（`read_file` with `{"path": "..."}`）、请求#2 yield 文本「文件已读取」→ 断言 Event 序列含 `tool=START/END` 与最终 `text`、`conv.messages()` 末尾为 assistant 文本（AC8）；
   (b) 请求#1 yield 工具、请求#2 仍 yield 工具 → 断言只调用一次 `registry.execute`、不再触发执行（AC9）。
   `FakeProvider` 内部用 `call_count` 切换两段脚本；Registry 用真的 `new_default_registry()` 或 fake 工具均可。

**验证：** `pytest tests/test_agent.py -v` 全通过；输出确认单轮上限生效。

## T15: prompt 系统提示词扩展**文件：** `src/mewcode/prompt.py`
**依赖：** 无
**步骤：**
1. 扩写 `SYSTEM_PROMPT`：说明 MewCode 是能使用工具的 Agent，可读写改文件、执行命令、查找/搜索代码；需要信息或操作时调用相应工具，拿到结果后给出简洁答复。

**验证：** `ruff check src/mewcode/prompt.py`；`pytest` 不回归。

## T16: tui 接入 agent + 工具行渲染**文件：** `src/mewcode/tui/app.py`、`src/mewcode/tui/stream.py`、`src/mewcode/tui/view.py`
**依赖：** T14, T15
**步骤：**
1. `app.py`：`MewCodeApp.__init__(self, providers, version, registry)` 存 `self._registry: Registry`；新增成员 `self._cur_tool: ToolDisplay | None = None`（小 dataclass：`name: str, args: str`）。
2. `stream.py`：`submit` 走 `self._stream_task = asyncio.create_task(self._consume_agent_events())`（替换 T10 的临时 `_consume_stream`）；`_consume_agent_events` 内部 `agent = Agent(self.provider, self._registry)`；`async for ev in agent.run(self.conv):` 分派——
   - `ev.text`：`cur_reply += ev.text`；刷新动态区；
   - `ev.tool and ev.tool.phase == Phase.START`：若 `cur_reply` 非空，先 `RichLog.write(rich.markdown.Markdown(cur_reply))` 提交 preamble 并清空；置 `self._cur_tool = ToolDisplay(name, args)`；
   - `ev.tool and ev.tool.phase == Phase.END`：`RichLog.write(tool_line(name, args))`，紧接 `RichLog.write(tool_result_summary(result, is_error))`；清 `self._cur_tool`；
   - `ev.done`：把 `cur_reply`（最终答复）经 markdown 渲染后写入 `RichLog`；`_finish_turn()`；
   - `ev.err`：`RichLog.write(error_block(ev.err))`；`_finish_turn()`。
3. `view.py` 新增：`tool_line(name, args) -> RenderableType`（`Text("● ", style="bold cyan") + Text(f"{name}({args})", style="bold")`）、`tool_result_summary(result, is_error) -> RenderableType`（`Padding(Text("⎿ " + result, style="red" if is_error else "dim"), (0, 0, 0, 2))`，UI 截断 ~8 行）；`_render_streaming` 在 `self._cur_tool is not None` 时渲染 `f"● {name}({args}) Running…"` + spinner，否则沿用 `Imagining… (Ns)`。

**验证：** `python -m mewcode` 启动正常；`ruff check src/mewcode/tui/` 无告警。

## T17: cli 接线**文件：** `src/mewcode/cli.py`
**依赖：** T16
**步骤：**
1. `from mewcode.tool import new_default_registry`；构造 `registry = new_default_registry()`；`MewCodeApp(cfg.providers, __version__, registry).run()`。

**验证：** `python -m mewcode` 在合法配置下能启动 TUI 并进入对话。

## T18: 全量验证与端到端冒烟**文件：** 无（验证）
**依赖：** T1–T17
**步骤：**
1. `ruff format --check .`；`ruff check .`；`pytest -v`（可选 `mypy src/mewcode`）。
2. 用当前 `.mewcode/config.yaml`（openai 兼容端点）跑：问「读 docs/python/ch03/spec.md 并用一句话总结」→ 观察工具行 `● read_file(...)` + 结果摘要 + 最终答复（AC8/AC11）。
3. 触发各错误：读不存在文件、edit 匹配不到、bash 非零退出 → 错误结构化回灌、程序不退出（AC12）。
4. （可选）若有 anthropic 配置，重复步骤 2 验证跨协议一致（AC10）。
5. 用 tmux 验证 scrollback：完成块用终端原生滚轮 / Ctrl+B + `[` 可回看工具行 + 结果摘要 + 最终答复，顺序不乱。

**验证：** 全部命令通过、端到端链路与错误恢复符合预期。

## 执行顺序

```
T1 ─┬─ T2 ─┬─ T3 ─┐
    │       ├─ T4 ─┤
    │       ├─ T5 ─┼─ T9 ─┐
    │       ├─ T6 ─┤      │
    │       ├─ T7 ─┤      │
    │       └─ T8 ─┘      │
    ├─ T10 ─┬─ T11 ──────┤
    │        └─ T12 ─────┤
    ├─ T13 ──────────────┤
    └─ T15               │
                T9,T11,T12,T13 ─→ T14 ─→ T16 ─→ T17 ─→ T18
                                   T15 ──┘
```