# 动态上下文压缩预算 Checklist

标记说明：`[x]` 为已执行并记录实际结果；验证命令统一使用 `python -m pytest ...`（本仓库解释器为
`F:\anaconda3\python.exe`）。全部通过时间：2026-09-09。

## 功能与集成

- [x] AC1 小窗口派火线：128k 窗口自动压缩阈值为 101_760
      （验证：`pytest tests/test_budget.py::test_one_hundred_twenty_eight_thousand_window` → passed，
      断言 `usable_window == 122_880`、`auto_compact_threshold == 101_760`）
- [x] AC1 小窗口真实压缩：128k 预算下估算越过阈值时 `manage_context` 返回 `compacted=True`
      （验证：`pytest tests/test_compact.py::test_small_window_auto_compaction_triggers_and_tightens_tail`
      → passed，摘要 provider 被调用 2 次，压缩后驻留量低于 `compact_target`）
- [x] AC1 回合内触发：Agent 回合在 128k 窗口下发出 `BEFORE_AUTO`/`AFTER_AUTO` 且历史被摘要替换
      （验证：`pytest tests/test_agent.py::test_small_window_turn_triggers_auto_compaction` → passed）
- [x] AC2 1M 零回归：1M 档 7 项既有量纲等于改动前常量 250000/1000000/100000/65000/15000/50000/25000
      （验证：`pytest tests/test_budget.py::test_one_million_window_reproduces_previous_absolute_constants`
      → passed；新增输出预留 40_000 使阈值成为 795_000，紧急线 845_000）
- [x] AC3 200k 基线：摘要预留 20_000、自动余量 13_000、单条 50_000 B、聚合 200_000 B、
      近期保留 10_000、附件单文件 5_000
      （验证：`pytest tests/test_budget.py::test_two_hundred_thousand_window_matches_legacy_base_line` → passed）
- [x] AC4 落盘线随窗口生效：40KB 结果在 128k 预算下落盘、在 1M 预算下保留原文
      （验证：`pytest tests/test_compact.py::test_layer1_small_window_offloads_what_1m_window_keeps` → passed）
- [x] AC4 聚合落盘：5 条 300KB 结果按从大到小落盘，处理后聚合量回到上限内
      （验证：`pytest tests/test_compact.py -k "layer1 or aggregate"` → 3 passed）
- [x] AC5 降级仍压缩：窗口 1_000 时 `degraded=True`、`effective_auto_threshold=1`，压缩仍执行
      （验证：`pytest tests/test_compact.py::test_degraded_window_still_compacts
      tests/test_tui.py::test_tiny_window_marks_degraded_compaction` → 2 passed）
- [x] AC6 切换模型重建预算：64k 窗口切换后阈值变为 49_344，旧窗口数值不再使用
      （验证：`pytest tests/test_tui.py::test_model_switch_keeps_conversation_session_and_writer` → passed，
      断言 `budget.context_window == 64_000` 与 `get_session_info().auto_compact_threshold == 49_344`）
- [x] AC6 窗口与工具开销来源唯一：`refresh_budget` 改变窗口/工具集后派火线随之变化
      （验证：`pytest tests/test_agent.py::test_refresh_budget_follows_window_and_tools` → passed）
- [x] AC7 质量校验收紧重试：首轮尾部驻留超标时以一半保留量加 1 条下限重试并通过
      （验证：同 `test_small_window_auto_compaction_triggers_and_tightens_tail`，`len(provider.requests) == 2`）
- [x] AC7 质量校验失败不进历史：两次均不达标时 `err=CompactionQualityError`、原始 10 条消息保留、
      连续失败计数 +1（未到熔断阈值）
      （验证：`pytest tests/test_compact.py::test_quality_gate_failure_keeps_history_and_reports_error` → passed）
- [x] 空摘要不覆盖历史：模型只返回空 `<summary>` 标记时保留原始历史并报错
      （验证：`pytest tests/test_compact.py::test_blank_summary_keeps_history` → passed）
- [x] 工具结果配对完整性：压缩结果存在孤儿工具结果时被质量校验拒绝
      （验证：`pytest tests/test_compact.py::test_quality_problem_detects_orphan_tool_results` → passed）
