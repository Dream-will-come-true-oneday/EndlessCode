# EndlessCode 历史时间线整理 Tasks

## 文件清单

| 操作 | 文件/位置 | 职责 |
|---|---|---|
| 新建 | `docs/history-rewrite/spec.md` | 已批准的范围与验收标准 |
| 新建 | `docs/history-rewrite/plan.md` | 已批准的技术设计 |
| 新建 | `docs/history-rewrite/task.md` | 本执行任务清单 |
| 新建 | `docs/history-rewrite/checklist.md` | 待批准的验收清单 |
| 生成 | 备份目录或临时目录 `evidence.json` | 原始提交和外部证据快照 |
| 生成 | 备份目录或临时目录 `milestones.json` | 里程碑到来源提交的映射 |
| 生成 | 备份目录或临时目录 `mapping.md` | 最终新旧提交对应表 |
| 修改 | 仅本地 `refs/heads/main` | 验收通过后移动到线性历史 |

## T1: 冻结输入状态

**文件：** Git refs、备份目录、临时状态文件  
**依赖：** 无

**步骤：**
1. 确认当前分支为 `main`，工作区只包含已知未跟踪文件和历史整理文档。
2. 记录 `main`、`dev`、`test`、`origin/*` 的完整 SHA。
3. 记录 `.coverage`、`closure_demo.py` 的 SHA-256。
4. 记录旧 `main` 的最终树清单和内容哈希。

**验证：** 运行 `git status --short --branch`、`git rev-parse main dev test origin/main origin/dev origin/test` 和 `Get-FileHash`，输出与备份记录一致。

## T2: 校验备份可恢复

**文件：** `D:\agent_python\知识到产品\EndlessCode-backup-20260819-115642\repo-all-refs.bundle`  
**依赖：** T1

**步骤：**
1. 使用 `git bundle verify` 校验 bundle。
2. 在临时目录初始化空仓库并从 bundle 导入 refs。
3. 确认导入仓库包含旧 `main`、`dev`、`test` 和远程跟踪 refs。

**验证：** `git bundle verify` 成功，并且临时仓库中的 `refs/heads/main` 等 SHA 与 T1 记录一致。

## T3: 采集原始提交证据

**文件：** 临时 `evidence.json`  
**依赖：** T1、T2

**步骤：**
1. 从旧 `main` 和 `dev` 历史读取提交 SHA、作者、作者时间、提交者、父提交、标题、正文和文件统计。
2. 标出 PR merge commit、同步 merge commit 与实际内容提交。
3. 保存 PR #1-#4 的标题、合并时间、内部提交 SHA 和文件列表快照。
4. 为每个候选里程碑写入 `category`、`evidence`、`source_commits` 和重新分布的日期。

**验证：** `evidence.json` 覆盖从 `cd9aaef` 到 `7c1c2b6` 的所有非 merge 内容提交，并且每个来源 SHA 可由 `git cat-file -t` 验证。

## T4: 建立里程碑映射

**文件：** 临时 `milestones.json`、`mapping.md`  
**依赖：** T3

**步骤：**
1. 按 `plan.md` 的 M01-M22 顺序创建里程碑记录。
2. 为每项确定单一主题、pathspec、原始作者和日期计划中的作者/提交者时间。
3. 对跨多个功能的文件，检查差异是否能按原始提交顺序安全应用。
4. 对无法安全切开的文件群标记为同一里程碑，不为增加提交数量强拆。
5. 在 `mapping.md` 中记录每个新提交的预期标题、来源 SHA、PR、文件范围和验证命令。

**验证：** 里程碑编号连续；每个来源内容提交恰好映射一次；merge commit 只作为来源说明，不作为新里程碑。

## T5: 创建临时重建引用

**文件：** `refs/history-rewrite/main`  
**依赖：** T4

**步骤：**
1. 在不切换工作区的情况下创建临时 namespace。
2. 以旧 `main` 的根树为起点，创建重建游标。
3. 确认 `refs/heads/main`、`dev`、`test` 未被移动。

**验证：** `git show-ref refs/history-rewrite/main refs/heads/main refs/heads/dev refs/heads/test` 显示临时 ref 已创建且正式分支 SHA 未变化。

## T6: 重建基础与工具系统里程碑

**文件：** 源码、工具模块、README、设计文档、测试  
**依赖：** T5

**步骤：**
1. 依次构建 M01-M03，保留对话基线、工具系统实现和工具系统文档边界。
2. 每次提交使用日期计划中的作者时间和提交者时间，并写入 `Source:` 正文。
3. 每次提交后运行局部 `pytest` 和 `git diff --check`。

**验证：** M01-M03 的 `git show --stat` 只包含映射表允许的文件，临时 ref 连续前进且无 merge parent。

