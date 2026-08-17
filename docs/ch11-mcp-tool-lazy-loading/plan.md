# MCP 工具延迟加载 Plan

## 架构概览

保留现有 MCP 连接阶段的 `list_tools` 调用，因为客户端仍需在本地持有工具名称、只读标记和完整 schema。Token 优化发生在构建 LLM 请求时：未激活 MCP 工具的完整定义不进入 `tools` 数组。

系统分为三层：

1. **基础工具注册中心**：保存本地工具和 MCP 工具实例，记录哪些工具是延迟工具，但不保存会话状态。
2. **Agent 会话工具视图**：每个 Agent 实例拥有独立的已激活集合，负责生成当前模式下的可见工具定义、未激活目录、`ToolSearch` 执行和未激活调用拦截。
3. **请求提示编排**：系统提示加入延迟加载规则；每次请求把未激活名称目录与现有 Plan reminder 合并成同一条临时 user message，不写入 Conversation。

```text
MCP Manager.list_tools
        |
        v
Registry (full schema + deferred marker, process scoped)
        |
        v
SessionToolSet (activated names, Agent scoped)
        |                         |
        | definitions(mode)       | inactive_names(mode)
        v                         v
Request.tools              Request.reminder
  built-ins                 deferred name catalog
  ToolSearch                + plan reminder
  activated MCP
```

## 核心数据结构与接口

### Registry 延迟标记

```python
class Registry:
    def register(self, tool: Tool, *, deferred: bool = False) -> None: ...
    def names(self) -> list[str]: ...
    def definition(self, name: str) -> ToolDefinition | None: ...
    def is_deferred(self, name: str) -> bool: ...
    def deferred_names(self, *, read_only_only: bool = False) -> list[str]: ...
```

- `register` 默认 `deferred=False`，所有现有本地工具和测试调用保持兼容。
- CLI 将 MCP Manager 产生的工具以 `deferred=True` 注册。
- 注册中心继续拥有完整 schema 和执行对象；延迟标记只决定 Agent 向 Provider 暴露什么。
- 名称和定义均按现有注册顺序返回；MCP Manager 已保证 MCP 工具按完整名称排序。

### SessionToolSet

```python
TOOL_SEARCH_NAME = "ToolSearch"


class SessionToolSet:
    def __init__(self, registry: Registry) -> None: ...
    def has_deferred(self) -> bool: ...
    def definitions(self, mode: Mode) -> list[ToolDefinition]: ...
    def inactive_names(self, mode: Mode) -> list[str]: ...
    def active_snapshot(self, mode: Mode) -> frozenset[str]: ...
    def known(self, name: str) -> bool: ...
    def is_read_only(self, name: str) -> bool: ...
    def blocked_result(
        self,
        name: str,
        mode: Mode,
        active_at_round_start: frozenset[str],
    ) -> Result | None: ...
    async def execute(
        self,
        name: str,
        args: str,
        mode: Mode,
        active_at_round_start: frozenset[str],
    ) -> Result: ...
    def reset(self) -> None: ...
```

- 内部使用锁保护 `set[str]` 已激活集合。
- `definitions` 依次返回符合当前模式的非延迟工具、`ToolSearch` 和已激活 MCP 工具。已激活集合只影响 MCP 可见性，不复制工具实例。
- Plan Mode 调用 `definitions`/`inactive_names` 时过滤非只读工具；默认模式不过滤。
- `active_snapshot` 在每次 LLM 响应进入执行阶段时冻结已激活名称。同一响应中即使 `ToolSearch` 先执行成功，同批的目标 MCP 调用仍会被拦截；新激活工具只在下一次 LLM 请求生效。
- `blocked_result` 在权限引擎之前拦截未激活 MCP 调用，避免为一个必然拒绝的调用向用户弹出确认。
- `reset` 在会话切换或恢复时清空已激活集合。

### ToolSearch 输入与结果

`ToolSearch` 是 `SessionToolSet` 提供的本地内部工具，不进入 MCP Server，定义为只读：

```json
{
  "name": "ToolSearch",
  "description": "Load one or more deferred MCP tools by exact name.",
  "input_schema": {
    "type": "object",
    "properties": {
      "names": {
        "type": "array",
        "items": {"type": "string"},
        "minItems": 1,
        "uniqueItems": true
      }
    },
    "required": ["names"],
    "additionalProperties": false
  }
}
```

