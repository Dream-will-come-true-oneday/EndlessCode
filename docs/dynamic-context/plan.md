# 动态上下文压缩预算 Plan

## 架构概览

```text
config.effective_context_window(provider)
        ↓ context_window（显式配置优先，否则协议默认 1M）
compact/budget.py   build_context_budget(window, tool_schema_tokens) -> ContextBudget
        ↓ 单一事实来源（frozen dataclass，纯函数推导）
   ┌──────┴──────┬────────────┬─────────────┬────────────────┐
layer1 落盘线   layer2 保留量  recovery 附件   agent 触发判定    tui 恢复判定
        ↓
compact/token.py  TokenMeter（EWMA 自校准 chars/token，仅用于回合内估算）
compact/compact.py  质量校验门（估算量 + 工具结果配对完整性 + 摘要非空）
```

组件职责：

- `budget.py`：把窗口换算成 8 项限制值与 3 条派火线，不做任何 I/O。
- `compact.py`：编排层，持有预算并执行触发判定与质量校验。
- `layer1.py` / `layer2.py` / `recovery.py`：从预算读取自己需要的量纲，不再直接读常量。
- `agent/__init__.py`：每回合开始重建预算（窗口或工具集变化时自然生效），持有 `TokenMeter`。
- `tui/app.py`：窗口设置与模型切换时重建预算，状态栏与状态命令展示派火线。

## 核心数据结构与接口

### ContextBudget（`src/endless_code/compact/budget.py`）

```python
@dataclass(frozen=True)
class ContextBudget:
    context_window: int              # 配置或协议默认窗口
    tool_schema_tokens: int          # 工具定义折算 token，纯派生值
    output_reserve_tokens: int       # 本轮输出预留
    single_result_bytes: int
    message_aggregate_bytes: int
    summary_reserve_tokens: int
    auto_safety_margin_tokens: int
    manual_safety_margin_tokens: int
    recent_keep_tokens: int
    recovery_tokens_per_file: int

    @property
    def usable_window(self) -> int:            # max(1, window - output_reserve - tool_schema)
    @property
    def auto_compact_threshold(self) -> int:   # usable - summary_reserve - auto_margin
    @property
    def emergency_retry_threshold(self) -> int:  # usable - summary_reserve - manual_margin
    @property
    def degraded(self) -> bool:                # auto_compact_threshold <= 0
    @property
    def effective_auto_threshold(self) -> int:  # degraded 时退化为 usable // 2（下限 1）
    @property
    def compact_target(self) -> int:            # int(usable * 0.6)，质量校验目标线

def build_context_budget(context_window: int, tool_schema_tokens: int = 0) -> ContextBudget: ...
def estimate_tool_schema_tokens(tool_defs: list[ToolDefinition]) -> int: ...
```

`build_context_budget` 对 `context_window` 要求正整数（拒绝 `bool`），否则 `ValueError`；
`tool_schema_tokens` 负数按 0 处理。

### 比例表（窗口 × ratio，再夹逼到 [floor, ceiling]）

| 项 | ratio | floor | ceiling | 1M | 200k | 128k |
|---|---|---|---|---|---|---|
| single_result_bytes | 0.250 | 8_000 | 250_000 | 250_000 | 50_000 | 32_000 |
| message_aggregate_bytes | 1.000 | 32_000 | 1_000_000 | 1_000_000 | 200_000 | 128_000 |
| summary_reserve_tokens | 0.100 | 4_000 | 100_000 | 100_000 | 20_000 | 12_800 |
| auto_safety_margin_tokens | 0.065 | 2_000 | 65_000 | 65_000 | 13_000 | 8_320 |
| manual_safety_margin_tokens | 0.015 | 1_000 | 15_000 | 15_000 | 3_000 | 1_920 |
| recent_keep_tokens | 0.050 | 4_000 | 50_000 | 50_000 | 10_000 | 6_400 |
| recovery_tokens_per_file | 0.025 | 2_000 | 25_000 | 25_000 | 5_000 | 3_200 |
| output_reserve_tokens | 0.040 | 4_096 | 40_000 | 40_000 | 8_000 | 5_120 |

