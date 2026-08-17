# MCP 工具延迟加载 Checklist

## 功能与集成

- [ ] **首请求不携带未激活 schema（AC1）**：配置多个 MCP 工具后，首次 LLM 请求的 `tools` 只包含本地内置工具和 `ToolSearch`，不包含未激活 MCP 工具定义（验证：运行 `pytest -q tests/test_agent.py -k "first_request and deferred"`，看到该用例通过）。
- [ ] **系统指令声明 ToolSearch 前置条件（AC2）**：存在延迟 MCP 工具时，系统提示明确要求先加载再调用；无 MCP 时不出现该指令（验证：运行 `pytest -q tests/test_prompt.py -k "deferred and system"`，看到有/无 MCP 两类用例通过）。
- [ ] **合成名录仅包含名称（AC2）**：每次 LLM 请求的临时 user message 按稳定顺序列出所有当前可用但未激活的 MCP 工具名，不含描述和参数 schema（验证：运行 `pytest -q tests/test_prompt.py -k "deferred and catalog"`，看到名称、顺序和内容边界断言通过）。
- [ ] **合成名录不污染历史（AC2）**：合成 user message 可被 Provider 看到，但 Conversation 快照和 JSONL 会话存档中均没有该名录（验证：运行 `pytest -q tests/test_agent.py tests/test_session.py -k "deferred and not_persisted"`，看到请求/存档对比用例通过）。
- [ ] **ToolSearch 激活后下一轮加载完整定义（AC3）**：激活命中工具后，当前工具结果报告成功，下一次 LLM 请求携带它的完整描述和参数 schema，名录不再列出它（验证：运行 `pytest -q tests/test_agent.py -k "tool_search and next_round"`，看到跨轮请求用例通过）。
- [ ] **批量、重复和未命中查询分类正确（AC4）**：一次提交多个名称可批量激活，重复名称不产生重复定义，已激活和不存在的名称进入对应结果分类（验证：运行 `pytest -q tests/test_deferred_tool.py -k "batch or idempotent or not_found"`，看到分类和集合断言通过）。
- [ ] **无效 ToolSearch 输入不改变状态（AC4）**：非法 JSON、缺少名称数组、空数组或非字符串元素均返回可观察错误，后续请求的工具集合不变（验证：运行 `pytest -q tests/test_deferred_tool.py -k "invalid_input"`，看到所有非法输入用例通过）。
- [ ] **未激活直接调用被本地拦截（AC5）**：模型猜出 MCP 工具名并直接调用时，返回“先使用 ToolSearch”错误，不弹出权限确认且 MCP Server 零调用（验证：运行 `pytest -q tests/test_agent.py -k "unactivated and blocked"`，看到审批事件为空、远程调用计数为 0）。
- [ ] **同一响应不能绕过下一轮生效约束（AC5）**：同一 LLM 响应同时返回 ToolSearch 和目标 MCP 调用时，ToolSearch 为下一轮完成激活，当前响应内的目标调用仍被拦截（验证：运行 `pytest -q tests/test_agent.py -k "same_response and tool_search"`，看到首轮远程调用为 0、下一轮可见定义已增加）。
- [ ] **激活状态跨上下文压缩保留（AC6）**：工具激活后触发手动或自动压缩，压缩后的下一次 LLM 请求仍携带该工具完整定义（验证：运行 `pytest -q tests/test_agent.py tests/test_compact.py -k "deferred and compact"`，看到压缩前后定义一致断言通过）。
- [ ] **新会话与恢复会话不继承激活集合（AC6）**：新建 Agent、切换 Provider 或恢复 JSONL 会话后，首请求重新只显示 ToolSearch 和名录（验证：运行 `pytest -q tests/test_tui.py tests/test_agent.py -k "deferred and reset"`，看到所有生命周期重置用例通过）。
- [ ] **激活不等于授权（AC7）**：ToolSearch 不触发人工确认或远程执行；已激活写工具的真实调用仍进入现有权限链，用户拒绝后 Server 零调用（验证：运行 `pytest -q tests/test_agent.py tests/test_permission.py -k "deferred and permission"`，看到激活与调用两阶段事件断言通过）。
- [ ] **Plan Mode 仅暴露只读 MCP 工具（AC8）**：延迟名录不列出非只读工具，ToolSearch 不能激活非只读名称，默认模式已激活的写工具也不出现在 Plan Mode `tools` 中（验证：运行 `pytest -q tests/test_deferred_tool.py tests/test_agent.py -k "deferred and plan_mode"`，看到目录、激活、定义三层过滤用例通过）。
- [ ] **无 MCP 时保持原行为（AC9）**：没有 MCP 工具时，请求中不出现 ToolSearch、延迟系统指令或空名录，本地工具定义与现有版本一致（验证：运行 `pytest -q tests/test_agent.py tests/test_prompt.py tests/test_mcp_cli.py -k "without_mcp or empty_config"`，看到向后兼容用例通过）。
- [ ] **MCP 连接与错误隔离不回归（AC9）**：单个 Server 连接失败不影响其他 Server，stdio/HTTP 工具仍可建立连接并在激活后返回现有结果（验证：运行 `pytest -q tests/test_mcp_manager.py tests/test_mcp_http.py tests/test_mcp_tool.py`，看到全部现有 MCP 用例通过）。
- [ ] **58 工具 Token 降幅达标（AC10）**：固定 58 工具、10 次请求、激活 3 个工具的基准同时计入 ToolSearch、完整已激活定义和未激活名录，lazy 总 Token 较 eager 基线减少不少于 80%（验证：运行 `pytest -q -s tests/test_deferred_tool.py -k token_benchmark`，看到 eager、lazy 和降幅实际数值）。
- [ ] **顺序、并发与规模边界稳定（AC11）**：相同输入产生字节一致的名录、定义和 ToolSearch 结果；并发激活不丢状态；0、1 和数百工具均正常（验证：运行 `pytest -q tests/test_deferred_tool.py -k "deterministic or concurrent or scale"`，看到全部边界用例通过）。
- [ ] **Anthropic、OpenAI 和 OpenAI 兼容协议行为一致（AC11）**：三类 Provider 都只收到过滤后的 tools，且仅附加一条不修改原历史的合成 user message（验证：运行 `pytest -q tests/test_llm.py tests/test_anthropic_provider.py -k "reminder or deferred"`，看到协议转换和输入不变性用例通过）。

