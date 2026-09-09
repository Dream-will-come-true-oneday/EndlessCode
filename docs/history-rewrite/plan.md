# EndlessCode 历史时间线整理 Plan

## 架构概览

本次工作采用“只读证据采集 -> 临时线性重建 -> 内容与拓扑验收 -> 原子替换本地 `main`”的流程。

1. 以备份中的 `main` 哈希 `7c1c2b6e89f2ca515fd6cb9ef102256f1c0dd1d7` 作为输入基线，不从 `origin/main` 拉取新提交。
2. 读取当前主线及其已合入分支的原始提交，建立旧提交到新里程碑的映射。
3. 在临时 namespace 或临时分支中，用 Git 原生提交对象重建线性历史；每个新提交只承载一个证据充分的阶段。
4. 为每个新提交保留对应阶段的作者和提交者身份；作者时间与提交者时间按批准的日期分布重新生成，并在正文记录来源提交和 PR。
5. 对临时历史执行树内容、文件哈希、分支保护、未跟踪文件和工程检查。
6. 验收通过后，仅移动本地 `main` 到新线性历史；远程引用、`dev`、`test` 和工作区不变。

## 核心数据结构与接口

### HistoryEvidence

```text
source_commit: 原始提交 SHA
source_date: 原始作者时间（仅作证据记录）
source_author: 原始作者身份
source_pr: 可选的 GitHub PR 编号
files: 该阶段涉及的文件集合
category: feature | fix | test | docs | example | ci
evidence: PR 描述、原始提交标题、文件差异或测试记录
```

### Milestone

```text
id: M01, M02, ...
title: 新提交标题
category: feature | fix | test | docs | example | ci
date: 重新分布后的作者时间（北京时间，2026-03-01 至 2026-08-19）
author: 采用的作者身份
parents: 前一里程碑的临时提交
source_commits: 一个或多个 HistoryEvidence.source_commit
pathspec: 允许写入该提交的文件路径集合
validation: 该阶段完成后的可执行验证命令
```

### 重建接口

```text
collect_evidence() -> evidence.json
build_milestone_plan() -> milestones.json
rebuild_linear_history(milestones.json) -> refs/history-rewrite/main
verify_rewrite(evidence.json, milestones.json) -> verification report
```

这些接口可以由一次性 PowerShell/Git 脚本实现；脚本只服务本次整理，不作为产品运行时代码。

## 模块设计

### 证据采集模块

- 职责：锁定旧 `main`、记录每个原始提交的 SHA、作者、作者时间、父提交、标题和文件统计。
- 来源：本地 Git 对象、已存在的 `dev` 分支历史、PR 元数据快照和备份目录。
- 约束：不读取或合并 `origin/main` 在 `7c1c2b6` 之后的提交。

### 里程碑映射模块

建议的线性里程碑顺序如下；具体 pathspec 和来源 SHA 在执行前写入 `milestones.json` 并逐项核对：

| ID | 日期依据 | 类型 | 主题 | 主要证据 |
|---|---|---|---|---|
| M01 | 2026-07-26 | feature | 建立对话模块基线 | `cd9aaef` |
| M02 | 2026-07-27 | feature | 建立工具抽象与核心工具 | `fbc30fd` |
| M03 | 2026-07-27 | docs | 补齐工具系统设计文档 | `5cf0367` |
| M04 | 2026-07-30 | docs | 对齐 Agent Loop 规格 | `38db3b6` |
| M05 | 2026-07-30 | feature | Agent Loop 基础与输出脱敏 | `d87f1f2` |
| M06 | 2026-07-30 | fix | 工具分类与 Bash 进程清理 | `9bad816` |
| M07 | 2026-07-30 | feature | OpenAI 兼容流 usage 上报 | `b6cd7bc` |
| M08 | 2026-07-30 | feature | 可取消多轮 ReAct Agent Loop | `dfdba97` |
| M09 | 2026-07-30 | feature | TUI 接入循环模式与取消 | `f1dbfea` |
| M10 | 2026-07-30 | docs | 记录 Agent Loop 控制方式 | `ccd3384` |
| M11 | 2026-07-30 | test | 完成 Agent Loop 验证 | `afa0a5d` |
| M12 | 2026-07-31 | docs | 明确终端编程代理定位 | `668cac5` |
| M13 | 2026-08-04 | feature | Provider 适配与系统提示模块化 | `1a9f5f1` |
| M14 | 2026-08-04 | docs/example | 完善中文说明与 smoke 示例 | `a5025ac` |
| M15 | 2026-08-05 | feature | 五层权限系统 | `5006648` |
| M16 | 2026-08-05 | fix | 清理无效示例截图 | `53481f6` |
| M17 | 2026-08-05 | feature | MCP 客户端与远端工具生态 | `aa2ae1c` |
| M18 | 2026-08-05 | fix/test | 修复 TUI 退格重复分派并补回归测试 | `cf4439e` |
| M19 | 2026-08-05 | feature/docs | 长会话上下文管理 | `5738ccd` |
| M20 | 2026-08-05 | feature | 跨会话记忆与恢复 | `4ff1e41` |
| M21 | 2026-08-14 | feature | 默认上下文窗口与压缩阈值调整 | `d1f9a8e` |
| M22 | 2026-08-14 | ci | GitHub Actions CI | `7c1c2b6` |