设计约束：ceiling 等于改动前 `const.py` 的 1M 校准常量，因此 1M 窗口下 7 项既有量纲逐值
不变（AC2）；200k 与 128k 两档与 `archive/pre-rewrite-test` 的 `tests/test_limits.py` 断言
完全一致，等于把该方案纳入统一比例表；字节类上限不设中间封顶，1M 窗口保持 250KB/1MB。

派火线示例（`tool_schema_tokens=0`）：1M → 1_000_000−40_000−100_000−65_000 = 795_000；
200k → 200_000−8_000−20_000−13_000 = 159_000；128k → 128_000−5_120−12_800−8_320 = 101_760；
32k → 32_000−4_096−4_000−2_080 = 21_824；窗口 1_000 → usable=1、degraded、
effective_auto_threshold=1（仍然压缩）。

### TokenMeter（`src/endless_code/compact/token.py`）

```python
class TokenMeter:
    def __init__(self, chars_per_token: float = ESTIMATE_CHARS_PER_TOKEN) -> None
    def observe(self, added_bytes: int, added_tokens: int) -> None
    def reset(self) -> None
    @property
    def chars_per_token(self) -> float
    def estimate(self, anchor: int, messages: list[Message], anchor_msg_len: int) -> int

def estimate_tokens(anchor: int, messages: list[Message], anchor_msg_len: int,
                    chars_per_token: float = ESTIMATE_CHARS_PER_TOKEN) -> int
```

- `observe` 门限：`added_bytes >= 500` 且 `added_tokens >= 200`，且观测比例落在
  `[1.0, 12.0]` 才接受；EWMA `α = 0.25`，结果夹逼到 `[1.5, 8.0]`。
- `estimate_tokens` 新增可选比例参数，默认值即原常量，未迁移的调用点行为不变。
- 仅回合内估算（agent 的 `estimated`、紧急重试估算）走 meter；会话恢复、近期原文逐条估算、
  恢复附件字节换算继续用默认比例，保证离线判定可复现。
- `usage_anchor` 的求和口径（input + output + cache_read + cache_write）本次不改，只加注释
  说明该口径等于「截至锚点的完整历史 token 量」，待实测两个协议后再评估是否拆分输出项。

## 模块设计

### compact/const.py（收敛）

保留：`RECENT_KEEP_MESSAGES`、`RECOVERY_FILE_LIMIT`、`MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES`、
`PTL_RETRY_LIMIT`、`PTL_DROP_PERCENTAGE`、`ESTIMATE_CHARS_PER_TOKEN`、`PREVIEW_HEAD_BYTES`、
`PREVIEW_HEAD_LINES`。删除被预算取代的 7 个绝对量：`SINGLE_RESULT_LIMIT`、
`MESSAGE_AGGREGATE_LIMIT`、`SUMMARY_RESERVE`、`AUTO_SAFETY_MARGIN`、`MANUAL_SAFETY_MARGIN`、
`RECENT_KEEP_TOKENS`、`RECOVERY_TOKENS_PER_FILE`（其数值迁移进 `budget.py` 的 ceiling，避免
两处真值）。

### compact/compact.py

- `ManageInput` 新增必填字段 `budget: ContextBudget`（紧跟 `context_window`），由调用方构建。
- 触发判定删除 `context_window <= SUMMARY_RESERVE + AUTO_SAFETY_MARGIN` 早退分支，改为
  `budget.effective_auto_threshold`；`degraded` 为真时额外记一条 warning 但不早退。
- 新增质量校验：`_quality_problem(messages, after, budget)`，返回空串表示达标，条件为
  历史非空、`after <= budget.compact_target`（`budget.degraded` 时跳过此条：usable 已贴地，
  驻留量目标不可能达成）、历史中不存在孤儿工具结果（工具结果 id 无对应的先前调用）。
