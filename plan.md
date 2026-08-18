# 项目记忆与会话持久化 Plan
## 架构概览

本章新增三个独立子包，加上对现有模块的窄幅修改：

| 新子包 | 职责 |
|------|------|
| `endless_code.instructions` | 三层 ENDLESSCODE.md 加载 + @include 展开 |
| `endless_code.session` | JSONL 会话写入、列表扫描、加载恢复、过期清理 |
| `endless_code.memory` | 笔记 CRUD、索引管理、异步 LLM 更新 |

| 已有模块 | 改动 |
|--------|------|
| `endless_code.prompt` | `build_system_prompt` 接受 instructions/memory 参数 |
| `endless_code.conversation` | 新增 on_append/on_replace 回调 |
| `endless_code.compact.state` | session ID 格式改为 YYYYMMDD-HHMMSS-xxxx；`SessionContext` 加 `session_dir` 字段 |
| `endless_code.agent` | 每 5 轮 run 结束后触发记忆更新 |
| `endless_code.tui` | 在 `EndlessCodeApp` 中新增 /resume 命令和 `RESUMING` 状态 |
| `endless_code.cli` | 启动流程串联指令加载、记忆初始化、会话清理 |

## 核心数据结构

### instructions 子包

```python
# Loader 负责三层 ENDLESSCODE.md 的加载和 @include 展开。
from dataclasses import dataclass


@dataclass
class Loader:
    project_root: str
    user_config_dir: str | None = None  # 缺省 ~/.config/endless-code/
    max_depth: int = 5

    # 按优先级加载三层指令文件，返回拼接后的完整指令文本。
    # 加载失败的层静默跳过，全部为空返回空字符串。
    def load(self) -> str: ...

    # 加载单个文件，处理 @include 展开。
    # boundary 是路径逃逸检测的根边界。
    # depth 当前嵌套层数（从 1 开始），visited 环路检测集合。
    def _load_file(
        self,
        path: str,
        boundary: str,
        depth: int,
        visited: set[str],
    ) -> str: ...
```

### session 子包

```python
# Entry 是 JSONL 中一行的 dataclass 表示。
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Entry:
    role: str = ""  # "user"/"assistant"/"tool"
    content: str = ""
    tool_calls: list[dict] | None = None  # 仅 assistant
    tool_results: list[dict] | None = None  # 仅 tool
    ts: int = 0  # Unix 秒
    model: str | None = None  # 仅首条消息
    type: str | None = None  # "compact" 或省略


# Writer 负责向 conversation.jsonl 追加写入。
class Writer:
    def __init__(self, session_dir: str, model: str) -> None: ...

    @classmethod
    def open_existing(cls, session_dir: str, model: str) -> "Writer": ...

    def append(self, msg: llm.Message) -> None: ...
    def write_compact_marker(self) -> None: ...
    def append_all(self, msgs: list[llm.Message]) -> None: ...
    def close(self) -> None: ...
    def __enter__(self) -> "Writer": ...
    def __exit__(self, exc_type, exc, tb) -> None: ...


# SessionInfo 是会话列表中一项的摘要信息。
from datetime import datetime


@dataclass
class SessionInfo:
    id: str  # session ID（目录名）
    title: str  # 第一条 user 消息内容（截断）
    modified_at: datetime  # 最后修改时间
    model: str  # 模型标签
    size: int  # JSONL 文件大小（字节）
    dir: str  # 会话目录绝对路径


# 扫描 sessions_dir，返回按修改时间倒序排列的会话列表。
# 只返回包含 conversation.jsonl 且 ID 能解析为新格式的目录。
def list_sessions(sessions_dir: str) -> list[SessionInfo]: ...


# 从 conversation.jsonl 恢复消息列表。
# 从最后一个 compact 标记之后加载，跳过坏行，截断孤立工具调用。
def load_session(session_dir: str) -> list[llm.Message]: ...


# 删除超过 max_age 的会话目录。
# 只处理新格式 ID 的目录，旧格式跳过。
import datetime as _dt


def clean_expired(sessions_dir: str, max_age: _dt.timedelta) -> None: ...
```

### memory 子包

```python
# 笔记类型。
from enum import StrEnum


class NoteType(StrEnum):
    USER_PREFERENCE = "user_preference"
    CORRECTION_FEEDBACK = "correction_feedback"
    PROJECT_KNOWLEDGE = "project_knowledge"
    REFERENCE_MATERIAL = "reference_material"


# 一条笔记的内存表示。
from datetime import datetime
from dataclasses import dataclass


@dataclass
class Note:
    type: NoteType
    title: str
    slug: str
    content: str
    filename: str
    created: datetime
    updated: datetime


# LLM 返回的单条操作。
@dataclass
class UpdateAction:
    action: str  # "create"/"update"/"delete"
    level: str  # "project"/"user"
    type: str = ""  # NoteType（create 时必需）
    title: str = ""
    slug: str = ""
    content: str = ""
    filename: str = ""  # update/delete 时必需


# 管理单级（项目级或用户级）的笔记文件和索引。
import threading


class Store:
    def __init__(self, dir: str) -> None:
        self._dir = dir
        self._lock = threading.Lock()

    def ensure_dir(self) -> None: ...  # os.makedirs(exist_ok=True)
    def load_index(self) -> str: ...  # 读取 MEMORY.md 内容
    def apply(
        self, actions: list[UpdateAction]
    ) -> None: ...  # 执行 create/update/delete


# 编排项目级和用户级笔记的加载和更新。
import asyncio


class Manager:
    def __init__(
        self,
        project_dir: str,
        user_dir: str,
    ) -> None: ...

    def load_index(self) -> str: ...  # 合并两级索引，截断到 25KB
    def set_provider(self, provider: llm.Provider, model: str) -> None: ...
    async def update_async(self, recent_msgs: list[llm.Message]) -> None: ...
```