匹配采用完整名精确匹配，不做模糊或语义搜索。执行结果是稳定 JSON：

```json
{
  "activated": ["mcp__server__new_tool"],
  "already_active": ["mcp__server__old_tool"],
  "not_found": ["mcp__server__missing"]
}
```

- 输入非法时返回 `is_error=True`，不改变集合。
- 输出列表按请求去重后的首次出现顺序组织。
- Plan Mode 中非只读名称归入 `not_found`，不泄露该模式下不可用的工具定义。
- `ToolSearch` 的实际执行不调用权限确认 UI；它不进行 I/O，不代表用户授权后续 MCP 调用。

### 提示组装接口

```python
def build_system_prompt(
    instructions: str = "",
    memory: str = "",
    deferred_tools: bool = False,
) -> str: ...


def deferred_tools_reminder(names: list[str]) -> str: ...
def combine_reminders(*parts: str) -> str: ...
```

- `deferred_tools=True` 时添加稳定系统模块，告知模型不得直接调用延迟工具，必须先用 `ToolSearch`。无 MCP 工具时保持现有系统提示字节不变。
- `deferred_tools_reminder` 只渲染未激活完整名称，不调用 `description()` 或 `parameters()`。
- `combine_reminders` 过滤空片段并合并 Plan reminder 和延目录；Provider 继续只附加一条临时 user message。

## 模块设计

### 工具注册与会话视图

- **职责**：保存延迟元数据，维护会话激活集合，生成 Provider 可见工具，执行 `ToolSearch`，拦截未激活直接调用。
- **对外接口**：`Registry` 的延迟查询接口与 `SessionToolSet` 接口。
- **依赖**：现有 `Tool`、`ToolDefinition`、`Result`、`Mode`。不依赖 Provider 或 MCP SDK，避免协议层循环依赖。

### Agent 请求与执行编排

- **职责**：每轮根据 Mode 取工具定义和延迟目录，构建统一 Request，在执行前冻结激活快照，先执行未激活拦截再进入权限引擎。
- **对外接口**：Agent 对外 `run` 和 `run_force_compact` 保持不变；新增会话工具状态重置入口供 TUI 恢复会话时调用。
- **依赖**：`SessionToolSet`、Prompt helper、Permission Engine、Context Manager。

### CLI 与 TUI 生命周期

- CLI 对 MCP Manager 返回的工具使用延迟标记注册。
- 每次选定 Provider 时新建 Agent，自然获得空的激活集合。
- TUI `/resume` 切换到另一会话时显式重置工具激活集合；因激活状态不在 JSONL 中，恢复后由模型按需重新激活。
- 退出时无新的持久化或清理操作。

### Provider 协议适配

- `Request` 数据结构和 Anthropic/OpenAI/DeepSeek 适配器无需新增字段。
- Agent 在调用 Provider 前已完成 `tools` 过滤和 `reminder` 合并，两类 Provider 沿用现有“单条合成 user message”转换。
- Provider 测试需证明合成目录不进入原始 Conversation，且两套协议收到相同的已过滤工具集合。

### Token 基准

- 新增固定基准用例：58 个工具，每个含名称、多段描述和具有多字段/必填项的参数 schema；模拟 10 次 LLM 请求，会话中激活 3 个工具。
- eager 基线：10 轮均序列化 58 个完整工具定义。
- lazy 结果：每轮序列化 `ToolSearch`、已激活完整定义与未激活名称目录。
- 两边使用相同的确定性 JSON 序列化和项目现有字符/Token 估算口径，输出 eager 总量、lazy 总量和降低百分比，断言降低不少于 80%。

## 模块交互

### 首次请求

```text
Agent.run
  -> SessionToolSet.definitions(mode)
       -> built-ins + ToolSearch
  -> SessionToolSet.inactive_names(mode)
       -> all allowed deferred MCP names
  -> combine_reminders(plan reminder, deferred catalog)
  -> Provider.stream(Request)
```

### 激活与下一轮调用

```text
LLM -> ToolSearch(names=["mcp__github__search_repo"])
Agent freezes active_at_round_start
Agent -> SessionToolSet.execute(ToolSearch)
SessionToolSet -> active set add name
Agent -> Conversation stores normal ToolSearch call/result pairing
next Agent iteration
  -> definitions contains full mcp__github__search_repo schema
  -> deferred catalog no longer contains that name
LLM -> mcp__github__search_repo(...)
Agent -> permission chain -> Registry.execute -> MCP Server
```

