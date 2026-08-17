# MCP 工具延迟加载 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `src/endless_code/tool/__init__.py` | 为 Registry 增加延迟注册标记和稳定查询接口 |
| 新建 | `src/endless_code/tool/deferred.py` | 实现 SessionToolSet、ToolSearch、激活集合和未激活拦截 |
| 修改 | `src/endless_code/prompt/modules.py` | 添加可选延迟工具系统指令 |
| 修改 | `src/endless_code/prompt/reminder.py` | 渲染延迟工具名录并合并动态 reminder |
| 修改 | `src/endless_code/prompt/__init__.py` | 导出延迟工具提示 helper |
| 修改 | `src/endless_code/agent/__init__.py` | 接入会话工具视图、请求过滤、轮次快照和执行拦截 |
| 修改 | `src/endless_code/cli.py` | 将 MCP 工具标记为延迟注册 |
| 修改 | `src/endless_code/tui/app.py` | 恢复会话时重置工具激活状态 |
| 修改 | `tests/test_tool.py` | Registry 延迟标记和向后兼容测试 |
| 新建 | `tests/test_deferred_tool.py` | SessionToolSet、ToolSearch、Plan Mode、并发与 Token 基准测试 |
| 修改 | `tests/test_agent.py` | 跨轮激活、同轮拦截、权限和压缩集成测试 |
| 修改 | `tests/test_prompt.py` | 延迟指令、名录和 reminder 合并测试 |
| 修改 | `tests/test_mcp_cli.py` | MCP 工具以延迟方式注册的集成测试 |
| 修改 | `tests/test_llm.py` | OpenAI/OpenAI 兼容协议临时名录消息测试 |
| 修改 | `tests/test_anthropic_provider.py` | Anthropic 协议临时名录消息测试 |
| 修改 | `tests/test_tui.py` | 恢复会话清空激活集合测试 |
| 修改 | `README.md` | 说明 MCP 延迟加载流程、会话语义和基准结果 |

## T1：扩展 Registry 延迟元数据

**文件：** `src/endless_code/tool/__init__.py`、`tests/test_tool.py`
**依赖：** 无

**步骤：**
1. 为工具注册增加默认为 false 的延迟标记，并保留重名校验和注册顺序。
2. 增加按名称读取单个定义、全部名称、延迟状态和可选只读过滤的延迟名称接口。
3. 保持现有 `definitions()`、`read_only_definitions()` 和 `execute()` 行为不变，避免影响非 Agent 调用方。
4. 添加默认非延迟、显式延迟、稳定顺序、只读过滤和未知名称的单元测试。

**验证：** 运行 `pytest -q tests/test_tool.py`，预期 Registry 全部旧用例和新增延迟用例通过。

## T2：实现 SessionToolSet 可见性与 ToolSearch

**文件：** `src/endless_code/tool/deferred.py`、`tests/test_deferred_tool.py`
**依赖：** T1

**步骤：**
1. 定义稳定的 `ToolSearch` 工具名、描述和 `names` 数组参数 schema。
2. 实现会话级已激活集合与锁，提供已激活快照、重置、已知工具和只读判定。
3. 实现默认模式与 Plan Mode 下的可见定义和未激活名称目录，保证输出顺序稳定。
4. 实现 `ToolSearch` JSON 解析、去重、精确匹配、`activated`/`already_active`/`not_found` 分类和幂等激活。
5. 实现基于轮次快照的未激活 MCP 拦截，保证被拦截时不委托 Registry 执行。
6. 添加输入错误不改状态、重复激活、不存在名称、Plan Mode 隐藏写工具、重置和同一轮快照拦截测试。

**验证：** 运行 `pytest -q tests/test_deferred_tool.py -k "not token_benchmark"`，预期除 Token 基准外的 SessionToolSet 测试全部通过。

## T3：实现延迟工具系统指令和合成名录

**文件：** `src/endless_code/prompt/modules.py`、`src/endless_code/prompt/reminder.py`、`src/endless_code/prompt/__init__.py`、`tests/test_prompt.py`
**依赖：** T2

**步骤：**
1. 为系统提示构建器增加默认关闭的延迟工具模块，说明必须先调用 `ToolSearch` 再使用 MCP 工具。
2. 实现未激活工具名目录渲染，仅输出名称并在空列表时返回空字符串。
3. 实现多个动态 reminder 的空值过滤与确定性合并，保证 Plan 指令与工具名录同时存在。
4. 导出新 helper，并测试无 MCP 时系统提示字节不变、名录不含 schema/描述、合并顺序稳定。

