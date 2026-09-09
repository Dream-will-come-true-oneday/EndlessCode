# 动态上下文压缩预算 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/endless_code/compact/budget.py` | 比例表、`ContextBudget`、`build_context_budget`、`estimate_tool_schema_tokens` |
| 新建 | `tests/test_budget.py` | 五档窗口数值、上下限夹逼、派火线、降级、非法入参 |
| 新建 | `tests/test_token_meter.py` | `TokenMeter` 校准门限、EWMA、夹逼区间、重置 |
| 修改 | `src/endless_code/compact/const.py` | 删除被预算取代的 7 个绝对量 |
| 修改 | `src/endless_code/compact/token.py` | `estimate_tokens` 增可选比例参数、新增 `TokenMeter` |
| 修改 | `src/endless_code/compact/layer1.py` | 落盘线改读预算 |
| 修改 | `src/endless_code/compact/layer2.py` | 保留量改读预算，支持收紧重试参数 |
| 修改 | `src/endless_code/compact/recovery.py` | 附件上限改读预算 |
| 修改 | `src/endless_code/compact/compact.py` | `ManageInput` 增 `budget`、触发判定、质量校验 |
| 修改 | `src/endless_code/compact/__init__.py` | 导出预算与计量符号 |
| 修改 | `src/endless_code/agent/__init__.py` | 运行时持有预算与 meter、三处公式改派火线 |
| 修改 | `src/endless_code/tui/app.py` | 窗口变更重建预算、恢复判定、状态栏降级标记 |
| 修改 | `src/endless_code/command/types.py` | `SessionInfo` 增 3 个带默认值字段 |
| 修改 | `src/endless_code/command/builtin.py` | `/status` 增可用窗口、阈值、降级说明 |
| 修改 | `tests/test_compact.py` | 传入预算、小窗口触发、质量校验重试 |
| 修改 | `tests/test_golden.py` | `offload_and_snip` 增预算参数 |
| 修改 | `tests/test_agent.py` | 预算接线、极小窗口仍压缩 |
| 修改 | `tests/test_command_builtin.py` | 新字段展示断言 |
| 修改 | `tests/test_tui.py` | 切换模型后阈值随之变化 |
| 修改 | `README.md` | 阈值随窗口缩放说明与两档对照表 |
| 修改 | `.endless-code/config.yaml.example` | `context_window` 注释更新 |

## T1: 建立预算模块与数值表

**文件：** `src/endless_code/compact/budget.py`、`tests/test_budget.py`
**依赖：** 无

**步骤：**
1. 按 plan.md 的比例表实现 8 项 `_scaled(ratio, window, floor, ceiling)` 与 `ContextBudget`
   的 6 个派生属性（`usable_window`、`auto_compact_threshold`、`emergency_retry_threshold`、
   `degraded`、`effective_auto_threshold`、`compact_target`）。
2. 实现 `build_context_budget`（入参校验：非 int、`bool`、≤0 抛 `ValueError`；
   `tool_schema_tokens` 负数归 0）与 `estimate_tool_schema_tokens`。
3. 写 `tests/test_budget.py`：32k/128k/200k/1M/2M 五档全字段断言；1M 零回归断言；
   200k 与 128k 对齐 archive 基线；`tool_schema_tokens` 扣减；窗口 1_000 的降级；
   2M 的 ceiling 生效；三类非法入参。

**验证：** 运行 `python -m pytest tests/test_budget.py -q`，预期全部通过且 1M 档 7 项数值
等于改动前 `const.py` 中的 250000/1000000/100000/65000/15000/50000/25000。

## T2: 收敛常量并实现 TokenMeter

**文件：** `src/endless_code/compact/const.py`、`src/endless_code/compact/token.py`、`tests/test_token_meter.py`
**依赖：** T1

**步骤：**
1. 从 `const.py` 删除 7 个被取代的绝对量，保留计数型与协议型常量。
2. `estimate_tokens` 增加 `chars_per_token` 可选参数，默认取 `ESTIMATE_CHARS_PER_TOKEN`。
3. 实现 `TokenMeter`（`observe`/`reset`/`chars_per_token`/`estimate`），门限 500 字节与
   200 token，观测比例合法域 1.0–12.0，EWMA α=0.25，结果夹逼 1.5–8.0。
4. 写 `tests/test_token_meter.py`：小样本不更新；合法样本使比例下降（中文场景）；连续异常样本
   被夹逼在区间内；`reset` 回到 3.5。

