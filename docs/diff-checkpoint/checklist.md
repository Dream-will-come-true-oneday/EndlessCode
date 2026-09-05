# Diff 展示与 Checkpoint 回滚 Checklist

> 验收状态见各项末尾标注；证据为对应测试名或验证记录（2026-09-05 验收）。

## 功能与集成

- [x] 审批 diff（F1/AC1/AC2）：AI 修改已有文件触发审批时，弹窗出现 unified diff（增绿删红带上下文行），批准前文件内容不变（证据：`test_approval_carries_diff_for_new_file`、`test_approval_carries_diff_for_edit`；TUI 着色渲染 `_styled_diff`）
- [x] 新建文件审批展示全部为新增（证据：`test_approval_carries_diff_for_new_file`，diff 含"新建文件"与 `+` 行）
- [x] diff 截断与二进制占位（F1/N2/AC10）：超限尾部 `[diff truncated，共 N 行]`；二进制显示占位说明（证据：`test_diff.py::TestTruncate`、`test_binary_target_diff_placeholder`，E2E 场景 3 输出占位文案）
- [x] 结果 diff 摘要（F2/AC3）：END 事件携带 diff 与 `+X -Y` 统计，TUI 渲染在工具结果下方（证据：`test_end_event_carries_diff_stat_and_checkpoint_created`）
- [x] git checkpoint 创建（F3/AC4）：当前分支 `git log` 无新提交、shadow ref 存在、元数据 +1（证据：`test_create_does_not_pollute_branch`、E2E 场景 1 `git log 无新增: True | shadow ref 存在: True`）
- [x] git 无变化跳过（F3/N1）：无文件变更时复用上一 checkpoint（证据：`test_no_change_reuses_last_checkpoint`）
- [x] 非 git 快照（F3/AC5）：快照目录出现文件副本且可列出（证据：`test_create_and_restore_roundtrip`、E2E 场景 2）
- [x] /rewind 时间线（F4/AC6）：列出序号/时间/工具/目标；Esc 退出无任何文件变更（证据：`test_rewind_esc_leaves_files_untouched`）
- [x] 三种回滚范围（F5/AC7）：仅文件——文件恢复、对话保留；仅对话——文件不动、消息截断；都恢复——两者同时生效（证据：`test_rewind_files_only_keeps_conversation_and_notifies`、`test_rewind_both_restores_conversation`、`test_conversation_only_leaves_files_untouched`）
- [x] 回滚后感知（F6/AC8）：仅文件回滚后注入 `[系统提示] 工作区文件已回滚到 checkpoint #N` 的 user 消息（证据：`test_rewind_files_only_keeps_conversation_and_notifies`）
- [x] resume 后可回滚（F7/AC9）：同会话目录重建 Manager 后仍列出并成功恢复（证据：`test_restore_across_manager_instances`；`_restore_session` 重建 Manager）
- [x] 清理入口（F7）：清理后快照/元数据被清空（证据：`test_rewind_cleanup_clears_checkpoints`、`test_clear_removes_ref`、`test_clear_removes_snapshots`）

## 工程检查

- [x] 项目编译通过（验证：`python -m compileall src` → OK）
- [x] 全量单元测试通过（验证：`python -m pytest -q --ignore=test_modifications.py` → 276 passed, 1 skipped；`test_modifications.py` 为上一轮遗留的根目录临时脚本，其断言的字段在当前源码中本就不存在，属既有失败，与本次改动无关）
- [x] lint 通过（验证：`ruff check src tests` → All checks passed；`ruff format --check` → 101 files already formatted）
- [x] checkpoint 异常不拖垮工具执行：非 git 目录 git 后端返回 None、工具照常执行（证据：`test_git_failure_returns_none`；另有 `test_restore_missing_snapshot_is_noop`）
- [x] Agent 未注入 checkpoint 时行为与现状一致（证据：`test_agent_without_checkpoint_still_emits_diff` 及既有 agent/TUI 测试零回归）
- [x] 恢复操作写入审计 `checkpoint_restored`（证据：`test_restore_writes_audit_event`）

## 端到端

- [x] **git 项目完整回滚**：AI 修改 a.py（write + edit 两轮）→ 审批 diff 展示 → 批准 → 结果区 `+1 -1` 统计 → checkpoint 2 条 → shadow commit 恢复（BOTH 语义）→ a.py 回到 v1、对话截断到 checkpoint 时刻、`git log` 全程无污染（验证记录：E2E 场景 1 输出全 True）
- [x] **非 git 项目降级**：同流程走文件快照后端，仅文件恢复成功、对话保留、AI 内容被还原（验证记录：E2E 场景 2 输出全 True；界面模式提示为 "file snapshot"）
- [x] **Windows 全链路**：以上全部在本机 Windows 11 + Python 3.12.7 执行通过（含 git 子进程、路径、编码）；交互式 TUI 手工走查需真实 API key，交互逻辑已由 `tests/test_tui_rewind.py` 的 Pilot 驱动测试覆盖
