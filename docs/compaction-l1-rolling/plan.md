# 压缩阶梯 L1 与增量滚动摘要 Plan

## 架构概览

```text
AUTO 触发（估算 ≥ budget.effective_auto_threshold）
  L0 offload_and_snip（现状不动：工具结果落盘 + 预览，ContentReplacementState 冻结）
        ↓ 估算仍 ≥ 触发线？
  L1 trim.apply（新增，纯本地零调用）
        ├─ 重放 TrimLedger 已冻结的指针（字节级幂等，不伤缓存）
        ├─ F1 同路径旧文件读取 → 「已有更新版本」指针（依据 RecoveryState 的读取台账）
        └─ F2 触发线之前已落盘结果的预览正文 → 纯指针（解析 L0 稳定格式）
        ↓ 估算 < 触发线 → 结束（本次零 LLM 调用）
        ↓ 仍 ≥ 触发线
  L2 摘要：SummaryState 有可用上一版 → 滚动增量（只摘 covered 之后的新片段 + 合并旧摘要）
                        否则 → 全量摘要（现状路径，含 PTL 重试）
        ↓ 质量门（驻留量 / 空摘要 / 孤儿工具结果，不动）
  replace_history → 计算 covered 前导数 → SummaryState 更新并持久化到会话目录
```

P3 窗口自校准挂在 agent 回合循环的 PTL 分支：校准先于紧急压缩执行，让紧急压缩用修正后的预算。

## 核心数据结构与接口

### SummaryState（`src/endless_code/compact/rolling.py`，新建）

```python
@dataclass
class SummaryState:
    summary_text: str = ""      # 上一版摘要正文（不含恢复附件）
    covered_messages: int = 0   # 当前历史开头已被摘要覆盖的前导消息数
    revision: int = 0           # 滚动轮次

    def usable_for_rolling(self, history_len: int) -> bool   # 有正文且 0 < covered < len
    def rolling_update(self, summary_text: str, covered: int) -> None
    def save(self, session_dir: str) -> None                 # JSON 原子写（tmp + replace）
    @classmethod
    def load(cls, session_dir: str) -> "SummaryState"        # 缺失/损坏/版本不符 → 全新实例
```

持久化文件 `{session_dir}/summary-state.json`：`{"version": 1, "summary_text": ...,
"covered_messages": ..., "revision": ...}`。损坏回退由 `load` 吞掉全部解析异常实现（N2）。

### TrimLedger（`src/endless_code/compact/state.py`，追加）

```python
class TrimLedger:
    """L1 指针决策账本：冻结每个工具结果的最终指针文本，重放字节级幂等。"""
    def pointer_for(self, tool_use_id: str) -> str | None
    def supersede(self, tool_use_id: str, pointer: str) -> str   # 首次冻结并返回
```

与 `ContentReplacementState` 分开：L0 冻结的是预览，L1 冻结的是指针，两者每轮各自重放、
互不覆盖（否则 L0 会在下轮把预览盖回指针之上）。

### RecoveryState 读取台账（`state.py` 扩展）

```python
    def record_read(self, tool_call_id: str, path: str) -> None  # agent 处理 read_file 结果时调用
    def read_path_of(self, tool_call_id: str) -> str | None
    def latest_call_for(self, path: str) -> str | None           # 该路径最新一次读取的 call id
```

### trim 层（`src/endless_code/compact/trim.py`，新建）

```python
SUPERSEDED_PREFIX = "[outdated file read;"          # F1 指针标记（幂等检测用）
DEGRADED_PREFIX = "[offloaded result;"              # F2 指针标记

def apply_local_trim(
    messages: list[Message],
    recovery: RecoveryState,
    budget: ContextBudget,
    ledger: TrimLedger,
    active: bool,               # False 时仅重放已冻结指针
) -> list[Message]:
```

- 变换范围限定在 `messages[:recent_tail_start(messages, budget)]` 之前（满足 F4 的
  「近期保留范围不动」），整体 deepcopy 后返回新列表（与 layer1 同模式）。
- F1：结果 content 非指针、其 call id 在读取台账中且不是该路径最新读取 →
  `supersede(id, "[outdated file read; {path}\n已被更新版本的读取取代；如需原文请重新 read_file]")`。
- F2：content 以 L0 预览标记 `[tool result offloaded; original size: N bytes]` 开头 →
  解析出 size 与 `[saved to]` 路径，生成两行纯指针并追加
  `[preview removed to save context]`；已是 DEGRADED 前缀则跳过（幂等）。
- 均不触碰 `ContentReplacementState` 的既有冻结值。

### summary_prompt.py 扩展

```python
def build_summary_prompt(messages: list[Message], previous_summary: str = "") -> list[Message]
```

`previous_summary` 非空时在 `[conversation]` 之前注入「上一版摘要（其全部关键事实必须
原样保留并与新增片段合并）」段；九节输出契约与 `<summary>` 解析不变（N3）。

### layer2.py 变更

```python
def recent_tail_start(messages: list[Message], budget: ContextBudget,
                      max_tokens: int | None = None, min_messages: int | None = None) -> int
def pick_recent_tail(...)   # 改为调用 recent_tail_start 后切片（行为不变）
def leading_compacted_count(messages: list[Message]) -> int
    # 扫描前导：摘要消息（"## 历史会话摘要" 开头）与固定占位 assistant 文本计入 covered
def run_rolling_summary(input_, recent_keep_tokens=None, recent_min_messages=None)
    -> tuple[list[Message], str]:
    # segment = old[state.covered_messages:]，prev = state.summary_text
    # PTL 重试作用于 segment；tail 仍按全量历史选取
def auto_compact(input_, recent_keep_tokens=None, recent_min_messages=None):
    # input_.summary_state 可滚动 → run_rolling_summary，否则 run_summary（现状全量）
    # 成功后 state.rolling_update(...)；空摘要校验与 record_failure/record_success 不变
def force_compact(...)       # 不变：始终全量，不触碰 state
```