- 摘要正文为空或过短的校验由 `layer2.auto_compact` 抛出 `CompactionQualityError`（该类定义在
  `layer2.py`，由 `compact/__init__.py` 导出，避开层间循环导入）。
- 不达标时：以 `recent_keep_tokens // 2` 与 `min_messages=1` 同时收紧尾部保留量重试一次
  （只收 token 不放开条数下限时，五条保底本身就可能超目标）；再失败则
  `auto_tracking.record_failure()` 并返回带 `err=CompactionQualityError` 的 `ManageOutput`，
  此时不替换历史。
- `MANUAL` 与 `EMERGENCY` 触发的强制压缩路径不做质量校验（语义就是无条件压缩），保持现状。

### compact/layer1.py / layer2.py / recovery.py

- `offload_and_snip(messages, state, session, budget)`：落盘判定改用
  `budget.single_result_bytes` 与 `budget.message_aggregate_bytes`。
- `pick_recent_tail(messages, budget, max_tokens=None, min_messages=None)`：保留量取
  `max_tokens or budget.recent_keep_tokens`，最少条数取 `min_messages or RECENT_KEEP_MESSAGES`。
- `run_summary` 额外返回摘要正文，`auto_compact` 校验其长度不小于 `MIN_SUMMARY_CHARS`（拦住
  空标记碎片），`force_compact` 不做该校验。
- `build_recovery_attachment(snapshot, tool_defs, budget)`、
  `render_file_block(record, budget)`：单文件上限与字节换算改用
  `budget.recovery_tokens_per_file`。

### agent/\_\_init\_\_.py

- `SessionRuntime` 新增 `budget: ContextBudget`、`meter: TokenMeter` 字段；
  `new_session_runtime(workspace, context_window=1_000_000)` 内构建默认预算。
- 新增 `SessionRuntime.refresh_budget(tool_defs) -> ContextBudget`：按当前
  `context_window` 与工具开销重算并写回，回合开始与强制压缩前各调用一次。
- 三处 `context_window - SUMMARY_RESERVE - AUTO_SAFETY_MARGIN` /
  `... - MANUAL_SAFETY_MARGIN` 改为 `budget.effective_auto_threshold` /
  `budget.emergency_retry_threshold`。
- `estimate_tokens(...)` 调用改为 `self._runtime.meter.estimate(...)`；
  `_record_usage_anchor` 内先算锚点后内容字节量与 token 增量，调用 `meter.observe`，再写锚点。
- 历史被压缩替换后（自动与紧急路径）调用 `meter.reset()` 并清零锚点。
- 构造 `ManageInput` 时传入 `self._runtime.budget`。

### tui/app.py 与 command 层

- `_activate_provider` 与 `_command_switch_model` 在设置 `context_window` 后调用
  `refresh_budget([])`（工具集由 agent 回合内刷新，这里只需按新窗口重算），并清零锚点。
- 会话恢复判定改用 `self._runtime.budget.effective_auto_threshold`。
- `get_session_info()` 填充 `usable_window`、`auto_compact_threshold`、`degraded`；
  `SessionInfo` 新增这三个带默认值的字段。
- `status_command` 增行：`可用窗口：`、`自动压缩阈值：`，degraded 时追加降级说明。
- `_update_status` 在 degraded 时于窗口展示后追加 `降级` 标记。

## 模块交互

