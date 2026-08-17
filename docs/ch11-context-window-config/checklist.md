# 可配置上下文窗口与动态压缩阈值 Checklist

## 功能行为

- [ ] **默认窗口统一为 200K（AC1）**：分别构造未配置窗口的 Anthropic、OpenAI、DeepSeek Provider，并直接创建默认运行时。（验证：运行 `F:\anaconda3\python.exe -m pytest -q tests/test_config.py tests/test_agent.py -k "context_window or session_runtime"`，看到三协议和运行时均断言为 200,000。）
- [ ] **显式窗口保持原值（AC2）**：配置 1,000,000、512,000 和其他正整数后，有效窗口不被改写；`0` 仍走 200K 默认。（验证：运行 `F:\anaconda3\python.exe -m pytest -q tests/test_config.py -k "context_window"`，看到显式值和默认值用例全部通过。）
- [ ] **200K token 阈值正确（AC3）**：摘要预留、自动安全余量、紧急安全余量、近期保留、恢复附件依次为 20,000/13,000/3,000/10,000/5,000；自动线为 167,000，紧急重试线为 177,000。（验证：运行 `F:\anaconda3\python.exe -m pytest -q tests/test_limits.py -k "default_window"`，看到精确数值断言通过。）
- [ ] **1M token 阈值正确（AC3）**：上述阈值依次为 100,000/65,000/15,000/50,000/25,000；自动线为 835,000，紧急重试线为 885,000。（验证：运行 `F:\anaconda3\python.exe -m pytest -q tests/test_limits.py -k "one_million"`，看到精确数值断言通过。）
- [ ] **任意窗口连续缩放并向上取整（AC2、AC3）**：128K、300K、512K 和非整除窗口按 200K 基线计算，不出现浮点误差或向下少留安全余量。（验证：运行 `F:\anaconda3\python.exe -m pytest -q tests/test_limits.py`，看到表驱动与取整用例全部通过。）
- [ ] **工具结果线在 200K 下正确（AC4）**：单条结果等于 50,000 字节时保留，超过时落盘；同消息聚合结果回落至不超过 200,000 字节。（验证：运行 `F:\anaconda3\python.exe -m pytest -q tests/test_compact.py -k "layer1"`，看到默认窗口边界、落盘和聚合用例通过。）
- [ ] **工具结果线在 1M 下正确并封顶（AC4）**：单条结果等于 100,000 字节时保留，超过时落盘；聚合结果回落至不超过 400,000 字节；2M 配置不再放大两条保护线。（验证：运行 `F:\anaconda3\python.exe -m pytest -q tests/test_limits.py tests/test_compact.py -k "tool or layer1 or cap"`，看到 1M 与 2M 用例通过。）
- [ ] **工具落盘无信息丢失（AC4）**：超限原文完整保存在会话工具结果目录，历史中包含稳定预览和可重读路径；重复处理不会重复写入或改变预览。（验证：运行 `F:\anaconda3\python.exe -m pytest -q tests/test_compact.py -k "offload or spill or replacement"`，看到文件大小、路径、预览和幂等断言通过。）
- [ ] **摘要近期保留量跟随窗口（AC3、AC6）**：200K 保留约 10K token，1M 保留约 50K token，且两者都至少保留最近 5 条消息并不拆开工具调用配对。（验证：运行 `F:\anaconda3\python.exe -m pytest -q tests/test_compact.py -k "recent_tail"`，看到 200K/1M 与配对用例通过。）
- [ ] **恢复附件上限跟随窗口（AC3、AC6）**：200K 每文件最多约 5K token，1M 最多约 25K token；两者最多附带 5 个文件并保持 UTF-8 完整。（验证：运行 `F:\anaconda3\python.exe -m pytest -q tests/test_compact.py -k "recovery or render_file or truncate_utf8"`，看到截断、数量和编码用例通过。）

## 集成检查

