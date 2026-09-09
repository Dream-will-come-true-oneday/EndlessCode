# 压缩阶梯 L1 与增量滚动摘要 Checklist

标记说明：`[x]` 已验收通过，证据为 2026-09-09 在提交基线 07b7b34 之上的实际改动运行结果。
验证命令统一 `python -m pytest ...`（本机需使用 `& F:\anaconda3\python.exe -m pytest`）。

## 功能与集成

- [x] AC1 同路径读取去重：同一文件读取两次后触发自动压缩，旧版本变为
      `[outdated file read;` 指针行且含路径，最新版本保留原文，工具调用与结果配对完整
      （验证：`pytest tests/test_trim.py -k superseded` → 1 passed）
- [x] AC2 旧落盘预览降级：触发线之前的已落盘结果变为「原始大小 + saved to 路径 +
      preview removed」指针，路径指向的磁盘内容完整；近期保留范围内的预览不变
      （验证：`pytest tests/test_trim.py -k degraded` → 1 passed，断言含
      `original size: 300 bytes`、spill 路径、`[head preview]` 已移除、磁盘文件内容完整）
- [x] AC3 本地裁剪零调用：落盘后估算高于触发线时 L1 自动执行，全程零模型调用，
      裁剪后驻留量低于触发线时不再发起摘要
      （验证：`pytest tests/test_compact.py -k l1_trims` → 1 passed；
      `provider.requests == 0`、`compacted is False`、23 条旧读取转指针、
      `after_tokens < effective_auto_threshold`）
- [x] AC3 指针重放幂等：账本冻结后再调用返回同一文本，L0 预览不会盖回 L1 指针
      （验证：`pytest tests/test_trim.py -k "across_rounds or ledger"` → 5 passed）
- [x] AC3 边界：某路径最新一次读取不被 F2 降级（L0 已落盘时保留其预览正文）
      （验证：`pytest tests/test_compact.py -k l1_trims` 内
      `not startswith(SUPERSEDED_PREFIX)` 且 `"READ24-" in content`）
- [x] AC4 滚动增量请求：连续两次自动压缩，第二次摘要请求只含上次覆盖点之后的新片段
      （验证：`pytest tests/test_rolling.py -k incremental` → 1 passed；
      `pytest tests/test_rolling.py -k "only_covers_new_segment"` 同步通过）
- [x] AC4 合并保留旧事实：滚动摘要的合并提示词要求保留上一版全部关键事实，
      产物含旧摘要的标记内容
      （验证：`pytest tests/test_rolling.py -k merge` → 1 passed）
- [x] AC5 状态持久化与续增量：压缩后 `summary-state.json` 写入会话目录，
      `revision`/`covered_messages` 前进；恢复会话后再次压缩仍走增量
      （验证：`pytest tests/test_rolling.py -k "round_trip or revision or boundaries"` → 4 passed；
      `pytest tests/test_agent.py -k persists_rolling` → 1 passed；
      `pytest tests/test_tui.py -k resume_state` → 1 passed，含 revision 3→4 且磁盘复核）
- [x] AC5 损坏回退：状态文件损坏时触发压缩回退为全量摘要一次并重建状态，会话不崩溃
      （验证：`pytest tests/test_rolling.py -k corrupt` → 1 passed；
      `pytest tests/test_rolling.py -k "missing or rejects_bool"` 同步通过）
- [x] AC6 窗口自校准：1M 运行时注入 PTL 后窗口收敛为 121_600、预算重建、notice 恰好
      一次，紧急压缩后本轮继续；再次 PTL 不重复提示
      （验证：`pytest tests/test_agent.py -k calibrat` → 4 passed；
      断言 `context_window == 121_600`、`budget.context_window == 121_600`、
      notice 计数 1、`clamp_notice_sent is True`、二次收敛 `16_000 < window < 121_600` 且提示 0 次）
- [x] AC6 切换模型复位：`/model` 切换后校准标志与窗口复位
      （验证：`pytest tests/test_tui.py -k switch` → 9 passed；
      断言切换后 `context_window == 64_000`、`clamp_notice_sent is False`）
- [x] AC7 质量门兼容：滚动摘要产物仍受驻留量 / 空摘要 / 孤儿工具结果三查约束；
      被拒时滚动状态回退（不留下未采纳的覆盖点）
      （验证：`pytest tests/test_compact.py -k "quality or blank or orphan"` → 3 passed，
      其中 `state.snapshot() == ("旧版正文", 2, 3)` 证明回退生效）
