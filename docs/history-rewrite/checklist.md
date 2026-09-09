# EndlessCode 历史时间线整理 Checklist

## 功能与集成

- [ ] `main` 最终文件树与改写前 `main` 一致（验证：对旧 `main` 与新 `main` 运行 `git ls-tree -r`，并逐文件比较 blob SHA）。
- [ ] 新 `main` 为连续线性历史（验证：运行 `git log --merges main` 为空；遍历 `git rev-list main` 确认每个提交只有一个父提交）。
- [ ] 原 PR merge commit 和同步 merge commit未被重复重建（验证：检查 `mapping.md`，每个新提交只列内容来源提交，merge 仅出现在来源说明）。
- [ ] 每个新提交都有可追溯来源（验证：逐项查看 `mapping.md`、`git show --stat` 和 `git show` 正文中的 `Source:` 字段）。
- [ ] 历史包含功能里程碑（验证：检索 `feat:` 提交，并核对工具、Agent Loop、Provider、权限、MCP、长会话和记忆文件变更）。
- [ ] 历史包含修复里程碑（验证：检索 `fix:` 提交，并核对 Bash/工具进程、TUI 退格等实际差异）。
- [ ] 历史包含测试里程碑（验证：检索 `test:` 或验证类提交，并核对对应 `tests/` 文件）。
- [ ] 历史包含文档、示例和 CI/发布里程碑（验证：检索 `docs:`、`example`、`ci:`，核对 README、示例、设计文档和 `.github/workflows/ci.yml`）。
- [ ] 日期重排符合批准范围（验证：运行 `git log --reverse --date=iso --format='%h %ad %cd %s'`，确认全部时间在 2026-03-01 至 2026-08-19、单调递增，最后提交为 2026-08-19）。

## 分支与工作区保护

- [ ] `dev` 指向未改写前的 `4ff1e415...`（验证：与 T1 冻结值和备份 `branches.txt` 比较）。
- [ ] `test` 指向未改写前的 `c8d32efd...`（验证：与 T1 冻结值和备份 `branches.txt` 比较）。
- [ ] `origin/main`、`origin/dev`、`origin/test` 未被修改（验证：比较远程跟踪 ref 的前后 SHA，且不执行 push）。
- [ ] `.coverage` 和 `closure_demo.py` 仍未跟踪且内容不变（验证：运行 `git status --short` 和 `Get-FileHash`）。
- [ ] 历史整理文档未意外进入产品历史（验证：确认其是否按执行约定保留在工作区；若不纳入重建，最终树比较需排除整理期间新增文档并记录原因）。

## 工程检查

- [ ] 备份 bundle 可验证（验证：运行 `git bundle verify D:\agent_python\知识到产品\EndlessCode-backup-20260819-115642\repo-all-refs.bundle`）。
- [ ] 旧历史可在临时 clone 中恢复（验证：从 bundle 初始化临时仓库并检出备份 `main`）。
- [ ] 全量 Python 测试通过（验证：运行 `python -m pytest -q`，记录通过/跳过/失败数量）。
- [ ] Ruff lint 通过（验证：运行 `python -m ruff check .`）。
- [ ] Ruff 格式检查通过或记录已存在的历史例外（验证：运行 `python -m ruff format --check .`）。
- [ ] Python 编译检查通过（验证：运行 `python -m compileall -q src examples`）。
- [ ] Git 差异检查通过（验证：运行 `git diff --check`）。

## 端到端场景

- [ ] 从备份恢复原始 `main`（验证：临时目录 `git clone`/`git fetch` bundle 后检出旧 `main`，SHA 等于 `7c1c2b6e...`）。
- [ ] 从临时线性 ref 完成验收后移动本地 `main`（验证：`git rev-parse main` 等于新 ref，`git status` 和远程引用保持预期）。
- [ ] 查看整理后的时间线能连续读出开发阶段（验证：运行 `git log --date=short --format='%ad %h %s' main`，按功能、修复、测试、文档、示例、CI 顺序抽查 M01-M22）。
- [ ] 边界输入不会被纳入历史（验证：确认未跟踪文件、远程新增提交和其他本地分支均未进入新 `main`）。

## 验收报告模板

```markdown
## 验收报告

### 通过（N/总数）
- [x] 条目 — 证据：命令与关键输出

### 未通过
- [ ] 条目 — 预期：...；实际：...；修复：...

### 端到端
- [x] 场景 — 结果：...
```