## 工程检查

- [ ] **延迟工具聚焦测试通过**（验证：运行 `pytest -q tests/test_deferred_tool.py tests/test_tool.py tests/test_agent.py tests/test_prompt.py tests/test_mcp_cli.py tests/test_llm.py tests/test_anthropic_provider.py tests/test_tui.py`，预期零失败）。
- [ ] **现有 MCP、权限和压缩回归通过**（验证：运行 `pytest -q tests/test_mcp_manager.py tests/test_mcp_tool.py tests/test_mcp_http.py tests/test_permission.py tests/test_compact.py`，预期零失败）。
- [ ] **Python 编译通过**（验证：运行 `python -m compileall -q src examples`，预期退出码 0）。
- [ ] **Ruff 格式通过**（验证：运行 `python -m ruff format --check .`，预期退出码 0）。
- [ ] **Ruff lint 通过**（验证：运行 `python -m ruff check .`，预期退出码 0）。
- [ ] **全量测试通过**（验证：运行 `pytest -q`，预期零失败，跳过项仅限项目原有的环境条件跳过）。
- [ ] **差异无空白错误且范围受控**（验证：运行 `git diff --check` 和 `git status --short`，预期前者退出码 0，后者仅列出本功能文件及用户原有未跟踪文件）。
- [ ] **README 与实测一致**（验证：运行 `rg -n "ToolSearch|58|80%|延迟|Plan Mode" README.md`，预期文档包含使用流程、状态范围、权限边界和实际基准数值）。

## 端到端

- [ ] **完整发现与调用流程**：启动带只读和写入 MCP 工具的会话 -> 首请求只看到名录 -> 模型调用 ToolSearch -> 下一请求获得目标完整 schema -> 模型调用目标 -> 写工具出现人工确认 -> 允许后 MCP Server 收到一次调用并返回结果（验证：运行 `pytest -q tests/test_agent.py -k "e2e_deferred_discover_activate_authorize_execute"`，预期每阶段的请求、工具事件、审批事件和远程调用计数全部符合顺序）。
- [ ] **同轮调用边界**：模型在一个响应内同时请求 ToolSearch 和刚选中的 MCP 工具 -> ToolSearch 成功 -> MCP 调用被拦截 -> 下一轮 schema 出现后才能执行（验证：运行 `pytest -q tests/test_agent.py -k "e2e_same_response_activation_boundary"`，预期首轮远程调用计数为 0，后续合法调用计数为 1）。
- [ ] **Plan Mode 边界**：在默认模式激活一个写工具 -> 切换到 Plan Mode -> 名录与 tools 均不显示该工具 -> 猜名调用也被拦截 -> 只读 MCP 工具可经 ToolSearch 激活并执行（验证：运行 `pytest -q tests/test_agent.py -k "e2e_deferred_plan_mode"`，预期写工具零远程调用、只读工具调用成功）。
- [ ] **无 MCP 边界**：不配置 MCP Server 启动会话 -> 系统提示、合成 user message 和 tools 与开发前一致 -> 本地 read/write/bash 工具仍按现有权限规则工作（验证：运行 `pytest -q tests/test_agent.py tests/test_mcp_cli.py -k "e2e_without_mcp"`，预期没有 ToolSearch/名录且本地工具流程通过）。