- [x] AC8 计量自校准：门限过滤、EWMA 值 2.875、连续样本夹逼在 1.5–8.0、`reset` 回到 3.5
      （验证：`pytest tests/test_token_meter.py` → 7 passed）
- [x] AC8 校准接线与复位：模型回报 usage 后比例改变且锚点写入；压缩后比例复位
      （验证：`pytest tests/test_agent.py::test_reported_usage_calibrates_token_meter
      tests/test_agent.py::test_compaction_resets_token_meter` → 2 passed）
- [x] AC9 状态可见：`/status` 含上下文窗口、可用窗口、自动压缩阈值三行，降级时带降级说明
      （验证：`pytest tests/test_command_builtin.py -k status` → 3 passed）
- [x] F5 工具开销计入：`estimate_tool_schema_tokens` 对工具集返回正数并等量减少可用窗口
      （验证：`pytest tests/test_budget.py -k tool_schema` → 3 passed）
- [x] F6 单一事实来源：除 `budget.py` 的比例表定义外，`src` 下不再引用
      `SUMMARY_RESERVE`/`AUTO_SAFETY_MARGIN`/`MANUAL_SAFETY_MARGIN`/`SINGLE_RESULT_LIMIT`/
      `MESSAGE_AGGREGATE_LIMIT`/`RECENT_KEEP_TOKENS`/`RECOVERY_TOKENS_PER_FILE`
      （验证：脚本扫描 `src/**/*.py` 排除 `budget.py` → 输出 `outside budget.py: NONE`）

## 工程检查

- [x] 导入通过（验证：`python -c "import endless_code.agent, endless_code.tui.app, endless_code.compact.budget"`
      → 输出 `import ok`）
- [x] 全量单元测试通过（验证：`python -m pytest -q` → `416 passed, 1 skipped`）
- [x] 覆盖率不低于门禁（验证：`python -m pytest -q -m "not eval" --cov=endless_code`
      → `Total coverage: 87.19%`，改动前为 86.36%；`compact/budget.py` 与 `compact/token.py` 100%）
- [x] ruff lint 通过（验证：`python -m ruff check .` → `All checks passed!`）
- [x] ruff format 通过（验证：`python -m ruff format --check .` → `125 files already formatted`）
- [x] mypy 未新增错误（验证：`python -m mypy src` → 37 errors；HEAD 基线同命令 → 41 errors，
      净减 4 条；mypy 不在 CI 门禁内，仅确认无新增）
- [x] 文档与配置示例口径一致（验证：README 新增「上下文窗口与自动压缩」含比例表；
      `config.yaml.example` 注释指向该节并通过 `yaml.safe_load` 校验解析）

## 端到端

- [x] 小窗口长会话（进程内 TUI 等价覆盖）：Textual `run_test` 下驱动 128k/1k 窗口的完整回合，
      界面产生自动压缩并正常回到 IDLE
      （验证：`pytest tests/test_tui.py -k "degraded or resume"` → 3 passed；
      未在真机上手工跑 128k 长会话，交互验证留给下次真实模型评测）
- [x] 默认窗口不回归：1M 档阈值 795_000、紧急线 845_000，`/compact` 手动路径行为不变
      （验证：`pytest tests/test_compact.py::test_manual_manage_context_replaces_history` → passed，
      1M 数值断言见 `test_one_million_window_reproduces_previous_absolute_constants`）
- [x] 会话恢复：恢复超阈值旧会话时先压缩再进入空闲态，且使用预算派火线而非硬编码常量
      （验证：`pytest tests/test_tui.py -k resume` → 2 passed）
- [x] 极小窗口配置：`context_window=1000` 时状态栏显示「降级压缩」、`/status` 显示降级说明、
      发消息不崩溃
      （验证：`pytest tests/test_tui.py::test_tiny_window_marks_degraded_compaction` → passed）

## 已知边界（本轮如实记录，未视为通过）

- `/compact` 与紧急压缩（MANUAL/EMERGENCY）不做质量校验，空摘要仍会替换历史 —— 属已批准设计的
  既有语义，若要收紧需另开子项目。
- `usage_anchor` 仍把 `output_tokens` 计入锚点，比例校准依赖的字节/token 差值口径未对两个协议
  做实测确认，故本轮只加注释不改行为。