### 同轮绕过拦截

```text
LLM response contains ToolSearch(target) + target(...) together
  -> active_at_round_start does not contain target
  -> ToolSearch activates target for future requests
  -> target call is rejected before permission/remote execution
  -> next LLM request exposes target schema
```

## 文件组织

```text
src/endless_code/
├── agent/__init__.py          # 按模式构建延迟工具请求、调用拦截、状态重置
├── cli.py                     # MCP 工具以 deferred 标记注册
├── prompt/
│   ├── modules.py             # 可选 ToolSearch 系统指令模块
│   ├── reminder.py            # 延迟目录渲染与 reminder 合并
│   └── __init__.py             # 导出新 helper
├── tool/
│   ├── __init__.py             # Registry 延迟标记和查询接口
│   └── deferred.py             # SessionToolSet、ToolSearch 定义/执行/激活状态
└── tui/app.py                 # 恢复会话时重置激活集合

tests/
├── test_tool.py               # Registry deferred 标记兼容测试
├── test_deferred_tool.py       # 目录、激活、拦截、并发、Token 基准
├── test_agent.py              # 跨轮请求、同轮拦截、Plan/权限/压缩集成
├── test_prompt.py             # 系统指令、目录和 reminder 合并
├── test_mcp_cli.py            # MCP 工具延迟注册集成
├── test_llm.py                # OpenAI 合成 user message 协议验证
├── test_anthropic_provider.py # Anthropic 合成 user message 协议验证
└── test_tui.py                # 恢复会话时重置激活状态

README.md                          # MCP 延迟加载、ToolSearch 和 Token 收益说明
```

## 需求归属

| 需求 | 架构归属 |
|---|---|
| F1 | Registry 延迟标记 + SessionToolSet definitions |
| F2-F4 | Prompt 组装 + SessionToolSet ToolSearch 定义 |
| F5-F9 | SessionToolSet 激活状态 + Agent 执行编排 + TUI 生命周期 |
| F10 | Agent 的拦截/权限顺序 + 现有 Permission Engine |
| F11 | SessionToolSet Mode 过滤 |
| F12 | 现有 MCP Manager 隔离 + Registry/SessionToolSet |

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 延迟位置 | 在 Agent 请求构建前过滤 | MCP 客户端必须先获得 schema 才能在后续激活；真正的 Token 消耗发生在 Provider 请求中 |
| 激活状态归属 | Agent 会话级 SessionToolSet | 避免共享 Registry 导致新会话继承旧激活状态 |
| ToolSearch 实现 | 会话工具视图内部工具 | 它修改会话可见性，不是全局工具，也不应发送到 MCP Server |
| 搜索语义 | 完整名精确匹配，一次可选多个 | 模型每轮已获得全部名称；精确匹配确定、可测，避免新增检索复杂度 |
| 生效时机 | 下一次 LLM 请求 | 模型必须在看到完整 schema 后才能构造合法调用；轮次快照防止同响应绕过 |
| 临时目录承载 | 复用 `Request.reminder` | 现有两套 Provider 已保证 reminder 是单条不持久化 user message，无需改动协议数据模型 |
| 未激活拦截顺序 | 早于权限引擎 | 避免无意义的人工确认，并保证远程调用绝不发生 |
| Plan Mode | 目录、激活和 definitions 三处均过滤 | 即使模型猜出工具名或该工具在默认模式已激活，Plan Mode 仍不暴露非只读 schema |
| 新 SDK 能力 | 不引入 | 延迟加载完全位于项目内部请求编排，不需要 MCP SDK 额外 API；Context7 在当前环境未暴露，现有 SDK 调用保持不变 |

## 替代方案

1. **推荐：Registry 保存全量 + Agent 会话视图**。兼顾会话隔离、执行安全和 Provider 无关性，上述设计采用此方案。
2. **全局 Registry 直接移动工具**。实现代码较少，但激活状态会泄漏到后续会话或 Provider 切换，且并发迭代容易出错，不采用。
3. **在每个 Provider 适配器中过滤**。能避免 Agent 改动，但 Anthropic/OpenAI 需重复激活逻辑，且无法在进入权限链前统一拦截未激活执行，不采用。