### conversation 模块（修改）

```python
class Conversation:
    def __init__(
        self,
        on_append: Callable[[llm.Message], None] | None = None,
        on_replace: Callable[[list[llm.Message]], None] | None = None,
    ) -> None: ...

    # 从已有消息列表创建会话（恢复场景），可选回调。
    @classmethod
    def from_messages(
        cls,
        msgs: list[llm.Message],
        on_append: Callable[[llm.Message], None] | None = None,
        on_replace: Callable[[list[llm.Message]], None] | None = None,
    ) -> "Conversation": ...
```

### compact.state 模块（修改）

```python
from dataclasses import dataclass


@dataclass
class SessionContext:
    session_id: str  # 形如 "20260601-143022-a1b2"
    session_dir: str  # <workspace>/.endless-code/sessions/<session_id>
    spill_dir: str  # session_dir + "/tool-results"


# 改为 YYYYMMDD-HHMMSS-xxxx 格式。
def _new_session_id() -> str: ...


# 打开已有会话目录（恢复场景）。
def open_session_context(workspace: str, session_id: str) -> SessionContext: ...


# 从 ID 前 15 位解析 YYYYMMDD-HHMMSS，供清理和排序使用。
def parse_session_time(session_id: str) -> datetime: ...
```

### prompt 模块（修改）

```python
# 组装完整系统提示。
# instructions 非空时填入 custom-instructions 模块（priority 80）。
# memory 非空时填入 long-term-memory 模块（priority 100）。
def build_system_prompt(instructions: str, memory: str) -> str: ...
```

### agent 模块（修改）

```python
# Agent 新增字段。
class Agent:
    def __init__(
        self,
        ...,
        memory_manager: memory.Manager | None = None,
        instruction_text: str = "",
        memory_text: str = "",
    ) -> None: ...
```

## 模块交互

### 启动流程

```
cli.main()
  ├─ config.load()
  ├─ instructions.Loader(project_root).load() → instruction_text
  ├─ memory.Manager(project_mem_dir, user_mem_dir, provider=None, model="")
  │   .load_index() → memory_text   （provider 未选定时先空，选定后 set_provider）
  ├─ asyncio.create_task(session.clean_expired(sessions_dir, timedelta(days=30)))
  ├─ tool.default_registry()
  ├─ mcp.Manager() → mcp_tools → registry.register(...)
  ├─ permission.Engine()
  └─ EndlessCodeApp(providers, ..., mem_mgr, instruction_text).run()
       └─ 选定 provider 后：
           ├─ compact.new_session_context(root) → ses_ctx
           ├─ session.Writer(ses_ctx.session_dir, model) → writer
           ├─ Conversation(on_append=writer.append, on_replace=writer.replace)
           ├─ mem_mgr.set_provider(provider, model)
           └─ Agent(..., memory_manager=mem_mgr, instruction_text=instruction_text)
```

### Agent Loop 与记忆更新

```
async def run(self, conv, mode):
    while True:
        text, tool_calls = await self._stream_once()
        if not tool_calls:   # Done 分支
            conv.add_assistant(text)   # → writer.append (via on_append)
            # 每 5 轮或检测到显式记忆请求时触发异步记忆更新
            if self._mem_mgr is not None:
                self._runtime.turn_count += 1
                recent_msgs = self._extract_recent_turn(conv)
                if (self._runtime.turn_count % 5 == 0
                        or _has_memory_signal(recent_msgs)):
                    asyncio.create_task(
                        self._mem_mgr.update_async(recent_msgs)
                    )
            yield StreamEvent(done=True)
            return
        # 有工具调用：继续迭代
        ...
```

### /resume 恢复流程

```
TUI: /resume → state = RESUMING
  ├─ session.list_sessions(sessions_dir) → items
  ├─ 显示 OptionList（上下选择 + 输入框搜索）
  ├─ Enter 选择：
  │   ├─ session.load_session(selected_dir) → msgs
  │   ├─ 检查孤立工具调用 → 截断
  │   ├─ 估算 token → 超限则压缩
  │   ├─ 检查时间跨度 → 超 6h 追加提醒
  │   ├─ Conversation.from_messages(msgs, on_append, on_replace) → new_conv
  │   ├─ compact.open_session_context(root, selected_id) → new_ses_ctx
  │   ├─ session.Writer.open_existing(selected_dir) → new_writer
  │   ├─ 替换 TUI 的 conv、writer、ses_ctx、runtime.session
  │   ├─ 显示 "已恢复会话 <id>，共 N 条消息"
  │   └─ state = IDLE
  └─ Esc → state = IDLE（不变）
```