```text
回合开始
  agent: estimated = runtime.meter.estimate(anchor, messages, anchor_len)
  agent: budget = runtime.refresh_budget(definitions)
  agent: likely_auto = estimated >= budget.effective_auto_threshold and not breaker.tripped()
         → 发 BEFORE_AUTO 事件
  agent → compact.manage_context(ManageInput(..., budget, ...))
            layer1.offload_and_snip(..., budget)      # 落盘，读 single/aggregate
            估算 after_layer1
            after_layer1 < budget.effective_auto_threshold → 返回（含 degraded 也走同一比较）
            layer2.auto_compact(input_)
              recovery.build_recovery_attachment(..., budget)
              layer2.pick_recent_tail(..., budget)
            质量校验（compact_target / 摘要非空 / 无孤儿工具结果）
              不达标 → recent_keep 减半重试一次 → 仍不达标 → record_failure + err
  agent → AFTER_AUTO 事件；meter.reset()
请求被拒（PromptTooLongError）
  agent → manage_context(EMERGENCY) → force_compact
  agent → retry_estimate >= budget.emergency_retry_threshold → 报超限错误并中断
```

## 文件组织

```text
docs/dynamic-context/
├── spec.md        # 需求与验收标准
├── plan.md        # 本文档
├── task.md        # 实施顺序
└── checklist.md   # 验收清单
src/endless_code/
├── agent/__init__.py        # 预算接线、TokenMeter 观测与重置
├── command/builtin.py       # /status 增行
├── command/types.py         # SessionInfo 增 3 个带默认值字段
├── compact/budget.py        # 新建：比例表与 ContextBudget
├── compact/compact.py       # ManageInput 增字段、触发判定、质量校验
├── compact/const.py         # 删除被取代的 7 个绝对量
├── compact/layer1.py        # 落盘线改读预算
├── compact/layer2.py        # 保留量改读预算，支持收紧重试
├── compact/recovery.py      # 附件上限改读预算
├── compact/token.py         # TokenMeter 与可选比例参数
├── compact/__init__.py      # 导出预算与计量符号
└── tui/app.py               # 窗口变更重建预算、恢复判定、状态栏
tests/
├── test_budget.py           # 新建：五档窗口、上下限、派火线、非法入参
├── test_compact.py          # 传入预算、小窗口触发、质量校验重试
├── test_golden.py           # offload_and_snip 增预算参数
├── test_agent.py            # 预算接线与降级窗口
├── test_command_builtin.py  # SessionInfo 新字段展示
├── test_token_meter.py      # 新建：TokenMeter 校准行为
└── test_tui.py              # 切换模型后阈值随之变化
README.md                    # 阈值随窗口缩放说明与两档对照表
.endless-code/config.yaml.example  # 窗口字段注释更新
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 阈值来源 | 窗口 × 比例 + 上下限夹逼 | 单一比例表同时复现 1M 现值与 200k/128k 基线，零回归且不静默失效 |
| 模块与类型命名 | `budget.py` / `ContextBudget` | 语义含输出预留与工具开销，比 archive 的 `limits` 更准确，避免与旧实现混淆 |
| 是否沿用 archive 的工具字节 400k 封顶 | 不沿用 | 封顶会让 1M 窗口降到 100KB/400KB，违反 AC2 的零回归要求 |
| 预算构建位置 | 调用方构建并作为 `ManageInput` 必填字段 | 压缩层不做推导，避免每轮重复估算工具开销与公式分叉（F6） |
| 极小窗口处理 | 降级模式，`usable // 2` 作触发线 | 现在只写日志然后不压缩属于静默失效；降级仍保住不落爆窗口 |
| 质量校验不达标的手段 | 收紧近期原文保留量后重试一次 | 残余大头通常在被保留的近期原文里，重摘全量历史不会有不同结果 |
| 工具开销的比例 | 用默认比例折算，不走 meter | 保持 `ContextBudget` 纯函数与不可变（N2），避免估算值随会话历史漂移 |
| `usage_anchor` 是否剔除输出 token | 本次不改 | 现口径等于「完整历史 token 量」，改前需实测两个协议字段语义，避免臆断 |
| meter 覆盖范围 | 仅回合内估算 | 会话恢复与逐条估算保持默认比例，离线判定可复现 |
| 窗口自校准（拒答反推） | 推到后续子项目 | 需要新的运行时状态与提示链路，超出本轮范围 |