### budget.py 追加

```python
MIN_CALIBRATED_WINDOW = 16_000   # 校准下限，防异常小的估算把窗口打到不可用
```

## 模块设计

### compact/compact.py（阶梯编排）

- `ManageInput` 追加 `summary_state: SummaryState | None = None`、
  `trim_ledger: TrimLedger | None = None`（带默认值，旧构造点不破）。
- AUTO 分支改为三级判定：
  1. `offload_and_snip` 后估算 `< 触发线` → 直接返回（现状行为）。
  2. 否则 `trim.apply_local_trim(layer1, recovery, budget, ledger, active=True)` →
     `replace_history` → 重估；`< 触发线` → 返回（本次零 LLM 调用，AC3）。
  3. 仍 `≥ 触发线` → `_auto_compact_guarded`（质量门不变），摘要走滚动或全量由
     `auto_compact` 内部按 state 决定。
- MANUAL / EMERGENCY 分支不变（F7）。

### agent/\_\_init\_\_.py

- `SessionRuntime` 追加 `summary_state: SummaryState`、`trim_ledger: TrimLedger`、
  `clamp_notice_sent: bool = False`；`new_session_runtime` 用 `session.session_dir`
  构造并尝试 `SummaryState.load`（新会话目录无文件 → 全新状态）。
- `ManageInput` 构造处传入两个新状态对象。
- `managed.compacted` 为真后调用 `summary_state.save(session_dir)`。
- **P3 校准**：`round_state.error is PromptTooLongError` 且尚未校准过本轮时，在紧急压缩
  之前执行：`observed = meter.estimate(anchor, messages, anchor_len)`；
  `new_window = max(MIN_CALIBRATED_WINDOW, min(context_window, int(observed * 0.95)))`；
  变小则写回 `context_window`、`refresh_budget(definitions)`，且 `clamp_notice_sent`
  为 False 时 `yield Event(notice=...)` 并置 True。`/model` 切换处复位该标志与窗口
  （复用现有赋值点）。
- read_file 结果处理处：在 `recovery.record_file(...)` 旁追加
  `recovery.record_read(call_id, str(path))`。

### tui/app.py

- 恢复会话处（现有 `open_session_context` 调用点）：
  `runtime.summary_state = SummaryState.load(session_dir)`，损坏自动回退全新状态。
- `_switch_provider` 的运行时复位块追加 `clamp_notice_sent = False`。
- `/status` 增列「滚动摘要轮次」与「校准后窗口」，便于验收观察。

## 模块依赖

`trim → (state, budget, layer2.recent_tail_start, llm)`；`rolling → (state, budget)`；
`layer2 → rolling, summary_prompt`；`compact → trim, layer2, rolling`；`agent → compact`。
无环；`summary_prompt` 不反向依赖 layer2。

## 文件组织

```text
docs/compaction-l1-rolling/
├── spec.md / plan.md / task.md / checklist.md
src/endless_code/
├── compact/trim.py            # 新建：L1 确定性裁剪
├── compact/rolling.py         # 新建：SummaryState 与持久化
├── compact/state.py           # 追加 TrimLedger、RecoveryState 读取台账
├── compact/summary_prompt.py  # build_summary_prompt 增 previous_summary
├── compact/layer2.py          # recent_tail_start / leading_compacted_count / 滚动摘要
├── compact/compact.py         # 三级阶梯编排、ManageInput 增两字段
├── compact/budget.py          # MIN_CALIBRATED_WINDOW
├── compact/__init__.py        # 导出新符号
├── agent/__init__.py          # 状态接线、read 台账记录、P3 校准与一次性提示
└── tui/app.py                 # 恢复加载状态、切换复位、/status 增列
tests/
├── test_trim.py               # 新建：F1/F2 变换、幂等重放、边界不动
├── test_rolling.py            # 新建：增量请求、covered 计算、持久化与损坏回退
├── test_compact.py            # 三级阶梯、L1 后免摘要
├── test_agent.py              # P3 校准与一次性提示、增量接线
└── test_tui.py                # 恢复续增量、/status 新列
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| L1 幂等机制 | TrimLedger 冻结指针 + 标记前缀双保险 | 只靠标记会被 L0 每轮重放的预览盖回；只靠账本则无法识别外部改动；两者都便宜 |
| L1 作用范围 | 限定 `recent_tail_start` 之前的消息 | 严格满足 F4「近期保留范围不动」，且最新读取天然不被裁 |
| 读取路径来源 | 执行期台账（RecoveryState 记 call_id→path） | 解析历史文本脆弱；执行期记录一次即可，且与现有 record_file 同点 |
| covered 语义 | 新历史的前导消息数（摘要+占位） | 消息无稳定 id，按内容标记扫描前导是确定性且与 _join_after_summary 的三种形态一一对应 |
| 滚动摘要的 PTL 重试 | 作用于新片段，丢最旧用户组 | 全量历史已被旧摘要覆盖，重试丢片段不丢已固化事实 |
| 校准下限 | 16_000 常量 | 防御异常小的估算（锚点失效等）把窗口打到不可用；不影响 AC6 的 121_600 |
| 状态持久化 | 会话目录 JSON + tmp 原子替换 + 宽松加载 | N2 要求损坏不致命；与会话生命周期天然对齐 |
| MANUAL/EMERGENCY 不进阶梯 | 维持现状 | F7 明确；手动语义就是「无条件彻底压」，强行增量会让 /compact 结果不可预期 |