### JSONL 写入时序

```
用户输入 "hello"
  → conv.add_user("hello")
    → on_append(Message(role="user", content="hello"))
      → writer.append(msg, model, is_first=True)
        → {"role":"user","content":"hello","ts":1717200000,"model":"gpt-5.4-mini"}\n

Agent 回复 "hi!"
  → conv.add_assistant("hi!")
    → on_append(Message(role="assistant", content="hi!"))
      → writer.append(msg, model, is_first=False)
        → {"role":"assistant","content":"hi!","ts":1717200005}\n

压缩触发
  → conv.replace_history(new_msgs)
    → on_replace(new_msgs)
      → writer.write_compact_marker()
        → {"type":"compact","ts":1717201000}\n
      → writer.append_all(new_msgs)
        → 逐条追加新消息
```

## 文件组织

```
src/endless_code/
├── instructions/
│   ├── __init__.py
│   ├── loader.py            — Loader 类、load、_load_file、@include 展开
│   └── ... (tests/test_instructions_loader.py)
├── session/
│   ├── __init__.py
│   ├── writer.py            — Writer、Entry、append、write_compact_marker
│   ├── list.py              — list_sessions、SessionInfo、扫描逻辑
│   ├── load.py              — load_session、坏行跳过、孤立截断
│   ├── cleanup.py           — clean_expired、ID 时间戳解析
│   └── ... (tests/test_session.py)
├── memory/
│   ├── __init__.py
│   ├── types.py             — NoteType、Note、UpdateAction
│   ├── store.py             — Store、笔记文件 CRUD、索引读写
│   ├── manager.py           — Manager、load_index、update_async
│   ├── prompts.py           — 记忆更新 prompt 模板
│   └── ... (tests/test_memory.py)
├── prompt/__init__.py       — build_system_prompt 签名变更（+instructions, +memory）
├── prompt/modules.py        — optional_modules 改为接受参数
├── conversation.py          — from_messages、回调触发
├── compact/
│   └── state.py             — _new_session_id 格式变更、session_dir 字段、open_session_context
├── agent/
│   └── __init__.py          — Agent、SessionRuntime 与记忆更新生命周期
├── tui/
│   ├── commands.py          — /resume 注册
│   └── app.py               — RESUMING 状态、会话列表、搜索与恢复流程
└── cli.py                   — 启动流程串联

tests/
├── test_instructions_loader.py
├── test_session.py
├── test_memory.py
├── test_conversation.py     — 回调测试补充
└── test_prompt/__init__.py           — 新签名测试
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 指令文件格式 | 手写 Markdown | 用户直接编辑，不需要特殊工具；与系统提示纯文本注入无缝衔接 |
| @include 深度限制 | 5 层 | 足够覆盖合理的模块化拆分，又不会因为误配无限递归 |
| 会话存储格式 | JSONL 追加写 | 追加快、崩溃安全（只丢最后一行）、无需维护索引文件 |
| 压缩后 JSONL 处理 | compact 标记行 + 追加新消息 | 保持追加语义，恢复时从最后 compact 标记开始加载 |
| session ID 格式 | YYYYMMDD-HHMMSS-xxxx | 人类可读，可直接从 ID 解析时间戳用于过期清理和排序 |
| 记忆更新触发点 | 每 5 轮或检测到"记住"关键词时 | 定时提取控制频率；关键词检测保证显式请求不漏 |
| 记忆去重策略 | LLM 判断 | 语义级去重比机械字符串匹配更准确，且实现简单 |
| 记忆注入方式 | 索引注入系统提示 | 约 2-3K tokens 开销可控，模型通过索引感知全貌，详情按需读文件 |
| Conversation 回调 | 构造时注入可调用对象 | 最小侵入，不需要引入事件总线；未设置回调时零开销 |
| /resume 列表组件 | 复用 Textual `OptionList` | 与已有 provider 选择列表一致的交互模式，减少代码和认知负担 |
| 记忆 provider | 复用主会话 provider | 简单直接，不引入额外配置；未来可扩展为配置专用 provider |
| 异步并发模型 | asyncio task（不引入线程池） | Textual + LLM SDK 都基于 asyncio，记忆更新跑在同一 event loop 自然不阻塞 UI |
| 文件写入刷盘 | `file.flush()` + `os.fsync(fileno())` | Python 标准库等价于 Go 的 `f.Sync()`，保证崩溃前数据落盘 |
| YAML frontmatter | 手写解析 + `yaml.safe_dump` 生成 | 笔记 frontmatter 字段少且固定，避免引入额外 frontmatter 库 |