**验证：** 运行 `pytest -q tests/test_prompt.py`，预期旧提示用例与新增延迟提示用例全部通过。

## T4：接入 Agent 每轮请求构建

**文件：** `src/endless_code/agent/__init__.py`、`tests/test_agent.py`
**依赖：** T2、T3

**步骤：**
1. Agent 初始化时基于 Registry 创建独立 SessionToolSet，保持原 Registry 对外参数不变。
2. 构建系统提示时根据是否存在延迟工具启用 ToolSearch 指令。
3. 每次 Agent 迭代使用 SessionToolSet 生成当前 Mode 的可见 definitions 和未激活名目录，与 Plan reminder 合并后写入 Request。
4. 手动、自动和紧急压缩统一使用当前可见 definitions，确保恢复附件不泄露未激活 schema。
5. 增加跨轮测试：首次请求只有内置工具和 ToolSearch，ToolSearch 结果后下一轮出现完整 MCP 定义，真实 Conversation 不含合成名录。

**验证：** 运行 `pytest -q tests/test_agent.py -k "deferred or tool_search or stable_prefix"`，预期延迟请求和原有稳定提示用例通过。

## T5：接入 Agent 执行拦截与权限链

**文件：** `src/endless_code/agent/__init__.py`、`tests/test_agent.py`
**依赖：** T4

**步骤：**
1. 每次 LLM 响应进入工具执行前获取已激活快照，并传递给该响应中的所有工具执行分支。
2. 用 SessionToolSet 替换 Agent 内的工具已知判定、只读判定和执行委托，保留并行只读/串行写工具调度语义。
3. 在 Permission Engine 之前处理未激活拦截；ToolSearch 按只读本地工具自动执行，不弹出人工确认。
4. 保证已激活 MCP 工具仍经过现有权限判定、允许一次/永久允许/拒绝流程和取消处理。
5. 添加直接调用未激活工具、同一响应同时 ToolSearch+目标调用、激活后权限拒绝和 Plan Mode 非只读拦截测试，并断言远程 execute 未被调用。

**验证：** 运行 `pytest -q tests/test_agent.py -k "deferred or tool_search or permission or plan_mode"`，预期未激活拦截不触发审批/远程执行，已激活调用保留原权限行为。

## T6：接入 CLI 延迟注册和 TUI 会话重置

**文件：** `src/endless_code/cli.py`、`src/endless_code/tui/app.py`、`tests/test_mcp_cli.py`、`tests/test_tui.py`
**依赖：** T4、T5

**步骤：**
1. CLI 注册 MCP Manager 工具时传入延迟标记，本地默认 Registry 不传标记。
2. 为 Agent 增加清空会话激活状态的窄接口。
3. TUI 成功恢复另一会话后，在任何压缩或模型请求前调用状态重置。
4. 更新 CLI 集成测试，断言 MCP 工具存在于 Registry 且标记为延迟；无 MCP 配置时没有延迟工具。
5. 添加 TUI 恢复测试，先激活工具，再恢复会话，断言首次请求回到未激活状态。

**验证：** 运行 `pytest -q tests/test_mcp_cli.py tests/test_tui.py -k "mcp or deferred or resume"`，预期 MCP 注册和会话恢复状态用例通过。

## T7：验证 Provider 合成消息协议一致性

**文件：** `tests/test_llm.py`、`tests/test_anthropic_provider.py`、`tests/test_prompt.py`
**依赖：** T3、T4

**步骤：**
1. 为 OpenAI 请求增加包含延迟名录的 reminder 断言，确认它是最后一条合成 user message 且 `tools` 只含已经过滤的定义。
2. 为 DeepSeek/OpenAI 兼容请求增加相同断言，确认复用 OpenAI 协议时行为一致。
3. 为 Anthropic 请求增加合成 user content block 和已过滤 tools 断言。
4. 断言上述转换均不修改原始 `Request.messages` 或 Conversation 快照。

**验证：** 运行 `pytest -q tests/test_llm.py tests/test_anthropic_provider.py tests/test_prompt.py`，预期 Anthropic、OpenAI 和 DeepSeek 合成消息用例全部通过。

## T8：实现并发安全与确定性测试

