```Markdown
# MCP 客户端 Spec
## 背景
ch06 之后 endless-code 已具备五层权限护栏：一次工具调用在真正执行前必须穿过 黑名单 → 沙箱 → 规则引擎 → 模式兜底 → 人在回路。但工具集是**封闭**的——只有 6 个内置工具，模型无法触达外部世界的数据源与服务。这在 ch02 背景里就被列为预留能力（「在没有工具调用、**权限**、记忆等高级能力之前」）。

本章给 endless-code 接上 **MCP（Model Context Protocol）客户端**：通过官方 `mcp` Python SDK，以 **stdio** 与 **Streamable HTTP** 两种传输拉起远端 MCP server，把远端工具适配成本地 `Tool` 协议后**无缝汇入现有工具流转链路**——权限判定、Agent Loop、结果回灌、TUI 展示对工具来源完全透明。工具命名空间 `mcp__<server>__<tool>` 一眼识别来源，与内置工具天然不冲突。

接入安全护栏：远端工具**默认走既有权限链路**（只读工具按 Read 兜底、其余按 Exec 兜底 Ask），权限规则可写 `mcp__<server>__<tool>` 与 `mcp__<server>__*`；连接失败 / 配置非法 / 超时的 server 只跳过自身、绝不拖垮启动或退出。

## 目标
- **两层配置、天然降级**：用户级 `~/.config/endless-code/mcp.yaml` + 项目级 `<root>/.endless-code/mcp.yaml` 按 server 名合并，项目级完整覆盖同名 server；任一文件缺失视为空层、格式非法跳过该层 + stderr 告警，**永不抛出**。
- **${VAR} 展开、密钥不入配置**：env / headers 的值支持 `${VAR}` 从宿主环境展开；未定义变量展开为空串 + 一次性告警，不阻断启动；command / args / 工具名 / server 名**不展开**。
- **并发连接、失败隔离**：启动时并发连接所有 server，每个 server 30s 超时；失败 / 超时 / 非法只跳过自身 + 告警，其余 server 与内置工具照常注册可用。
- **工具命名空间**：所有 MCP 工具 `name` 形如 `mcp__<server>__<tool>`；拼合后含 LLM 工具名禁用字符的工具被跳过 + 告警；与内置 6 工具天然不重名，不同 server 同名工具互不覆盖。
- **调用超时与错误回灌**：远端调用 30s `asyncio.wait_for` 超时、协议异常、远端 `isError` 均映射为 `Result(is_error=True)` 回灌，**不中断 Agent Loop**。
- **生命周期干净**：退出时统一关闭所有会话（stdio 子进程终止、HTTP 断开），总超时 5s 兜底，绝不阻塞退出。
- **权限链路零改动**：permission 包与 provider 适配层不改一行，MCP 工具经由既有 `friendly_name` / `categorize` / `extract_target` 自然命中五层防御。

## 功能需求

- F1: 两层配置加载与合并
  从用户级 `~/.config/endless-code/mcp.yaml` 与项目级 `<root>/.endless-code/mcp.yaml` 读取 `mcp_servers` 段，按 server 名合并：项目级同名 server **完整覆盖**用户级（不做字段级半合并）。返回归一化的 `Config(servers={name: ServerConfig})`。

- F2: 配置校验与降级
  单个 server 校验：`type` 必为 `stdio` / `http`；`stdio` 必填 `command`；`http` 必填 `url`。违规的 server 跳过 + stderr 告警原因，其余 server 不受影响。文件缺失视为空层；格式非法（YAML 解析失败）跳过该层 + stderr 告警，**不抛未捕获异常**。`Path.home()` 失败时用户层跳过不致错。

- F3: ${VAR} 展开
  对 `env` / `headers` 的**值**做 `${VAR}` 正则展开（`\$\{[A-Za-z_][A-Za-z0-9_]*\}`）；已定义展开为环境值，未定义展开为空串 + stderr 一次性告警（同 server 同变量限一次）。`command` / `args` / 工具名 / server 名不展开，保留字面量。

- F4: stdio 连接
  用 `mcp.client.stdio.stdio_client` 拉起 MCP server 子进程（`command` + `args`，`env` = 宿主环境 ∪ 配置 env，同名宿主变量被覆盖），由 SDK 完成 `initialize` 握手与 `list_tools`。

- F5: HTTP 连接
  用 `mcp.client.streamable_http.streamable_http_client` 连接 Streamable HTTP 端点（`url` + 自定义 `headers`），完成握手与列工具。只消费请求-响应用途，**不订阅**服务端推送长连接。

- F6: 工具列取与注册
  对 `list_tools` 返回的每个远端 `Tool` 调 `adapt_tool` 适配为 `McpTool`；把成功适配的工具按 `full_name` 稳定排序后交给 cli 注册进 registry。工具来源对 agent / tui / permission 透明。

- F7: 工具适配与调用结果处理
  适配：`description` 为空时给兜底文案；`inputSchema` 浅拷贝为 `dict`、空 schema 兜底 `{"type": "object"}`；`read_only` 仅信远端 `annotations.read_only_hint==True`（None-safe）。调用：把远端多个 text 内容块按序拼成 `content`；非 text 块（image / audio / resource_link / embedded_resource）静默丢弃 + 单工具限一次告警；远端 `isError==True` 映射为 `Result.is_error=True` 且保留远端 text。

- F8: 工具命名与命名空间隔离
  命名规则 `mcp__<server>__<tool>`；拼合后不匹配 `^[A-Za-z0-9_-]+$` 的工具跳过 + 告警。不同 server 同名工具互不覆盖；与内置 6 工具天然不重名。

- F9: 连接并发、失败隔离与启动超时
  启动时对每个 server 起独立连接任务，`asyncio.gather` 并发；每个 server 以 `asyncio.wait_for` 施加 30s 超时。连接 / 握手 / 列工具失败只跳过该 server + 告警，其余 server 与内置工具照常注册。启动只在**全部 server 的建立结果**（成功 / 失败 / 超时）齐备后进入 TUI。

- F10: 调用超时与协议错回灌
  远端调用以 `asyncio.wait_for` 施加 30s 超时；超时与协议异常均映射为 `Result(is_error=True, content=可读错因)` 回灌，不中断 Agent Loop、不回滚会话历史。

- F11: 生命周期关闭
  退出时统一关闭所有会话：stdio 子进程终止、HTTP 会话断开；总超时 5s 兜底，超时给 stderr 告警后不再等待，绝不阻塞退出。

- F12: 权限链路自然命中
  不做任何权限适配。`friendly_name` 对 `mcp__<server>__<tool>` 原样返回 → 规则可写 `mcp__<server>__<tool>` / `mcp__<server>__*`；`categorize` 在 `read_only==True` 走 Read、否则归 Exec → 模式兜底矩阵自然命中；`extract_target` 对未知工具返回 `("", False, False)` → 黑名单 / 沙箱自动跳过。

## 非功能需求

- N1: 降级与失败隔离——配置缺失 / 格式非法 / server 连接失败 / 超时，一律只影响自身：不抛未捕获异常、不中断启动、不拖垮其它 server 与内置工具集。
- N2: 校验严格但局部——非法 server 跳过并给出原因，判定只依赖 server 自身字段，不牵连合法 server。
- N3: provider 适配层零改动——`src/endless_code/llm/anthropic_provider.py`、`openai_provider.py` 无修改；工具定义透传，协议无关。
- N4: permission 包零改动——`src/endless_code/permission/` 无修改；MCP 工具的权限判定完全复用既有链路。
- N5: 不中断 Agent Loop——调用超时 / 协议错 / 远端错误均以 `is_error` 结果回灌，Loop 继续、历史一致、既有能力（多轮编排、流式、保序分批并发、取消）不退化。
- N6: 凭据不落盘——配置示例 / 文档 / 测试 fixture 全用 `${VAR}`；密钥经 env / headers 从宿主环境注入，不写入配置文件、不回显。
- N7: 退出干净——`close()` 终止所有子进程 / 断开 HTTP，5s 兜底防卡死，无残留子进程。
- N8: 并发安全——连接并发写共享状态经锁保护；`close` 后无悬挂 task、无死锁、无 `coroutine was never awaited` 告警。
- N9: 代码规范——`ruff format --check .` 无 diff、`ruff check .` 无告警；`pytest` 全过；新增 `tests/test_mcp_*.py`。

## 不做的事
- **资源 / 提示词 / 采样 / roots**——本章只覆盖工具能力，不实现 MCP 资源、提示词、采样与 roots 语义。
- **独立 SSE 推送通道**——只消费请求-响应，不订阅 `streamable_http_client` 返回的服务端推送流。
- **非 text 内容块回灌**——image / audio 等块静默丢弃（模型只能消费文本），仅告警一次。
- **OAuth 完整流程**——用户预换 token 写进 `headers`；本章范围最小化。
- **本地级（第三层）MCP 配置**——只两层；`${VAR}` 已让密钥不入配置，本地层冗余。
- **黑名单 / 沙箱扩展**——MCP 工具的黑名单 / 沙箱行为沿用内置工具的既有判定，不新增作用域。
- **HTTP server 的交互式建连**——无重连 / 重试 / 心跳，连接失败即跳过。

## 验收标准
- AC1: 两层配置——两文件存在时按 server 名合并，同名 server 项目级完整覆盖用户级（验证：单测构造两层文件断言合并结果与字段来源）。(F1)
- AC2: 降级与校验——任一文件缺失视为空、格式非法跳过该文件 + stderr 告警且其它层正常加载；stdio 缺 command / http 缺 url / `type` 非法或缺失的 server 被跳过 + 给出原因，其余 server 不受影响；`load_config` 永不抛出。(F2/N1/N2)
- AC3: ${VAR} 展开——env / headers 的值被展开；未定义展开为空串 + 一次性告警；command / args 中的 `${X}` 保留字面量。(F3)
- AC4: stdio 连接——能拉起 MCP server 子进程并由 SDK 完成 `initialize` + `list_tools`；`env` 注入到子进程环境（验证：真实 demo server 端到端，或 tmux 实跑 `@modelcontextprotocol/server-everything`）。(F4/F6)
- AC5: HTTP 连接——能对 HTTP MCP server 完成握手 + 列工具；`headers` 真正出现在每个 HTTP 请求中（验证：`pytest-httpx` / `httpx.MockTransport` 起最小端点断言收到 `Authorization` 头）。(F5/F6/N6)
- AC6: 工具适配与调用——description 空给兜底；schema 透传 / 空 schema 兜底；`read_only` 仅信 `read_only_hint==True`（None-safe）；多 text 块按序拼接；非 text 块丢弃 + 单工具限一次告警；远端 `isError` 映射（验证：`test_mcp_tool` 注入 stub 覆盖各分支）。(F7)
- AC7: 命名与命名空间——工具 `name` 形如 `mcp__<server>__<tool>`；含禁用字符的工具被跳过 + 告警；registry 注册后全名集合无重复（与内置 6 工具、跨 server 均不冲突）。(F8)
- AC8: 失败隔离与启动超时——一个失败 server + 一个正常 server 启动时：前者告警、后者工具照常注册可用；卡住的 server 在（测试缩短的）超时窗口结束后被跳过，启动不阻塞超过该窗口。(F9/N1)
- AC9: 协议错与超时回灌——远端调用抛异常或 30s 超时 → `Result(is_error=True)` + 可读错因回灌，Agent Loop 不中断。(F10/N5)
- AC10: 退出干净——`close()` 终止所有 stdio 子进程、断开 HTTP；某 session 关闭卡住时 5s 兜底返回不阻塞；实跑退出后无残留子进程。(F11/N7)
- AC11: 权限链路自然命中——无规则时 `readOnlyHint=True` 工具走 Read 兜底（default 放行）、其余走 Exec 兜底（default Ask）；allow 规则 `mcp__<server>__*` 命中直接放行；bypass 放行；MCP 工具不被黑名单 / 沙箱误拦（`extract_target` 返回 `("", False, False)`）。(F12/N4)
- AC12: provider 适配层零改动——`src/endless_code/llm/anthropic_provider.py`、`openai_provider.py` 无修改（验证：核对 diff）。(N3)
- AC13: 既有能力不退化——`pytest` 全过，既有用例无需适配；Agent Loop / 权限 / 工具链路行为不变。(N5)
- AC14: 凭据不落盘——配置示例 / 文档 / 测试 fixture 全用 `${VAR}`；`git grep -E '(Bearer|sk-|ghp_|github_pat_)[A-Za-z0-9_-]{16,}'` 在本次开发期间无命中。(N6)
```
