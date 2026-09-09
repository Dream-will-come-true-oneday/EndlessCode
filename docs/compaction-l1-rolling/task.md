# 压缩阶梯 L1 与增量滚动摘要 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/endless_code/compact/rolling.py` | `SummaryState`、滚动可用性判定、JSON 持久化 |
| 新建 | `src/endless_code/compact/trim.py` | L1 确定性裁剪（F1/F2）与指针重放 |
| 新建 | `tests/test_rolling.py` | 增量语义、covered 计算、持久化与损坏回退 |
| 新建 | `tests/test_trim.py` | F1/F2 变换、幂等重放、近期边界不动 |
| 修改 | `src/endless_code/compact/state.py` | `TrimLedger`、`RecoveryState` 读取台账 |
| 修改 | `src/endless_code/compact/summary_prompt.py` | `build_summary_prompt` 增 `previous_summary` |
| 修改 | `src/endless_code/compact/layer2.py` | `recent_tail_start`、`leading_compacted_count`、滚动摘要分派 |
| 修改 | `src/endless_code/compact/compact.py` | 三级阶梯编排、`ManageInput` 增两字段 |
| 修改 | `src/endless_code/compact/budget.py` | `MIN_CALIBRATED_WINDOW` |
| 修改 | `src/endless_code/compact/__init__.py` | 导出新符号 |
| 修改 | `src/endless_code/agent/__init__.py` | 状态接线、read 台账、P3 校准与一次性提示 |
| 修改 | `src/endless_code/tui/app.py` | 恢复加载状态、切换复位、`/status` 增列 |
| 修改 | `tests/test_compact.py` | 三级阶梯、L1 后免摘要 |
| 修改 | `tests/test_agent.py` | P3 校准与一次性提示 |
| 修改 | `tests/test_tui.py` | 恢复续增量、`/status` 新列 |
| 修改 | `tests/test_command_builtin.py` | `SessionInfo` 新字段展示 |
| 修改 | `src/endless_code/command/types.py` | `SessionInfo` 增 `summary_revision`、`calibrated_window` |
| 修改 | `README.md` | 压缩阶梯说明补 L1 与滚动摘要两行 |

## T1: 状态层扩展（TrimLedger 与读取台账）

**文件：** `src/endless_code/compact/state.py`
**依赖：** 无

**步骤：**
1. 新增 `TrimLedger`（线程安全，模式对齐 `ContentReplacementState`）：`pointer_for`、
   `supersede(id, pointer) -> str`（首写冻结、重复调用返回已存文本）。
2. `RecoveryState` 追加 `_reads: dict[str, str]`（call_id→路径）与
   `_latest_call: dict[str, str]`（路径→最新 call_id），实现 `record_read`、
   `read_path_of`、`latest_call_for`，全部持锁。
3. `tests/test_trim.py` 先写账本与台账用例：冻结幂等、并发 record_read、
   `latest_call_for` 随更新迁移。

**验证：** 运行 `python -m pytest tests/test_trim.py -q`，预期全通过。

## T2: SummaryState 与持久化

**文件：** `src/endless_code/compact/rolling.py`、`tests/test_rolling.py`
**依赖：** T1（无实际耦合，仅顺序）

**步骤：**
1. 实现 `SummaryState` 数据类与 `usable_for_rolling` / `rolling_update`。
2. 实现 `save`（tmp 文件 + `os.replace` 原子替换）与 `load`（缺失、损坏 JSON、
   `version` 不符、字段类型错误 → 返回全新实例，不抛出）。
3. `tests/test_rolling.py`：round-trip 保存读取、损坏文件回退全新、
   `usable_for_rolling` 边界（covered 为 0、等于长度、无正文）。

**验证：** 运行 `python -m pytest tests/test_rolling.py -q`，预期全通过。

## T3: 摘要提示词与 layer2 滚动分派

**文件：** `src/endless_code/compact/summary_prompt.py`、`src/endless_code/compact/layer2.py`
**依赖：** T2

**步骤：**
1. `build_summary_prompt(messages, previous_summary="")`：非空时在 `[conversation]`
   前注入「上一版摘要 + 合并要求」段；九节契约与 `<summary>` 解析不变。
2. 抽出 `recent_tail_start(messages, budget, max_tokens=None, min_messages=None) -> int`，
   `pick_recent_tail` 改为其切片包装（行为不变）。
3. 新增 `leading_compacted_count(messages)`：前导的摘要消息（`## 历史会话摘要` 开头）
   与固定占位 assistant 文本计入，遇到其他角色即停。
4. 新增 `run_rolling_summary`：`segment = old[state.covered_messages:]`、
   `prev = state.summary_text`、`build_summary_prompt(segment, prev)`；PTL 重试作用于
   segment；tail 仍按全量历史选取。
5. `auto_compact` 分派：`state.usable_for_rolling(len(old))` → 滚动，否则现状全量；
   成功后 `state.rolling_update(summary_text, leading_compacted_count(messages))`；
   空摘要校验与熔断计数不动；`force_compact` 不触碰 state。
6. `tests/test_rolling.py` 扩展：连续两次压缩第二次请求不含早期消息（用记录请求的
   假 provider 断言）、covered 三形态（无占位 / 有占位 / 纯摘要）、滚动与全量回退。

**验证：** 运行 `python -m pytest tests/test_rolling.py tests/test_compact.py -q`，
预期 `test_rolling` 全通过；`test_compact` 仅因签名演进需要同步（T5 处理）。