原 PR merge commit、同步 merge commit 和重复的合并节点不单独重建；其可读信息通过新提交正文中的 `Source:` 字段保留。

### 线性历史构建模块

- 以旧根提交的最终树作为起点。
- 对每个里程碑，使用原始提交之间的差异或精确 pathspec 应用变更。
- 每次提交后立即验证 `git diff --check` 和该里程碑的局部测试。
- 提交对象写入临时引用 `refs/history-rewrite/main`，不触碰 `refs/heads/main`。
- 若某个原始大提交跨越多个里程碑，按原始分支提交顺序和文件差异切分；无法安全切分的文件群保持在同一里程碑，禁止为了数量强行拆分。

### 验收与回滚模块

- 将旧 `main` 的最终树与新引用的最终树做递归哈希比较。
- 对 `dev`、`test`、`origin/*` 记录前后 SHA，任何非预期变化即停止。
- 比较 `.coverage` 与 `closure_demo.py` 的 SHA-256，并确认仍为未跟踪。
- 通过备份 bundle 恢复旧 `main` 的演练只在临时 clone 中进行，不覆盖工作区。

## 模块交互

```text
旧 main / dev 历史 + PR 元数据 + 备份状态
        |
        v
  evidence.json
        |
        v
  milestones.json + mapping.md
        |
        v
refs/history-rewrite/main (临时线性历史)
        |
        +--> 内容/拓扑/分支/工作区/工程验证
        |
        v
本地 refs/heads/main 原子移动
```

## 文件组织

```text
docs/history-rewrite/
├── spec.md                 # 已批准的行为范围和验收标准
├── plan.md                 # 本文，技术设计
├── task.md                 # 待批准的执行任务
├── checklist.md            # 待批准的验收检查
├── evidence.json           # 执行阶段生成的原始证据快照
├── milestones.json         # 执行阶段生成的里程碑映射
└── mapping.md              # 最终提交到来源提交的可读映射
```

一次性脚本可放在备份目录或临时目录中执行，不纳入仓库提交，避免把历史改写工具混入产品代码。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 改写对象 | 当前本地 `main` 到 `7c1c2b6` | 用户已批准只整理本地 `main`，避免把远程新提交混入事实时间线 |
| 构建方式 | Git 原生临时 ref + 原子 ref 更新 | 不依赖额外历史改写工具，且能在替换前完整验收 |
| 主线形态 | 线性历史 | 阅读和后续小里程碑维护最清楚 |
| 时间策略 | 在 2026-03-01 至 2026-08-19 内按阶段重新分布，最后提交为 2026-08-19 | 拉长时间线但保持提交顺序和阶段边界 |
| 提交边界 | 功能、修复、测试、文档、示例、CI | 与用户希望的后续里程碑类型一致 |
| 合并节点 | 不重建 PR merge commit | 合并节点不代表独立产品内容，来源信息写入新提交正文 |
| 未跟踪文件 | 保持未跟踪 | `.coverage` 与 `closure_demo.py` 不属于历史整理范围 |
| 远程仓库 | 不 push | 用户只批准本地改写，避免破坏 GitHub 现有历史 |
