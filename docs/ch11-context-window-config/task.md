# 可配置上下文窗口与动态压缩阈值 Tasks

## 执行环境

当前 PowerShell 中的 `python` 指向不可用的 Windows Store shim；执行验证统一使用 `F:\anaconda3\python.exe`（Python 3.12.7）。

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/endless_code/compact/limits.py` | 定义动态限制配置、缩放公式与派生触发线 |
| 修改 | `src/endless_code/compact/__init__.py` | 导出动态限制接口 |
| 修改 | `src/endless_code/compact/const.py` | 删除动态常量，仅保留固定常量 |
| 修改 | `src/endless_code/config.py` | 将协议默认窗口统一为 200K |
| 修改 | `src/endless_code/agent/__init__.py` | 运行时默认值及 Agent 动态触发线 |
| 修改 | `src/endless_code/tui/app.py` | 恢复会话使用动态触发线 |
| 修改 | `src/endless_code/compact/compact.py` | 生成并传递动态限制配置 |
| 修改 | `src/endless_code/compact/layer1.py` | 动态工具结果保护线 |
| 修改 | `src/endless_code/compact/layer2.py` | 动态摘要近期保留量 |
| 修改 | `src/endless_code/compact/recovery.py` | 动态恢复附件上限 |
| 新建 | `tests/test_limits.py` | 缩放、取整、封顶和派生公式测试 |
| 修改 | `tests/test_config.py` | 默认与显式窗口测试 |
| 修改 | `tests/test_compact.py` | 工具落盘、摘要、恢复和自动压缩测试 |
| 修改 | `tests/test_agent.py` | 运行时默认、自动和紧急压缩集成测试 |
| 修改 | `tests/test_tui.py` | 恢复路径动态阈值测试 |
| 修改 | `README.md` | 默认 200K、显式 1M 和阈值说明 |
| 修改 | `.endless-code/config.yaml.example` | Provider 配置示例 |
| 修改 | `docs/ch10-1m-context/spec.md` | 标注旧固定 1M 方案已取代 |
| 修改 | `docs/ch10-1m-context/plan.md` | 标注旧固定 1M 方案已取代 |

## T1：实现动态限制配置

**文件：** `src/endless_code/compact/limits.py`、`tests/test_limits.py`  
**依赖：** 无

**步骤：**

1. 定义 200K 基线及各 token/字节基线值，定义工具结果最大缩放倍数 2。
2. 实现正整数向上比例缩放，不使用浮点运算；非正窗口抛出 `ValueError`。
3. 实现冻结的 `ContextLimits`，包含计划中全部字段和三个派生属性。
4. 实现 `build_context_limits(context_window)`：token 阈值连续缩放，工具阈值使用 `min(context_window, 400_000)` 封顶缩放。
5. 添加表驱动测试，覆盖 128K、200K、300K、512K、1M、2M、非整除取整和非正输入。

**验证：** 运行 `F:\anaconda3\python.exe -m pytest -q tests/test_limits.py`，预期全部通过，并确认 200K 为 `50K/200K`、1M 为 `100K/400K`、2M 不再增大工具阈值。

## T2：接入压缩包接口与管理输入

**文件：** `src/endless_code/compact/__init__.py`、`src/endless_code/compact/compact.py`  
**依赖：** T1

**步骤：**

1. 从压缩包顶层导出 `ContextLimits` 与 `build_context_limits`。
2. 为 `ManageInput` 增加从 `context_window` 生成限制配置的只读属性。
3. 在 `manage_context` 中以该配置替代固定摘要余量、自动安全余量和小窗口守卫。
4. 暂时保留 `const.py` 中的旧动态常量，保证尚未迁移的消费方可运行；待 T7 完成全部迁移后统一删除。
5. 暂时保持层 1/层 2 的现有调用可运行，后续任务再传入动态参数。

**验证：** 运行 `F:\anaconda3\python.exe -m pytest -q tests/test_compact.py -k "manage_context"`，预期现有管理流程测试通过且模块不存在被删除常量的导入错误。

## T3：修改默认窗口与配置行为

**文件：** `src/endless_code/config.py`、`src/endless_code/agent/__init__.py`、`tests/test_config.py`、`tests/test_agent.py`  
**依赖：** T1

**步骤：**

1. 将 Anthropic、OpenAI/DeepSeek 的默认窗口常量统一改为 200,000。
2. 将 `SessionRuntime.context_window` 和 `new_session_runtime` 的默认参数改为 200,000。
3. 更新配置测试：三协议未配置时均为 200K，显式 1M、512K 和原有自定义值保持原值。
4. 增加运行时默认值断言，确保直接创建 Agent/Runtime 时也使用 200K。
5. 保留配置中的 `0`、正整数和非法类型/负数校验语义。

**验证：** 运行 `F:\anaconda3\python.exe -m pytest -q tests/test_config.py tests/test_agent.py -k "context_window or session_runtime"`，预期所有默认与显式窗口断言通过。

## T4：让工具结果落盘线动态化

**文件：** `src/endless_code/compact/layer1.py`、`src/endless_code/compact/compact.py`、`tests/test_compact.py`  
**依赖：** T1、T2

**步骤：**

1. 为 `offload_and_snip` 增加可选 `ContextLimits` 参数，缺省时构造 200K 限制配置。
2. 使用 `single_result_bytes` 和 `message_aggregate_bytes` 替代固定常量。
3. 从 `manage_context` 将当前 `ManageInput` 的限制配置传给层 1。
4. 将现有 1M 固定测试改为明确传入 200K 或 1M 配置，避免依赖隐式全局值。
5. 增加边界值测试：等于阈值时保留，超过阈值时落盘；聚合结果按大小降序落盘直至回到限制内；1M 与 2M 均受 `100K/400K` 封顶。

**验证：** 运行 `F:\anaconda3\python.exe -m pytest -q tests/test_compact.py -k "layer1 or spill or replacement"`，预期落盘、聚合、预览稳定性和磁盘失败降级测试全部通过。

## T5：让摘要保留与恢复附件动态化

**文件：** `src/endless_code/compact/layer2.py`、`src/endless_code/compact/recovery.py`、`tests/test_compact.py`  
**依赖：** T1、T2

**步骤：**

1. 为 `pick_recent_tail` 增加 token 保留参数，缺省为 200K 基线 10,000。
2. 为 `render_file_block` 和 `build_recovery_attachment` 增加每文件 token 上限参数，缺省为 200K 基线 5,000。
3. 在 `run_summary` 中读取 `ManageInput` 的限制配置，并分别传给近期历史选择和恢复附件渲染。
4. 保持至少 5 条消息、工具调用不拆对、最多 5 个恢复文件及 UTF-8 安全截断行为不变。
5. 添加 200K 与 1M 下的近期保留和附件截断测试，并验证直接调用 helper 时仍使用 200K 默认值。

**验证：** 运行 `F:\anaconda3\python.exe -m pytest -q tests/test_compact.py -k "recent_tail or recovery or render_file or summary"`，预期动态 token 上限与所有现有摘要行为测试通过。

## T6：统一 Agent 自动与紧急压缩阈值

**文件：** `src/endless_code/agent/__init__.py`、`tests/test_agent.py`  
**依赖：** T1、T2、T3、T4、T5

**步骤：**

1. 移除 Agent 对固定摘要/安全余量常量的导入。
2. 每轮基于 `SessionRuntime.context_window` 构建限制配置。
3. 自动压缩预判使用 `auto_compact_threshold`，并保留熔断逻辑和事件顺序。
4. 紧急压缩后重试判断使用 `emergency_retry_threshold`，仍只重试一次。
5. 增加 200K 与 1M 的阈值集成测试，验证触发前不压缩、达到阈值压缩、紧急压缩后过线不重试。

**验证：** 运行 `F:\anaconda3\python.exe -m pytest -q tests/test_agent.py -k "compact or prompt_too_long or context_window"`，预期自动、紧急和默认窗口场景全部通过。

## T7：统一 TUI 会话恢复阈值并清理旧常量

**文件：** `src/endless_code/tui/app.py`、`src/endless_code/compact/const.py`、`tests/test_tui.py`  
**依赖：** T1、T3、T4、T5、T6

**步骤：**

1. 移除 TUI 对固定摘要/自动安全余量常量的导入。
2. 恢复会话时根据当前 Provider 的运行时窗口构建限制配置，并使用 `auto_compact_threshold`。
3. 保留恢复失败降级、Writer 切换、时间跨度提醒和界面状态恢复行为。
4. 添加恢复测试：200K 与显式 1M 分别在其动态阈值下触发一次压缩，阈值以下不触发。
5. 确认所有消费方已迁移后，从 `const.py` 删除 7 个动态常量，并用搜索确认代码中不再导入这些名称。

**验证：** 运行 `F:\anaconda3\python.exe -m pytest -q tests/test_tui.py -k "resume or compact"`，再运行 `rg -n "\b(SINGLE_RESULT_LIMIT|MESSAGE_AGGREGATE_LIMIT|SUMMARY_RESERVE|AUTO_SAFETY_MARGIN|MANUAL_SAFETY_MARGIN|RECENT_KEEP_TOKENS|RECOVERY_TOKENS_PER_FILE)\b" src/endless_code`；预期测试通过且搜索无匹配。

## T8：更新用户文档与历史方案标记

**文件：** `README.md`、`.endless-code/config.yaml.example`、`docs/ch10-1m-context/spec.md`、`docs/ch10-1m-context/plan.md`  
**依赖：** T3、T4、T5、T6、T7

**步骤：**

1. 将 README 与配置模板的未配置默认值改为 200,000。
2. 同时展示默认 200K 配置和显式 `context_window: 1000000` 的 1M 配置方式。
3. 在长会话章节说明 token 阈值按窗口缩放、工具保护线最多放大 2 倍，并列出 200K 与 1M 的关键数值。
4. 在旧 `ch10-1m-context` 两份文档顶部添加已被 `ch11-context-window-config` 取代的醒目标记，不改写历史设计正文。
5. 检查文档中不存在把固定 1M 方案描述为当前默认行为的未标注内容。

**验证：** 运行 `rg -n "默认 200000|context_window: 1000000|ch11-context-window-config|100KB|400KB" README.md .endless-code/config.yaml.example docs/ch10-1m-context`，预期能看到默认 200K、显式 1M、工具阈值和旧方案取代标记。

## T9：执行全量工程验证

**文件：** 全部本次修改文件  
**依赖：** T1-T8

**步骤：**

1. 运行完整测试套件，修复所有回归。
2. 运行 Ruff 格式检查和静态检查；只格式化本次涉及文件，不改动无关文件。
3. 编译 `src` 与 `examples`，确认无语法或导入错误。
4. 检查 Git diff，确认未修改本地 `.endless-code/config.yaml`、会话数据、`.coverage` 或其他用户文件。

**验证：** 依次运行以下命令，预期全部退出码为 0：

```powershell
F:\anaconda3\python.exe -m pytest -q
F:\anaconda3\python.exe -m ruff format --check src tests
F:\anaconda3\python.exe -m ruff check src tests
F:\anaconda3\python.exe -m compileall -q src examples
```

## 执行顺序

```text
T1 -> T2
T1 -> T3
T2 -> T4 -> T5
T3 + T4 + T5 -> T6 -> T7
T3 + T4 + T5 + T6 + T7 -> T8
T1-T8 -> T9
```