## T4: L1 确定性裁剪

**文件：** `src/endless_code/compact/trim.py`、`tests/test_trim.py`
**依赖：** T1、T3

**步骤：**
1. 实现 `apply_local_trim(messages, recovery, budget, ledger, active)`：deepcopy →
   计算 `recent_tail_start` → 仅处理边界之前消息 → `active=False` 时只重放
   `TrimLedger` 指针 → `active=True` 时按 F1（读取台账判定过时读取）与 F2（解析
   `[tool result offloaded; original size: N bytes]` 预览）生成指针、`supersede`
   冻结并替换。
2. 指针文本与标记前缀按 plan.md 固定；F2 生成「原始大小 + saved to 路径 +
   preview removed」三行结构，已是降级前缀则跳过。
3. `tests/test_trim.py` 扩展：AC1（同路径两读，旧指针新保留）、AC2（预览降级，
   路径与大小保留）、近期边界内不动、重放幂等（二次调用字节相同）、
   与 L0 重放叠加不互相覆盖（先 offload 再 trim 再 offload）。

**验证：** 运行 `python -m pytest tests/test_trim.py -q`，预期全通过。

## T5: 阶梯编排与入口字段

**文件：** `src/endless_code/compact/compact.py`、`src/endless_code/compact/budget.py`、
`src/endless_code/compact/__init__.py`、`tests/test_compact.py`
**依赖：** T3、T4

**步骤：**
1. `budget.py` 追加 `MIN_CALIBRATED_WINDOW = 16_000`。
2. `ManageInput` 追加 `summary_state=None`、`trim_ledger=None`（带默认值）。
3. AUTO 分支按 plan.md 三级判定改写：L0 后低于触发线直接返回；否则 trim 并重估，
   低于触发线返回（零 LLM）；仍超线进入 `_auto_compact_guarded`。
4. `compact/__init__.py` 导出 `SummaryState`、`TrimLedger`、`apply_local_trim`、
   `recent_tail_start`、`leading_compacted_count`。
5. 更新 `tests/test_compact.py`：`_input` 支持传入 state/ledger；新增
   「L1 后免摘要」（provider 零调用、`compacted=False` 但驻留量已达标）、
   「手动 /compact 不触发 L1 且仍全量」两个用例。

**验证：** 运行 `python -m pytest tests/test_compact.py tests/test_trim.py -q`，预期全通过。

## T6: Agent 接线与窗口自校准

**文件：** `src/endless_code/agent/__init__.py`、`tests/test_agent.py`
**依赖：** T5

**步骤：**
1. `SessionRuntime` 追加 `summary_state`、`trim_ledger`、`clamp_notice_sent=False`；
   `new_session_runtime` 内 `SummaryState.load(session.session_dir)`；三个 `ManageInput`
   构造点传入状态对象。
2. `managed.compacted` 后调用 `summary_state.save(session.session_dir)`。
3. read_file 结果处理处追加 `recovery.record_read(call_id, str(path))`（与
   `record_file` 同点，取该结果的 tool_call_id）。
4. PTL 分支在紧急压缩之前插入校准：按 plan.md 公式计算新窗口，变小则写回、
   `refresh_budget(definitions)`，`clamp_notice_sent` 为 False 时 yield 一次
   notice 并置 True；`/model` 切换复位块置回 False。
5. `tests/test_agent.py` 新增：校准用例（1M 运行时注入 PTL，断言窗口收敛、预算重建、
   notice 恰好一次）；滚动状态保存用例（压缩后 `summary-state.json` 存在且
   `covered_messages > 0`）。

**验证：** 运行 `python -m pytest tests/test_agent.py -q`，预期全通过。

## T7: TUI 与状态命令

**文件：** `src/endless_code/tui/app.py`、`src/endless_code/command/types.py`、
`src/endless_code/command/builtin.py`、`tests/test_tui.py`、`tests/test_command_builtin.py`
**依赖：** T6

**步骤：**
1. `SessionInfo` 增 `summary_revision: int = 0`、`calibrated_window: int = 0`；
   `get_session_info` 填充（calibrated_window 取 `runtime.context_window`）。
2. `/status` 增「滚动摘要轮次」行；恢复会话处加载 `SummaryState`。
3. `_switch_provider` 复位 `clamp_notice_sent`。
4. 更新两个测试文件：`/status` 新行断言；恢复会话后 `runtime.summary_state`
   来自磁盘（预先写入一个状态文件再 resume，断言 `revision` 一致）。

**验证：** 运行 `python -m pytest tests/test_tui.py tests/test_command_builtin.py -q`，
预期全通过。

## T8: 文档与全量工程检查

**文件：** `README.md`
**依赖：** T7

**步骤：**
1. README「上下文窗口与自动压缩」节补两行：L1 本地裁剪说明、滚动摘要与
   `summary-state.json` 说明、窗口自校准行为。
2. 运行 `python -m pytest -q`、`python -m ruff check .`、
   `python -m ruff format --check .`、`python -m mypy src`（对比基线零新增）。

**验证：** 全量测试通过、lint 与格式通过、覆盖率不低于 85%、mypy 零新增。

## 执行顺序

```text
T1 -> T2 -> T3 -> T4 -> T5 -> T6 -> T7 -> T8
```

T1/T2 相互独立可并行；T3 依赖 T2；T4 依赖 T1、T3 的 `recent_tail_start`；T5 起影响
对外行为。每步验证失败先修复重跑，不得跳过验证进入下一步。
