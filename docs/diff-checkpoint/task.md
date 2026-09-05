# Diff 展示与 Checkpoint 回滚 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/endless_code/checkpoint/diff.py` | diff 纯函数：file_diff / truncate_diff / diff_stat |
| 新建 | `src/endless_code/checkpoint/metadata.py` | checkpoint 元数据 JSONL 读写 |
| 新建 | `src/endless_code/checkpoint/backend_git.py` | git shadow 后端 |
| 新建 | `src/endless_code/checkpoint/backend_files.py` | 非 git 文件快照后端 |
| 新建 | `src/endless_code/checkpoint/__init__.py` | Manager 门面 + 数据类型 + 后端选择 |
| 修改 | `src/endless_code/agent/__init__.py` | checkpoint 切入、ApprovalRequest.diff、ToolEvent.diff/stat |
| 修改 | `src/endless_code/command/host.py` | 新增 start_rewind / rewind_available / get_checkpoint_mode 抽象方法 |
| 修改 | `src/endless_code/command/builtin.py` | 注册 /rewind 命令 |
| 修改 | `src/endless_code/tui/app.py` | REWINDING 状态、审批 diff 渲染、结果 diff 渲染、host 方法实现 |
| 新建 | `tests/test_diff.py` | diff 纯函数测试 |
| 新建 | `tests/test_checkpoint_files.py` | 文件快照后端测试 |
| 新建 | `tests/test_checkpoint_git.py` | git 后端测试（临时仓库夹具） |
| 新建 | `tests/test_agent_checkpoint.py` | Agent 层切入与事件字段测试 |
| 修改 | `tests/test_command_*.py`（现有命令测试文件） | 覆盖 /rewind 注册与分发 |

---

## T1: diff 纯函数
**文件：** `src/endless_code/checkpoint/diff.py`（新建目录与文件）  **依赖：** 无

**步骤：**
1. 新建 `src/endless_code/checkpoint/` 目录与空 `__init__.py`（T1 仅占位，T5 再填门面）。
2. 实现 `file_diff(path, old, new, *, context=3) -> str`：基于 `difflib.unified_diff` 生成带 `+++`/`---`/`@@` 头的文本；`old is None` 时输出"新建文件"提示 + 全部 `+` 行；`new is None` 时输出"文件已删除"+ 全部 `-` 行。
3. 二进制判定：内容含 `\x00` 或 UTF-8 解码失败 → 返回 `（二进制文件，不展示 diff）`。
4. 实现 `truncate_diff(diff, max_lines=60, max_chars=8000)`：超限截断并追加 `[diff truncated，共 N 行]`；实现 `diff_stat(diff) -> str` 返回 `+X -Y`。

**验证：** 运行 `python -m pytest tests/test_diff.py -q`，预期全部通过（覆盖：新增/修改/删除/二进制/截断/统计）。

## T2: 元数据 JSONL 读写
**文件：** `src/endless_code/checkpoint/metadata.py`  **依赖：** 无

**步骤：**
1. 定义 `CheckpointMeta` dataclass（index/timestamp/tool/target/kind/ref/conv_len）与 `to_dict()/from_dict()`。
2. 实现追加写 `append_meta(session_dir, meta)`（崩溃安全：单行 JSON + 立即 flush）、`list_meta(session_dir) -> list[CheckpointMeta]`（跳过损坏行）、`clear_meta(session_dir)`（清空文件）。

**验证：** 运行 `python -m pytest tests/test_checkpoint_files.py -k metadata -q`（T3 中补测试），本步以模块可导入为准。

## T3: 非 git 文件快照后端
**文件：** `src/endless_code/checkpoint/backend_files.py`、`tests/test_checkpoint_files.py`  **依赖：** T2

**步骤：**
1. 实现 `FilesBackend.create(workspace, session_dir, tool, target) -> CheckpointMeta`：遍历工作区（跳过 `.git`、`.endless-code`、`node_modules`、`__pycache__`），把现存文件按字节复制到 `session_dir/checkpoints/files/<index>/`，index 取元数据最大值 +1，追加元数据。
2. 实现 `FilesBackend.restore(meta, workspace) -> (restored, deleted)`：从快照目录覆盖回文件；删除当前工作区中不在快照清单内的文件。
3. 测试：tmp_path 工作区创建/修改/删除文件后 create→restore，断言文件状态精确回到快照点（含删除多余文件）；元数据损坏行容错。

**验证：** 运行 `python -m pytest tests/test_checkpoint_files.py -q`，预期全部通过。

## T4: git shadow 后端
**文件：** `src/endless_code/checkpoint/backend_git.py`、`tests/test_checkpoint_git.py`  **依赖：** T2

**步骤：**
1. 封装 `_git(args, env_extra)`：`asyncio.create_subprocess_exec("git", ...)`，注入 `GIT_INDEX_FILE=<session_dir>/tmp-index`，10s 超时，返回 (returncode, stdout)。
2. `GitBackend.create`：临时索引 `git add -A` → `git write-tree` → 与父提交的树比对（无差异复用上一 checkpoint）→ `git commit-tree -p <parent> -m "endless-code checkpoint #N"` → `git update-ref refs/endless-code/<session_id>/head <sha>` → 写元数据（ref=sha）。git 命令失败或超时 → 返回 None 并不抛异常。
3. `GitBackend.restore(sha, workspace)`：临时索引 `git read-tree <sha>` → `git checkout-index -a -f` → `git ls-files`（临时索引）得快照文件集 → 删除工作区中不在集合内的已跟踪残留文件。
4. `GitBackend.clear`：`git update-ref -d refs/endless-code/<session_id>/head`。
5. 测试（pytest 夹具临时 `git init` 仓库 + 配置 user.email/user.name）：创建后断言 `git log`（分支）无新提交、`git status --porcelain` 与创建前一致、shadow ref 存在；修改文件后 restore 精确回到快照（含删除新增文件）；clear 后 shadow ref 消失。

