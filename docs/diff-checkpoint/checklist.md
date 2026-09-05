# Diff 展示与 Checkpoint 回滚 Checklist

## 功能与集成

- [ ] 审批 diff（F1/AC1/AC2）：AI 修改已有文件触发审批时，弹窗出现 unified diff（增绿删红带上下文行），批准前文件内容不变（验证：单测断言 ApprovalRequest.diff 非空且内容含 `-旧/+新` 行；手工 TUI 观察着色）
- [ ] 新建文件审批展示全部为新增（验证：单测覆盖 write 新路径场景，diff 无 `-` 行）
- [ ] diff 截断与二进制占位（F1/N2/AC10）：>8000 字符 diff 尾部有 `[diff truncated]`；二进制文件显示占位说明（验证：test_diff.py 用例）
- [ ] 结果 diff 摘要（F2/AC3）：编辑类工具 END 事件携带 diff 与 `+X -Y` 统计，TUI 渲染在工具结果下方（验证：test_agent_checkpoint.py 断言 ToolEvent 字段）
- [ ] git checkpoint 创建（F3/AC4）：编辑后当前分支 `git log` 无新提交、`git status --porcelain` 与创建前一致、shadow ref 存在、元数据 +1（验证：test_checkpoint_git.py）
- [ ] git 无变化跳过（F3/N1）：连续两次 checkpoint 之间无文件变更时复用上一条（验证：test_checkpoint_git.py 用例）
- [ ] 非 git 快照（F3/AC5）：非 git 目录编辑后快照目录出现文件副本且可列出（验证：test_checkpoint_files.py）
- [ ] /rewind 时间线（F4/AC6）：列出序号/时间/工具/目标；Esc 退出无任何文件变更（验证：test_command_rewind + 手工 TUI）
- [ ] 三种回滚范围（F5/AC7）：仅文件——文件恢复、对话保留并注入回滚系统提示；仅对话——文件不动、消息截断；都恢复——两者同时生效（验证：test_checkpoint_files/git restore 用例 + TUI 集成单测断言 conv.length 变化）
- [ ] 回滚后感知（F6/AC8）：仅文件回滚后对话中出现 `[系统提示] 文件已回滚到 checkpoint #N` 的 user 消息（验证：集成用例断言消息存在）
- [ ] resume 后可回滚（F7/AC9）：/resume 恢复会话后 /rewind 仍列出历史 checkpoint 且恢复成功（验证：集成测试——创建 checkpoint→重建 Manager（同目录）→restore 成功）
- [ ] 清理入口（F7）：/rewind 面板清理后 shadow ref/快照目录与元数据被清空（验证：clear 用例）

## 工程检查

- [ ] 项目编译通过（验证：`python -m compileall src`）
- [ ] 全量单元测试通过（验证：`python -m pytest -q`，含既有测试零回归）
- [ ] lint 通过（验证：`ruff check src tests` + `ruff format --check src tests`）
- [ ] checkpoint 异常不拖垮工具执行：git 缺失/超时时 create 返回 None，工具照常执行（验证：test_checkpoint_git.py 模拟失败用例）
- [ ] Agent 未注入 checkpoint 时行为与现状一致（验证：test_agent_checkpoint.py 回归用例）
- [ ] 恢复操作写入审计 `checkpoint_restored`（验证：断言 AuditWriter 收到事件）

## 端到端

- [ ] **git 项目完整回滚**：在 git 项目中让 AI 修改 a.py → 审批弹窗看到 diff → 批准 → 结果区看到 +X -Y 摘要 → `/rewind` 看到该检查点 → 选"都恢复" → a.py 内容恢复、对话回到修改前（验证：手工执行 + `git log` 无污染 + 文件内容比对）
- [ ] **非 git 项目降级**：在非 git 目录重复上述流程，回滚同样成功，界面提示当前为文件快照模式（验证：手工执行）
- [ ] **Windows 全链路**：AC1~AC9 在 Windows 环境下全部走通（验证：本机执行全量测试 + 手工场景）
