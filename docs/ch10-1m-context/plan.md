# 1M 上下文窗口适配 Plan

> 历史方案：本文档已被 `docs/ch11-context-window-config` 取代。当前默认窗口为 200K，1M 需要显式配置。

## 架构概览

本次改动是纯"默认值与阈值常量"变更，不新增组件、不改接口、不动压缩流程架构。所有消费方都从两个来源读取参数：

- 默认上下文窗口：`config.py` 的协议默认常量、`agent/__init__.py` 的运行时默认值；
- 压缩阈值：`compact/const.py` 的 7 个常量。

因此只需修改上述 3 个文件 + 测试/文档，agent 主循环、`manage_context`、TUI 恢复判断、layer1/layer2/recovery 的调用链自动获得新参数。

## 核心数据结构与接口

无新增数据结构、无接口签名变化。

- `ProviderConfig.context_window`：语义不变（`0` = 未配置走默认，`> 0` = 显式优先）。
- `SessionRuntime.context_window` / `new_session_runtime(context_window=...)`：默认值变更。
- `compact.const` 各阈值常量：值变更，名称与消费方不变。

## 模块设计

### config.py（修改）

- `DEFAULT_ANTHROPIC_CONTEXT_WINDOW`：`200_000` → `1_000_000`。
- `DEFAULT_OPENAI_CONTEXT_WINDOW`：`128_000` → `1_000_000`（deepseek 协议复用该常量，同步生效）。
- `effective_context_window` 逻辑不变：显式值优先，未配置时返回协议默认。

### agent/__init__.py（修改）

- `SessionRuntime.context_window` 默认值：`128_000` → `1_000_000`。
- `new_session_runtime(workspace, context_window=...)` 参数默认值：`128_000` → `1_000_000`。

### compact/const.py（修改）

| 常量 | 旧值 | 新值（×5） |
|---|---|---|
| `SINGLE_RESULT_LIMIT` | 50_000 | 250_000 |
| `MESSAGE_AGGREGATE_LIMIT` | 200_000 | 1_000_000 |
| `SUMMARY_RESERVE` | 20_000 | 100_000 |
| `AUTO_SAFETY_MARGIN` | 13_000 | 65_000 |
| `MANUAL_SAFETY_MARGIN` | 3_000 | 15_000 |
| `RECENT_KEEP_TOKENS` | 10_000 | 50_000 |
| `RECOVERY_TOKENS_PER_FILE` | 5_000 | 25_000 |

保持不变：`RECOVERY_FILE_LIMIT=5`、`RECENT_KEEP_MESSAGES=5`、`MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES=3`、`PTL_RETRY_LIMIT=3`、`PTL_DROP_PERCENTAGE=0.2`、`ESTIMATE_CHARS_PER_TOKEN=3.5`、`PREVIEW_HEAD_BYTES=2048`、`PREVIEW_HEAD_LINES=20`。

### 消费方（无代码改动，行为自动变化）

- `compact/compact.py`：小窗口守卫变为 `context_window <= 165_000` 时跳过自动压缩；自动触发点变为 `context_window - 165_000`（1M 下 835,000）。
- `agent/__init__.py` 主循环：`auto_threshold` 自动适配；紧急压缩后重试安全线变为 `context_window - 115_000`（1M 下 885,000）。
- `tui/app.py` 恢复判断：阈值公式引用同一组常量，自动适配。
- `compact/layer1.py`、`compact/layer2.py`、`compact/recovery.py`：落盘线、摘要保留量、恢复附件大小自动适配。

## 模块交互

```text
启动/运行：config.load → effective_context_window（未配置 → 1_000_000）
        → new_session_runtime(1_000_000) → agent 主循环
        → estimate_tokens ≥ context_window - 165_000 → manage_context(AUTO)
        → 先 layer1 落盘 → 仍超限 → layer2 摘要（保留最近 50k token）
        → provider 报超限 → manage_context(EMERGENCY) → 重试前校验 ≥ 窗口-115_000

恢复：load_session → estimate_tokens(0, messages, 0) ≥ 窗口-165_000
     → run_force_compact → 进入空闲态
```

## 文件组织

```text
src/endless_code/config.py              # 修改：默认窗口常量 → 1_000_000
src/endless_code/agent/__init__.py      # 修改：运行时默认窗口 → 1_000_000
src/endless_code/compact/const.py       # 修改：7 个阈值常量 ×5
tests/test_config.py                    # 修改：默认窗口断言 → 1_000_000
tests/test_compact.py                   # 修改：落盘/聚合用例适配新阈值
tests/test_agent.py                     # 检查：显式 200_000 窗口用例无需变更
README.md                               # 修改：配置示例与默认窗口注释 → 1M
.endless-code/config.yaml.example       # 修改：示例值与注释 → 1M
```

> 按用户指示：本变更只生成 `spec.md` 与 `plan.md`，不再生成 `task.md` / `checklist.md`；根目录原有的 `task.md`、`checklist.md` 已删除。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| deepseek 默认窗口 | 沿用 `DEFAULT_OPENAI_CONTEXT_WINDOW`，值改为 1M | 现有分支零改动，三种协议默认一致 |
| 阈值放大方式 | 直接改 `const.py` 常量值（×5 取整） | 消费方全部引用常量，单点修改全链生效 |
| 行为测试窗口 | 压缩行为用例保留显式 200_000 窗口；新增 1M 默认值与阈值数学断言 | 避免为 835k 触发构造数 MB 级 fixture |
| 文档更新 | README 与 `config.yaml.example` 全部按 1M 口径 | 满足 spec N4 |
| 本地 `config.yaml` | 不改 | 未配置 `context_window` 即自动走新默认 1M |
| 小窗口守卫 | 随 `SUMMARY_RESERVE + AUTO_SAFETY_MARGIN` 变为 165_000 | 机制不变，绝对值随放大变化（已纳入 spec） |
| 四份文档流程 | 仅生成 spec.md 与 plan.md（用户指示） | 本变更按用户要求跳过 task/checklist |