**验证：** 运行 `python -m pytest tests/test_token_meter.py -q`，预期全通过（此时
`tests/test_compact.py` 会因 `const` 删除符号而导入失败，由 T3 一并处理）。

## T3: 压缩三层与编排层接线

**文件：** `src/endless_code/compact/layer1.py`、`layer2.py`、`recovery.py`、`compact.py`、`__init__.py`
**依赖：** T1、T2

**步骤：**
1. `offload_and_snip` 增 `budget` 参数并改读字节上限；`pick_recent_tail` 增 `budget` 与可选
   `max_tokens`；`render_file_block`/`build_recovery_attachment` 增 `budget`。
2. `ManageInput` 增必填 `budget` 字段；删除 `context_window <= SUMMARY_RESERVE + AUTO_SAFETY_MARGIN`
   早退分支，改用 `budget.effective_auto_threshold`，`degraded` 时记 warning。
3. 新增 `CompactionQualityError` 与 `_compaction_acceptable`，实现收紧到
   `recent_keep_tokens // 2` 的一次重试与失败熔断记录。
4. `compact/__init__.py` 导出 `ContextBudget`、`build_context_budget`、
   `estimate_tool_schema_tokens`、`TokenMeter`。
5. 更新 `tests/test_compact.py`（`_input` 传入 200k 预算、断言随档位变化）与
   `tests/test_golden.py`（`offload_and_snip` 传预算）。

**验证：** 运行 `python -m pytest tests/test_compact.py tests/test_golden.py -q`，预期全通过；
其中小窗口用例断言 `manage_context` 在 128k 窗口下返回 `compacted=True`。

## T4: Agent 与 TUI 接线

**文件：** `src/endless_code/agent/__init__.py`、`src/endless_code/tui/app.py`
**依赖：** T3

**步骤：**
1. `SessionRuntime` 增 `budget` 与 `meter` 字段，`new_session_runtime` 构建默认值；
   新增 `refresh_budget(tool_defs)`。
2. 三处硬编码公式改为 `budget.effective_auto_threshold` 与
   `budget.emergency_retry_threshold`；两处 `ManageInput` 传入 `budget`。
3. 回合内估算改走 `meter.estimate`；`_record_usage_anchor` 先算增量字节与 token 差并
   `meter.observe`，再写锚点；自动与紧急压缩成功后 `meter.reset()`。
4. TUI：`_activate_provider` 与模型切换处设置窗口后 `refresh_budget([])`；会话恢复判定改用
   预算派火线；`_update_status` 在 degraded 时追加降级标记。
5. 删除 `agent` 与 `tui` 中不再需要的 `const` 导入。

**验证：** 运行 `python -m pytest tests/test_agent.py tests/test_tui.py -q`，预期全通过；
`python -c "import endless_code.tui.app, endless_code.agent"` 无 `ImportError`。

## T5: 状态命令展示

**文件：** `src/endless_code/command/types.py`、`src/endless_code/command/builtin.py`、`tests/test_command_builtin.py`
**依赖：** T4

**步骤：**
1. `SessionInfo` 增 `usable_window: int = 0`、`auto_compact_threshold: int = 0`、
   `degraded: bool = False`。
2. `status_command` 在「上下文窗口」行后增「可用窗口」「自动压缩阈值」两行，degraded 时阈值
   行追加降级说明。
3. `tui/app.py:get_session_info` 从 `runtime.budget` 填充三字段。
4. `tests/test_command_builtin.py` 增断言：带阈值与新字段时输出含该行。

**验证：** 运行 `python -m pytest tests/test_command_builtin.py tests/test_tui.py -q`，预期全通过。

## T6: 文档与工程检查

**文件：** `README.md`、`.endless-code/config.yaml.example`
**依赖：** T5

**步骤：**
1. README 的上下文管理章节改述为「阈值按窗口比例推导」，给出 200k 与 1M 两档对照表，并注明
   `docs/ch10-1m-context` 中「不引入可调阈值机制」的排除项已被比例表内置取代。
2. `config.yaml.example` 的 `context_window` 注释补充「阈值随该值等比缩放，1M 与 200k 对照见 README」。
3. 运行 `python -m pytest -q`、`python -m ruff check .`、`python -m mypy src`。

**验证：** 三条命令均退出码 0，`pytest` 覆盖率汇总不低于 85。

## 执行顺序

```text
T1 -> T2 -> T3 -> T4 -> T5 -> T6
```

T1 至 T3 为纯新增与压缩层内部改动，T4 起才影响对外行为；任一环节验证失败须先修复再继续，
不得跳过该环节验证直接进入下一任务。