- [ ] **Agent 自动压缩使用动态线（AC5）**：200K 在 167,000 前不触发、达到后触发；1M 在 835,000 前不触发、达到后触发；熔断后的行为保持不变。（验证：运行 `F:\anaconda3\python.exe -m pytest -q tests/test_agent.py tests/test_compact.py -k "auto and compact"`，看到阈值两侧及熔断用例通过。）
- [ ] **紧急压缩重试使用动态线（AC5）**：Provider 报上下文超限后只紧急压缩一次；压缩结果达到 200K/1M 对应安全线时不重试，低于安全线时仅重试原请求一次。（验证：运行 `F:\anaconda3\python.exe -m pytest -q tests/test_agent.py -k "prompt_too_long or emergency"`，看到调用次数、事件顺序和错误结果断言通过。）
- [ ] **手动压缩使用当前窗口的保留配置（AC5、AC6）**：手动 `/compact` 不受自动触发线限制，但摘要后近期消息和恢复附件使用当前 Provider 的动态上限。（验证：运行 `F:\anaconda3\python.exe -m pytest -q tests/test_compact.py tests/test_tui.py -k "manual or compact_command"`，看到手动路径和动态保留用例通过。）
- [ ] **会话恢复与正常运行使用同一阈值（AC5）**：恢复 200K/1M 会话时，低于动态线直接进入空闲态，达到动态线先压缩一次；Writer 切换和时间提醒不回归。（验证：运行 `F:\anaconda3\python.exe -m pytest -q tests/test_tui.py -k "resume"`，看到阈值两侧、会话切换和恢复状态断言通过。）
- [ ] **不同 Provider 窗口互不污染（N2、N4）**：同一进程分别创建 200K 和 1M 运行时，限制配置各自正确，不依赖可变全局状态。（验证：运行 `F:\anaconda3\python.exe -m pytest -q tests/test_limits.py tests/test_agent.py -k "independent or context_window"`，看到两个运行时并列断言通过。）
- [ ] **旧固定阈值已完全移除（N4）**：业务代码不再直接引用 1M 固定动态常量，所有路径从实际窗口取得限制配置。（验证：运行 `rg -n "\b(SINGLE_RESULT_LIMIT|MESSAGE_AGGREGATE_LIMIT|SUMMARY_RESERVE|AUTO_SAFETY_MARGIN|MANUAL_SAFETY_MARGIN|RECENT_KEEP_TOKENS|RECOVERY_TOKENS_PER_FILE)\b" src/endless_code`，预期无匹配。）
- [ ] **配置、运行、恢复调用链可导入（AC5）**：动态限制模块无循环依赖，压缩包顶层导出可用。（验证：运行 `F:\anaconda3\python.exe -c "from endless_code.compact import ContextLimits, build_context_limits; print(build_context_limits(200000).auto_compact_threshold)"`，预期输出 `167000`。）

## 文档检查

- [ ] **README 默认值与示例正确（AC7）**：配置章节明确未配置时默认 200K，并展示显式 1M；长会话章节解释动态 token 阈值和工具 2 倍封顶。（验证：运行 `rg -n "默认 200000|context_window: 1000000|100KB|400KB" README.md`，预期四类说明均存在。）
- [ ] **配置模板可直接表达 200K/1M（AC7）**：模板注释说明默认 200K，并包含可改为 1M 的示例，不修改本地实际 `config.yaml`。（验证：运行 `rg -n "默认 200000|1000000" .endless-code/config.yaml.example`，预期看到默认值与 1M 示例。）
- [ ] **旧 1M 方案已标记为历史（AC7）**：旧 spec/plan 顶部明确指向新方案，读者不会把固定 1M 视为当前默认。（验证：运行 `rg -n "已被.*ch11-context-window-config.*取代" docs/ch10-1m-context`，预期两份文档各有一条标记。）

## 工程检查

- [ ] **全量测试通过（AC6）**。（验证：运行 `F:\anaconda3\python.exe -m pytest -q`，预期退出码 0，无失败或错误。）
- [ ] **格式检查通过**。（验证：运行 `F:\anaconda3\python.exe -m ruff format --check src tests`，预期退出码 0。）
- [ ] **静态检查通过**。（验证：运行 `F:\anaconda3\python.exe -m ruff check src tests`，预期退出码 0。）
- [ ] **源码编译通过**。（验证：运行 `F:\anaconda3\python.exe -m compileall -q src examples`，预期退出码 0 且无错误输出。）
- [ ] **改动范围干净**：未修改 `.endless-code/config.yaml`、会话数据、`.coverage`、`closure_demo.py` 或其他与需求无关文件。（验证：运行 `git status --short` 和 `git diff --stat`，逐项核对仅包含批准文档与 `task.md` 文件清单中的实现文件。）

## 端到端场景

- [ ] **默认 200K 新会话**：使用未设置 `context_window` 的 Provider 启动，运行时窗口显示/断言为 200K；生成超过 50KB 的单条工具结果后原文落盘；会话估算达到 167K 时自动摘要并继续请求。（验证：运行覆盖该完整调用链的集成测试，观察窗口值、落盘文件、压缩事件和后续 Provider 请求均成功。）
- [ ] **显式 1M 新会话**：配置 `context_window: 1000000` 后启动，运行时使用 1M；100KB 内的单条工具结果保留，超出后落盘；聚合保护线为 400KB；会话达到 835K 才自动压缩。（验证：运行 1M 集成测试，观察四个边界值和压缩触发点均与配置一致。）
- [ ] **显式 1M 历史恢复**：恢复估算低于 835K 的会话时不压缩；恢复估算达到 835K 的会话时先压缩一次再进入空闲态，后续消息继续写入原 JSONL。（验证：运行 TUI 恢复集成测试，观察压缩调用次数、最终状态和存档追加位置。）
- [ ] **自定义 512K 会话**：配置 512K 后，token 阈值按 2.56 倍基线计算，工具保护线因 2 倍封顶保持 100KB/400KB；运行和恢复使用同一组结果。（验证：运行自定义窗口集成测试，观察限制配置、Agent 触发线和恢复触发线完全一致。）
