# Diff 展示与 Checkpoint 回滚 Plan

## 架构概览

新增独立的 `checkpoint/` 模块（含 diff 计算），Agent 层在两个切入点接入：**审批前**计算待执行 diff 塞进 `ApprovalRequest`，**执行前**创建 checkpoint，**执行后**计算结果 diff 塞进 `ToolEvent`。TUI 复用 resume-list 的交互模式新增 REWINDING 状态承接 `/rewind`。会话回滚复用 `Conversation.replace_history()` 原子替换 + Writer 的 replace 通道。

## 核心数据结构与接口

### `checkpoint/__init__.py` — Manager（门面）

```python
class RestoreScope(Enum): FILES_ONLY; CONVERSATION_ONLY; BOTH

@dataclass
class CheckpointMeta:
    index: int          # 会话内序号，从 1 开始
    timestamp: int      # epoch 秒
    tool: str           # 触发工具名
    target: str         # 主要目标（路径或命令摘要）
    kind: str           # "git" | "files"
    ref: str            # git: shadow commit sha；files: 快照子目录名
    conv_len: int       # checkpoint 时的对话消息数（回滚对话用）

@dataclass
class RestoreReport:
    meta: CheckpointMeta
    restored_files: list[str]
    deleted_files: list[str]
    ok: bool; error: str = ""

class CheckpointManager:
    def __init__(self, workspace: str, session_dir: str, audit: AuditWriter | None) -> ...
    def available(self) -> bool                  # git repo → git 后端，否则 files 后端
    def mode_label(self) -> str                  # "git shadow" / "file snapshot"
    def create(self, tool: str, target: str, conv_len: int) -> CheckpointMeta | None
    def list(self) -> list[CheckpointMeta]       # 从会话目录元数据 JSONL 读取
    def restore(self, meta: CheckpointMeta, scope: RestoreScope) -> RestoreReport
    def clear(self) -> None                      # 清理本会话 checkpoint（shadow ref / 快照目录）
```

### `checkpoint/backend_git.py` — GitBackend

- **create**：用临时索引（`GIT_INDEX_FILE=<会话目录>/tmp-index` 执行 `git add -A`）→ `git write-tree` → 与上一棵树比对（无差异则返回上一 checkpoint，跳过）→ `git commit-tree -p <父> -m "endless-code checkpoint"` → `git update-ref refs/endless-code/<session_id>/head <sha>`。全程不触碰用户的 index 与工作区，提交不出现在任何分支上。
- **restore(tree)**：临时索引 `git read-tree <tree>` → `git checkout-index -a -f` 写出全部文件 → 计算当前工作区相对该树的"多余文件"清单并删除（使工作区精确回到快照状态）。
- **clear**：`git update-ref -d refs/endless-code/<session_id>/head`。

### `checkpoint/backend_files.py` — FilesBackend（非 git 降级）

- **create**：记录当前存在的相对路径清单 + 逐文件字节复制到 `session_dir/checkpoints/files/<index>/`；元数据写 `session_dir/checkpoints/metadata.jsonl`（两后端共用）。
- **restore**：按清单覆盖回文件、删除快照后新增的文件。
- 元数据 JSONL 是 checkpoint 列表与 `/resume` 后恢复能力的唯一事实来源（AC9）。

### `checkpoint/diff.py` — diff 计算（纯函数，无状态）

```python
def file_diff(path: str, old: str | None, new: str | None, *, context: int = 3) -> str
    # old=None → 新建文件（全部为 +）；二进制/解码失败 → "（二进制文件，不展示 diff）"
    # 输出统一为带 +/- 前缀的 unified 风格文本，供 Rich 着色渲染
def truncate_diff(diff: str, max_lines: int = 60, max_chars: int = 8000) -> str  # 尾部 [truncated]
def diff_stat(diff: str) -> str   # "+3 -1" 统计行
```

## 模块设计

### Agent（修改 `src/endless_code/agent/__init__.py`）
- 职责：切入 checkpoint 与 diff，保持事件流协议扩展向后兼容。
- `ApprovalRequest` 增加字段 `diff: str = ""`：在 `_execute_events` 对 `write_file`/`edit_file` yield 审批事件**之前**，解析参数取路径、读取旧内容、按工具语义计算新内容（edit 用 old/new 替换；write 直接用新内容），生成 diff 填入。
- `ToolEvent` 增加字段 `diff: str = ""`、`stat: str = ""`：工具成功执行后再读一次文件计算实际 diff。
- `_execute_events` 串行（有副作用）分支中，权限判定通过后、调用 `_execute_call` 前：`self._checkpoint.create(call.name, explanation.target, conv.length())`；Manager 为 None 时跳过（Agent 构造函数新增可选参数 `checkpoint`）。
- 依赖：checkpoint、security（脱敏）；不依赖 TUI。

### command（修改 `command/builtin.py`、`host.py`、`types.py`）
- 新增 `/rewind` 命令（kind=UI），handler 调 `host.start_rewind()`。
- `CommandHost` 新增方法：`start_rewind()`、`get_checkpoint_mode() -> str`、`rewind_available() -> bool`。