**验证：** 运行 `python -m pytest tests/test_checkpoint_git.py -q`，预期全部通过。

## T5: Manager 门面与后端选择
**文件：** `src/endless_code/checkpoint/__init__.py`  **依赖：** T3、T4

**步骤：**
1. 定义 `RestoreScope`（FILES_ONLY/CONVERSATION_ONLY/BOTH）与 `RestoreReport`。
2. 实现 `CheckpointManager`：构造时检测工作区是否 git repo 选后端；`create()` 组合后端 + 元数据 + 审计；`list()` 读元数据；`restore(meta, scope)`：scope 含 FILES 时调后端 restore 并记 `checkpoint_restored` 审计事件；`clear()` 清后端 + 元数据；`mode_label()`。
3. 导出公共符号，`checkpoint` 包对外只暴露 Manager 与数据类型。

**验证：** 运行 `python -m pytest tests/test_checkpoint_files.py tests/test_checkpoint_git.py -q`，预期全绿且无回归。

## T6: Agent 层切入 checkpoint 与 diff
**文件：** `src/endless_code/agent/__init__.py`、`tests/test_agent_checkpoint.py`  **依赖：** T5

**步骤：**
1. `Agent.__init__` 新增可选参数 `checkpoint: CheckpointManager | None = None`。
2. `ApprovalRequest` 增加 `diff: str = ""` 字段；在 `_execute_events` ASK 分支 yield 审批事件前：若 call 是 `write_file`/`edit_file`，解析 args JSON 得路径与新旧内容，调 `file_diff` 填入。
3. `ToolEvent` 增加 `diff: str = ""`、`stat: str = ""` 字段；串行分支权限通过后、执行前调 `self._checkpoint.create(call.name, explanation.target, conv.length())`；执行成功后对写类工具再算实际 diff 填入 END 事件。
4. 测试：用假 Provider 驱动一轮 write_file 调用，断言审批事件携带非空 diff、END 事件携带 diff/stat、checkpoint 元数据新增一条；checkpoint=None 时行为与现状一致（回归）。

**验证：** 运行 `python -m pytest tests/test_agent_checkpoint.py tests/test_agent* -q`，预期全部通过（含既有 agent 测试无回归）。

## T7: /rewind 命令与 host 接口
**文件：** `src/endless_code/command/host.py`、`src/endless_code/command/builtin.py`、现有命令测试文件  **依赖：** 无（可与 T6 并行）

**步骤：**
1. `CommandHost` 增加 `start_rewind()`、`rewind_available() -> bool`、`get_checkpoint_mode() -> str` 三个方法。
2. `builtin.py` 注册 `/rewind`（kind=UI，描述"回滚文件/对话到本会话检查点"）；不可用时 host.show_notice 提示。
3. 命令测试补：/rewind 可被 parse/lookup/dispatch。

**验证：** 运行 `python -m pytest tests/ -k command -q`，预期全部通过。

## T8: TUI 渲染与 REWINDING 状态
**文件：** `src/endless_code/tui/app.py`  **依赖：** T6、T7

**步骤：**
1. `_render_approval`：`req.diff` 非空时在审批信息上方渲染着色 diff（`+`/`+++` 绿、`-`/`---` 红、`@@` 黄）。
2. `_handle_tool_event` END：`event.diff` 非空时在结果行下写 `stat` + diff（dim 着色）。
3. 新增 `SessionState.REWINDING` 与 `start_rewind()`/`rewind_available()`/`get_checkpoint_mode()` host 实现：复用 `#resume-list` 展示时间线；Enter 后切换为范围选择四项；↑↓/Enter/Esc 交互沿用 `_on_key` 现有模式。
4. 恢复执行：按 scope 调 `manager.restore()`；含对话时用 `Conversation.from_messages(截断到 meta.conv_len, ...)` 重建；仅文件时注入回滚系统提示 user 消息；完成后写审计、写提示、回 IDLE。
5. `_restore_session` 中重建 CheckpointManager（同会话目录）。

**验证：** 运行 `python -m pytest tests/ -k tui -q`，预期全部通过；手工启动走一遍 /rewind 确认交互不报错。

## T9: 工程收尾
**文件：** 无新增  **依赖：** T1–T8

**步骤：**
1. 运行全量测试与 lint，修复回归。
2. 确认 `docs/diff-checkpoint/` 四份文档已落盘。
3. 提交代码（feat 前缀，按逻辑分组：checkpoint 模块 / agent+diff / 命令+TUI）。

**验证：** 运行 `python -m pytest -q`（全部通过）与 `ruff check src tests` + `ruff format --check src tests`（零告警）。

## 执行顺序

```text
T1 -> T2 -> T3 -> T4 -> T5 -> T6 -> T8
                 \----------------> T7 -> T8 -> T9
```