## T7: 重建 Agent Loop 里程碑

**文件：** Agent、Provider、工具、TUI、测试和相关文档  
**依赖：** T6

**步骤：**
1. 依次构建 M04-M12，按基础、修复、usage、取消、TUI、文档、验证和定位拆开。
2. 保持原始提交顺序，但使用批准的 2026-03-01 至 2026-08-19 日期分布。
3. 每个阶段记录来源提交和 PR #1。

**验证：** 每个里程碑后运行与其文件范围对应的测试；M11 完成后运行全量测试并记录结果。

## T8: 重建 Provider、权限、MCP 与 TUI 修复里程碑

**文件：** Provider、prompt、permission、MCP、TUI、示例和测试  
**依赖：** T7

**步骤：**
1. 依次构建 M13-M18。
2. 将 Provider/系统提示、中文说明、权限系统、无效截图清理、MCP 客户端和退格修复分别提交。
3. 保留 PR #2 的来源说明以及对应测试文件边界。

**验证：** MCP 相关测试、权限测试和 TUI 回归测试在对应提交后通过；删除截图的提交只改变该二进制文件。

## T9: 重建长会话、记忆与上下文工程里程碑

**文件：** compact、memory、session、agent、README、配置和测试  
**依赖：** T8

**步骤：**
1. 依次构建 M19-M21。
2. 将长会话上下文、跨会话记忆恢复和默认上下文窗口调整分成独立主题。
3. 对同步 merge commit 只在 `mapping.md` 中记录，不复制为新提交。

**验证：** 对应压缩、会话、记忆和配置测试通过；临时 ref 的最终树与旧 `main` 在 M21 前的内容一致。

## T10: 重建 CI 里程碑

**文件：** `.github/workflows/ci.yml`  
**依赖：** T9

**步骤：**
1. 构建 M22，提交信息标明 CI 与验证矩阵。
2. 将 CI 提交设置为 2026-08-19，并保留来源说明。

**验证：** `git show --stat refs/history-rewrite/main` 显示 CI 文件变更，工作区源码无额外变化。

## T11: 验证临时历史

**文件：** 临时验证报告  
**依赖：** T10

**步骤：**
1. 验证新历史为线性：每个新提交只有一个 parent，`git log --merges` 为空。
2. 比较旧 `main` 与临时 ref 的最终树和所有文件内容哈希。
3. 比较 `dev`、`test`、`origin/*` 的 SHA 是否未变。
4. 比较 `.coverage`、`closure_demo.py` 的 SHA-256 和未跟踪状态。
5. 运行 `pytest`、`ruff check`、`ruff format --check`、`compileall` 和 `git diff --check`。

**验证：** 所有检查通过；任一内容或引用不一致都不得进入 T12。

## T12: 原子移动本地 main

**文件：** 仅本地 `refs/heads/main`  
**依赖：** T11

**步骤：**
1. 保存临时 ref 和最终验证报告。
2. 使用 fast-forward/force-update 仅移动本地 `main` 到 `refs/history-rewrite/main`。
3. 删除临时 ref（保留备份 bundle 和映射文件）。
4. 不执行任何 `git push`。

**验证：** `git rev-parse main` 等于重建 ref 的最终 SHA；`git status --short --branch` 仍显示已知未跟踪文件；`git remote -v` 无变化。

## T14: 重新分布提交日期

**文件：** 临时 `date-plan.json`、临时历史 ref  
**依赖：** T13、日期策略审批

**步骤：**
1. 为 33 个提交建立单调递增的北京时间日期表，范围为 2026-03-01 至 2026-08-19。
2. 保持原提交顺序和阶段边界；阶段之间使用 1-10 天间隔，阶段内部使用更短间隔。
3. 将最后一个 CI 提交的 author date 与 committer date 设置为 2026-08-19。
4. 在临时 ref 中重建提交对象，不修改文件树、提交正文或来源映射。

**验证：** `git log --reverse --date=iso` 中所有日期处于批准范围、单调递增，最后提交日期为 2026-08-19。

## T13: 生成验收报告

**文件：** `docs/history-rewrite/mapping.md`、临时验收报告  
**依赖：** T12

**步骤：**
1. 将实际新 SHA 回填到 `mapping.md`。
2. 按 `checklist.md` 逐项记录命令、结果和证据路径。
3. 记录通过项、失败项及修复后的重跑结果。

**验证：** 验收报告覆盖 AC1-AC8，并明确说明远程未被修改。

## 执行顺序

```text
T1 -> T2 -> T3 -> T4 -> T5 -> T6 -> T7 -> T8 -> T9 -> T10 -> T11 -> T12 -> T13 -> T14
```