### TUI（修改 `src/endless_code/tui/app.py`）
- 新增 `SessionState.REWINDING`：复用 `#resume-list` OptionList 展示 checkpoint 时间线（`#N 时间 · 工具 · 目标`），↑↓/Enter 选择、Esc 取消；选中后进入范围选择（同一列表切换为四项：仅文件/仅对话/都恢复/取消），键盘交互与审批一致。
- `_render_approval`：`req.diff` 非空时以 Rich 着色文本块渲染（`+` 绿 / `-` 红 / `@@` 黄，通过逐行 `RichText` 构造，保持可复制），置于审批选项之上。
- `_handle_tool_event`：END 且 `event.diff` 非空时，在工具结果下方渲染 `stat` + 精简 diff。
- `/rewind` 的 restore 调用 `CheckpointManager.restore()`；scope 含对话时用 `self._conv = Conversation.from_messages(截断后的消息, ...)` 并触发 Writer 重写（复用 `_restore_session` 的 writer 重建路径）；scope 仅文件时注入一条 user 系统提示消息：`[系统提示] 工作区文件已回滚到 checkpoint #N，相关文件内容可能已变化，请重新读取后再修改`（F6）。
- restore 完成写一条审计事件（`checkpoint_restored`，含 checkpoint index 与 scope）——满足 N3；用户主动回滚不经过权限 ASK（决策：权限引擎拦截的是模型发起的调用，用户本人操作属于人在回路本身）。
- `clear_session`/`action_quit` 时不清 checkpoint（随会话持久化），`/rewind` 面板内提供"清理本会话 checkpoint"选项（F7）。

### checkpoint（新建 `src/endless_code/checkpoint/`）
- 职责、接口见上；依赖：标准库 + 调用系统 `git`（`asyncio.create_subprocess_exec`，超时 10s）；审计写入复用 `AuditWriter.record()`。

## 模块交互（调用链）

1. **审批 diff**：模型请求 edit_file → Agent 权限判定 ASK → Agent 读旧文件算 diff → `Event(approval=ApprovalRequest(..., diff=...))` → TUI 渲染 diff + 三选项 → 用户批准。
2. **checkpoint + 结果 diff**：批准/ALLOW → `checkpoint.create(...)` → `registry.execute` → Agent 读新内容算 diff → `Event(tool=ToolEvent(END, ..., diff, stat))` → TUI 渲染。
3. **/rewind**：用户输入 `/rewind` → Dispatcher → host.start_rewind() → TUI REWINDING 列表 → 选中 meta + scope → `manager.restore(meta, scope)` → （可选对话截断 / 系统提示注入）→ 审计 → 回到 IDLE。
4. **resume 后**：`_restore_session` 时按会话目录重建 `CheckpointManager`（元数据 JSONL 天然随目录恢复）。

## 文件组织

```text
src/endless_code/
├── checkpoint/
│   ├── __init__.py        # Manager 门面 + RestoreScope/CheckpointMeta/RestoreReport
│   ├── backend_git.py     # shadow ref 后端（临时索引方案）
│   ├── backend_files.py   # 非 git 文件快照后端
│   ├── diff.py            # file_diff / truncate_diff / diff_stat 纯函数
│   └── metadata.py        # 元数据 JSONL 读写（append/list/clear）
├── agent/__init__.py      # 修改：checkpoint 切入、ApprovalRequest.diff、ToolEvent.diff/stat
├── command/
│   ├── builtin.py         # 修改：新增 /rewind
│   └── host.py            # 修改：start_rewind / rewind_available / get_checkpoint_mode
└── tui/app.py             # 修改：审批 diff 渲染、结果 diff 渲染、REWINDING 状态
tests/
├── test_checkpoint_git.py       # 临时仓库夹具：create/list/restore/clear
├── test_checkpoint_files.py
├── test_diff.py
├── test_agent_checkpoint.py     # Agent 层切入与事件字段
└── test_command_rewind.py       # 命令注册/分发
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| shadow commit 方式 | 临时索引（GIT_INDEX_FILE）+ write-tree/commit-tree/update-ref | 不污染用户 index、暂存区与分支历史；aider 验证过的成熟路径 |
| diff 计算位置 | Agent 层，而非工具内部 | 工具保持薄、Result 内容（送模型）不变；审批与结果两处复用同一纯函数 |
| 非 git 降级 | 文件字节快照 + 元数据 JSONL | 任何项目可用；元数据与会话目录绑定，天然支持 /resume 后回滚 |
| 大仓库降频（N1） | 与上一棵树比对无差异即跳过；git 子进程 10s 超时，超时则本工具调用不建 checkpoint 并在审计中记录 | 行为可预期，避免拖慢高频写操作 |
| 会话回滚 | 截断消息后用 Conversation.from_messages 重建 + Writer 重写 | 复用 /resume 已验证的恢复路径，JSONL replace 通道现成 |
| 恢复是否过权限 ASK | 不过，仅记审计 | 权限引擎拦截模型行为；用户本人操作即人在回路，再 ask 自己无意义 |
| diff 渲染 | 逐行 RichText 手工着色，不用 Markdown | 延续"可选中复制"的既有取舍（N5） |
| 二进制文件 | diff 占位说明，快照按字节复制 | N2，Windows 编码安全 |