**文件：** `tests/test_deferred_tool.py`
**依赖：** T2

**步骤：**
1. 并发执行多个有重叠名称的 ToolSearch 请求，验证激活集合最终完整、无重复且无迭代异常。
2. 对相同 Registry 和激活步骤重复构建 SessionToolSet，验证 definitions、名录和 ToolSearch JSON 结果字节一致。
3. 验证工具数量为 0、1 和数百时不出现边界错误或非确定顺序。

**验证：** 运行 `pytest -q tests/test_deferred_tool.py -k "concurrent or deterministic or scale"`，预期并发、确定性和规模边界用例全部通过。

## T9：建立 58 工具 Token 基准

**文件：** `tests/test_deferred_tool.py`
**依赖：** T2、T3

**步骤：**
1. 生成固定 58 个 MCP 风格工具定义，每个包含稳定的多段描述、多字段参数、required 和 additionalProperties 约束。
2. 模拟 10 次请求，按固定轮次激活 3 个工具，分别累计 eager 全量定义与 lazy 定义+名录的序列化字符数。
3. 用项目统一的 3.5 字符/Token 口径转换并输出 eager token、lazy token 和减少百分比。
4. 断言 lazy 总量低于 eager，且减少比例不少于 80%。

**验证：** 运行 `pytest -q -s tests/test_deferred_tool.py -k token_benchmark`，预期命令打印 eager/lazy token 和减少比例，减少比例至少 80%。

## T10：完成 MCP 和上下文压缩回归测试

**文件：** `tests/test_agent.py`、`tests/test_mcp_manager.py`、`tests/test_mcp_http.py`、`tests/test_compact.py`
**依赖：** T4、T5、T6

**步骤：**
1. 添加激活工具经历手动或自动压缩后仍出现于后续 definitions 的集成断言。
2. 验证压缩恢复附件只列出当前可见工具，不包含未激活 MCP schema。
3. 运行现有 stdio/HTTP MCP 管理和工具执行用例，确认连接、错误隔离、headers、超时和结果适配未改变。
4. 对回归暴露的测试断言仅做符合 spec 的更新，不改变 MCP 运输和压缩语义。

**验证：** 运行 `pytest -q tests/test_agent.py tests/test_compact.py tests/test_mcp_manager.py tests/test_mcp_tool.py tests/test_mcp_http.py`，预期延迟状态跨压缩保留，现有 MCP 运输/执行测试全部通过。

## T11：更新用户文档

**文件：** `README.md`
**依赖：** T6、T9、T10

**步骤：**
1. 在 MCP 章节说明 MCP 工具默认仅暴露名称，模型通过 ToolSearch 激活后下一轮获得完整 schema。
2. 说明已激活状态仅当前会话有效，Plan Mode 仅能加载只读 MCP 工具，实际调用仍经过五层权限链。
3. 填写 T9 实际测得的 58 工具基准数据，注明固定模拟场景而非所有会话的保证值。
4. 确认现有 MCP 配置示例无需更改，用户不需要新增配置开关。

**验证：** 运行 `rg -n "ToolSearch|80%|58|Plan Mode|延迟" README.md`，预期可找到延迟流程、状态边界、权限兼容和实际基准结果。

## T12：格式化、静态检查与全量回归

**文件：** 本任务中的所有 Python 和 Markdown 改动
**依赖：** T1-T11

**步骤：**
1. 运行 Ruff 格式检查，如有格式差异则仅格式化本次改动文件后重跑。
2. 运行 Ruff lint，修复本次改动导致的全部诊断。
3. 运行全量 pytest，对失败用例先修复再重跑，不通过删除断言规避回归。
4. 执行 `git diff --check` 和变更范围检查，确认未修改用户现有的无关文件。

**验证：** 依次运行 `python -m ruff format --check .`、`python -m ruff check .`、`pytest -q`、`git diff --check`，预期所有命令退出码为 0，全量测试通过。

## 执行顺序

```text
T1 -> T2 -> T3 -> T4 -> T5 -> T6
          |      |             |
          +-> T8 |             +-> T10
          +-> T9 +-> T7             |
                  \__________________+-> T11 -> T12
```

- T1-T6 是主实现链。
- T7-T10 在各自依赖完成后可分组执行，但最终必须全部完成才能进入 T11。
- T12 只在实现、基准和文档全部完成后执行。