- [x] AC7 手动语义不变：`/compact` 仍为无条件全量重摘，不触发 L1、不写滚动状态
      （验证：`pytest tests/test_compact.py -k manual` → 2 passed；
      断言 `ledger.pointer_for("c1") is None`、`state.revision == 0`、`len(provider.requests) == 1`）
- [x] F3 触发门槛：估算低于触发线时 L0 后直接返回，L1 与 L2 均不执行
      （验证：`pytest tests/test_compact.py -k below_threshold` → 1 passed）
- [x] F4 冻结与配对安全：L1 不改动 `ContentReplacementState` 冻结值、不删除近期保留
      范围内容、不产生孤儿工具结果
      （验证：`pytest tests/test_trim.py -k "boundary or frozen or ledger"` → 5 passed）
- [x] AC9 状态可见：`/status` 增「滚动摘要轮次」与「校准后窗口」
      （验证：`pytest tests/test_command_builtin.py -k status` → 4 passed；
      未收敛时不输出「校准后窗口」行，收敛后输出 `校准后窗口：121600`）

## 工程检查

- [x] 导入通过（`python -c "import endless_code.agent, endless_code.tui.app, endless_code.compact.trim, endless_code.compact.rolling"` → IMPORT OK）
- [x] 全量单元测试通过（`pytest -q` → 444 passed, 1 skipped）
- [x] 覆盖率不低于 85%（`pytest -q -m "not eval" --cov=endless_code` → 87.69%，门禁 85%；
      新增模块：`trim.py` 93%、`rolling.py` 96%、`budget.py`/`token.py`/`compact.py` 100%）
- [x] ruff lint 与格式通过（`ruff check .` → All checks passed!；`ruff format --check .` → 129 files already formatted）
- [x] mypy 相对基线零新增（`mypy src` → 24 errors，改动前 HEAD 07b7b34 基线 37 errors，净减 13；
      收紧 `ManageInput`/`SessionRuntime.session` 标注使 `tui/app.py` 从 9 降到 3）
- [x] 绝对常量扫描：除 `budget.py` 比例表与 `const.py` 结构常量外无新增裸窗口常量
      （`grep` 复核 `compact/trim.py`、`compact/rolling.py` 无窗口硬编码）
- [x] README 与行为一致（压缩节新增 L0/L1/L2 阶梯、`summary-state.json`、窗口自校准描述）

## 端到端

- [x] 长会话增量演进（进程内）：同一会话连续多轮超限 → 第一轮全量摘要并建状态 →
      后续轮 L1 先行、必要时增量摘要 → 摘要请求逐轮变小且不重复覆盖早期消息
      （验证：`pytest tests/test_agent.py -k rolling_progression` → 1 passed；
      首轮请求含 20 条历史消息、次轮仅含 5 条上次保留原文 + 14 条新消息，
      请求体长度逐轮下降，`revision` 1→2）
- [x] 小窗口自愈（进程内）：窗口配大于模型真实接受量时，PTL 后会话不报错退出，
      收敛后的预算使后续轮次不再超限
      （验证：`pytest tests/test_agent.py -k following_turns` → 1 passed；
      首轮 PTL 收敛到 121_600 后本轮继续得到回复，后续轮 `ptl_calls` 不再增长）
- [x] 恢复会话续增量（进程内 TUI）：带状态的会话 resume 后继续增量压缩，
      `summary-state.json` 的 revision 递增
      （验证：`pytest tests/test_tui.py -k resume_state` → 1 passed；
      resume 后 revision == 3，触发压缩后内存与磁盘均为 4，摘要请求含「上一版摘要」）

## 已知边界（不在本轮验收范围，如实记录）

- 手动 `/compact` 与紧急压缩是全量重摘，会把已建立的滚动状态作废（内存与磁盘同时重置），
  下一次自动压缩退回全量一次；这是 F7「维持现状语义」与状态一致性之间的取舍。
- AUTO 路径在 L0 之后若估算已低于触发线则直接返回，此时不重放 L1 账本：已被 L0 覆写回预览的
  条目要等下一次 L1 才恢复为指针。恢复动作本身字节级幂等（`test_trim` 的 across_rounds 用例），
  因此不会造成内容抖动累积。
- F1 依赖执行期读取台账：`resume` 恢复的历史没有台账，其中的重复读取本轮不会被识别为过时。
- 真实模型下的中段事实保留率复测（compact_retention ≥ 0.9）顺延到下一轮评测周期，
  本轮仅有 fake provider 的结构验证。
