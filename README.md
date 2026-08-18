# Endless Code

> 面向多模型的终端智能编程代理

Endless Code 是一个运行在终端中的智能编程助手。它以可取消的多轮 Agent Loop 为核心，让模型持续分析代码库、调用工具、读取结果并调整行动，直到任务完成或触发明确的停止条件。

项目基于 Textual 构建 TUI，统一支持 Anthropic、OpenAI、OpenAI 兼容端点和 DeepSeek。

## 核心能力

- **多轮 Agent Loop**：自动完成“分析 -> 调用工具 -> 读取结果 -> 继续行动”的工作流。
- **Plan / Do 模式**：`/plan` 仅开放只读工具进行调查，`/do` 恢复完整工具集并执行计划。
- **六个内置工具**：`read_file`、`write_file`、`edit_file`、`glob`、`grep` 和 `bash`。
- **MCP 客户端接入**：通过 stdio 与 Streamable HTTP 连接 MCP server，远端工具以 `mcp__<server>__<tool>` 命名空间并入既有工具与权限链路，密钥经 `${VAR}` 注入不落盘。
- **安全的工具调度**：连续只读调用可并发执行，写入和命令调用保持顺序边界。
- **流式响应与可取消**：实时显示文本、工具调用、结果、迭代轮次和 Token usage；支持 `Esc` 与 `Ctrl+C` 取消。
- **系统提示工程化**：稳定提示模块化，环境信息、缓存前缀和 `system-reminder` 分离管理。
- **缓存 usage 观测**：兼容 Anthropic、OpenAI 和 DeepSeek 返回的缓存读写字段。
- **长会话上下文管理**：超大工具结果自动落盘并保留稳定预览；接近 Provider 上下文窗口时自动摘要、恢复最近文件和可用工具，避免长任务因历史膨胀中断。
- **跨会话项目记忆**：启动时加载项目指令与长期记忆索引；对话实时写入 JSONL，支持 `/resume` 搜索并恢复历史会话。
- **输出脱敏**：API key 不会显示在工具预览、错误信息或对话界面中。

## 支持的 Provider

| Provider | 配置方式 | 特性 |
| --- | --- | --- |
| Anthropic | `protocol: anthropic` | 官方 Messages API、稳定 system 缓存断点 |
| OpenAI | `protocol: openai` | 官方 API 或任意 OpenAI 兼容 `base_url` |

DeepSeek 等 OpenAI 兼容服务可直接将 `protocol` 设置为 `openai`，再指定 `base_url` 接入。

## 快速开始

要求 Python 3.12 或更高版本。

```bash
git clone https://github.com/Dream-will-come-true-oneday/Endless-Coding.git
cd Endless-Coding
python -m pip install -e .
```

复制配置模板：

```bash
# macOS / Linux
cp .endless-code/config.yaml.example .endless-code/config.yaml

# Windows PowerShell
Copy-Item .endless-code/config.yaml.example .endless-code/config.yaml
```

设置所需的 API key。推荐使用环境变量，不要把真实 key 写入配置文件：

```bash
export ANTHROPIC_API_KEY="your-key"
export OPENAI_API_KEY="your-key"
export DEEPSEEK_API_KEY="your-key"
```

Windows PowerShell：

```powershell
$env:ANTHROPIC_API_KEY = "your-key"
$env:OPENAI_API_KEY = "your-key"
$env:DEEPSEEK_API_KEY = "your-key"
```

启动：

```bash
endless-code
```

## 配置 Provider

配置文件为 `.endless-code/config.yaml`，可以同时配置多个 Provider，启动后选择要使用的模型：

```yaml
providers:
  - name: anthropic
    protocol: anthropic
    model: claude-3-5-sonnet-latest
    api_key: $ANTHROPIC_API_KEY
    # 可选；未配置时默认 200000
    context_window: 200000

  - name: openai
    protocol: openai
    model: gpt-4o
    api_key: $OPENAI_API_KEY
    # 可选；显式 1M；未配置时默认 200000
    context_window: 1000000
    # 可选：第三方 OpenAI 兼容服务（如 DeepSeek，base_url 指向其 API 地址）
    # base_url: https://api.deepseek.com

```

`api_key` 支持 `$VAR_NAME` 环境变量引用，也支持明文值，但生产环境应优先使用环境变量。配置文件查找顺序为：

1. 当前目录 `.endless-code/config.yaml`
2. 用户目录 `~/.config/endless-code/config.yaml`

## MCP 工具扩展

Endless Code 内置 MCP（Model Context Protocol）客户端，通过 **stdio** 或 **Streamable HTTP** 连接 MCP server，把远端工具接入本地工具链路。工具命名为 `mcp__<server>__<tool>`（如 `mcp__github__search_repo`），与内置 6 个工具天然不冲突；权限规则可直接写 `mcp__<server>__<tool>` 或 `mcp__<server>__*`。

MCP 工具默认延迟加载。每轮请求只向模型列出未加载工具的名称，不携带完整描述和参数 schema。模型需要某个工具时会先调用 `ToolSearch`，被选中工具的完整 schema 从下一轮请求开始可用。已加载集合只属于当前运行会话，新会话、恢复会话或重启进程后会重置。

固定模拟基准中，58 个 MCP 工具经过 10 次请求并加载其中 3 个工具时，工具元数据估算从 968,935 Token 降至 35,294 Token，减少 96.36%，高于 80% 验收线。该数据用于可重复回归验证，实际收益取决于工具 schema 大小、请求轮数和加载工具数量。

MCP 配置同样分两层 YAML，同名 server 项目级完整覆盖用户级：

| 位置 | 路径 |
| --- | --- |
| 项目级 | `.endless-code/mcp.yaml` |
| 用户级 | `~/.config/endless-code/mcp.yaml` |

```yaml
mcp_servers:
  github:
    type: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_TOKEN: "${GITHUB_TOKEN}"      # 从宿主环境变量展开，密钥不入配置

  example-http:
    type: http
    url: "https://mcp.example.com/mcp"
    headers:
      Authorization: "Bearer ${EXAMPLE_TOKEN}"
```

- `env` / `headers` 的值支持 `${VAR}` 展开；未定义变量展开为空串并在启动时告警，不阻断启动。
- 每个 server 启动连接超时 30s、调用超时 30s；连接失败 / 配置非法 / 超时的 server 只跳过自身，其余 server 与内置工具照常可用。
- 远端声明只读（`readOnlyHint`）的工具走只读兜底，其余工具在 default 模式需人在回路确认，行为与内置工具一致。
- Plan Mode 的延迟目录和 `ToolSearch` 只暴露远端声明为只读的 MCP 工具。加载只改变模型可见性，不代表用户授权；真实调用仍完整经过五层权限链。
- 退出时自动终止所有 stdio 子进程并断开 HTTP 会话。
- 完整示例见 [`docs/mcp/mcp-servers.example.yaml`](docs/mcp/mcp-servers.example.yaml)。

## 使用方式

直接输入任务，Agent 会自主检索、修改和验证：

```text
定位这个项目的测试失败原因，修复后运行相关测试。
```

常用命令：

| 输入 | 行为 |
| --- | --- |
| `Enter` | 提交消息 |
| `/plan` | 进入只读计划模式 |
| `/do` | 退出计划模式并执行当前计划 |
| `/compact` | 立即压缩当前会话历史，不等待自动阈值 |
| `/resume` | 搜索、选择并恢复一个历史会话 |
| `/memory` | 查看用户级与项目级记忆路径、大小、分类和摘要 |
| `/memory clear user\|project\|all` | 30 秒内重复输入后清空对应范围的记忆 |
| `Esc` | 取消当前回合 |
| `Ctrl+C` | 运行时取消回合，空闲时退出 |
| `Ctrl+D` | 退出程序 |
| `/exit`、`/quit` | 退出程序 |

## 长会话上下文管理

Endless Code 会在每次请求前检查工具结果和会话长度。默认上下文窗口为 200K，Provider 可通过 `context_window` 选择任意正整数窗口（例如显式 1M）。在 200K 窗口下，单条超过 50KB 的工具结果会保存到 `.endless-code/sessions/<会话 ID>/tool-results/`，同消息聚合线为 200KB；窗口增大时两条工具保护线按比例放大，但最多为 100KB/400KB。会话中仅保留头部预览与 `read_file` 重读路径。

自动压缩阈值按当前窗口动态计算：200K 窗口在约 167K token 触发，1M 窗口在约 835K token 触发；紧急压缩后的重试安全线分别约为 177K 和 885K。摘要会按窗口保留近期原文和恢复附件，并补回当前可用工具和边界提示。界面会显示压缩开始与完成状态。遇到 Provider 报告上下文超限时，程序会先紧急压缩，再仅重试原请求一次。

`/compact` 可在空闲时手动触发摘要。工具原文和会话存档位于 `.endless-code/` 的 Git 忽略目录；项目记忆位于 `.endless-code/memory/`，可按团队需要提交，用户级记忆位于 `~/.config/endless-code/memory/`。

## 项目指令、会话与长期记忆

Endless Code 会在启动时按优先级读取下列 `ENDLESSCODE.md` 文件，并将内容作为项目指令注入请求期历史前缀：

1. `<项目根目录>/ENDLESSCODE.md`
2. `<项目根目录>/.endless-code/ENDLESSCODE.md`
3. `~/.config/endless-code/ENDLESSCODE.md`

指令文件支持独占一行的 `@include 相对路径`，可拆分代码规范；展开限制为 5 层，并会阻止路径逃逸和循环引用。

每个新会话会写入 `.endless-code/sessions/YYYYMMDD-HHMMSS-xxxx/conversation.jsonl`。消息、工具调用和工具结果会在每次变更后追加并执行 `fsync`，异常中断最多影响最后一行。输入 `/resume` 后可用上下键选择、输入关键词过滤、按 Enter 恢复；恢复后后续消息继续追加到原会话。

新格式会话在启动时异步清理 30 天前的数据，旧格式目录不会被自动删除。Agent 每次正常完成用户回合后都会异步提取长期记忆，不阻塞下一次输入。用户偏好和纠正反馈写入用户级目录，项目知识和参考资料写入项目级目录；分类到作用域的映射由程序强制执行，模型不能把项目知识写进用户级记忆。

每个会话首次请求时会在真实对话前附加两条不持久化的合成消息，依次提供环境、项目指令、用户级索引和项目级索引；压缩后根据最新文件重建。每个用户请求还会用本地中英文词元检索最多 6 条、合计不超过 8KB 的相关笔记全文，通过临时 reminder 提供给 Agent。合成前缀和召回内容都不会写入 `conversation.jsonl`。用户可直接编辑独立 Markdown 笔记；下一次请求或 `/memory` 会自动重建索引。

## 系统提示与缓存

稳定系统提示由身份、约束、任务模式、动作执行、工具约定、表达风格和输出格式等模块按优先级组装。工具定义与稳定提示保持固定顺序，便于使用 Provider 的前缀缓存。

请求期上下文另外包含：

- 会话首次请求和压缩后重建的环境、项目指令与两级记忆索引历史前缀。
- 不写入持久化对话历史的相关记忆与 MCP 工具目录 `<system-reminder>`。
- Plan Mode 的完整或精简轮次提醒。

Anthropic 使用 `cache_control.type: ephemeral` 标记稳定 system 块；OpenAI 和 DeepSeek 使用各自返回的缓存 usage 字段。端点不提供缓存字段时，usage 会以 `0` 展示，不影响对话继续。

## 工具与安全边界

`edit_file` 编辑前必须先读取目标文件，`bash` 描述明确优先使用专用读写搜索工具。工具输出、错误信息和 TUI 内容会进行敏感值脱敏。

内置五层权限系统：危险命令黑名单、路径沙箱、可配置规则、四档权限模式（default / acceptEdits / plan / bypassPermissions）与人在回路审批。文件读写默认限制在项目根内，命令执行按模式决定是否弹窗确认；`Shift+Tab` 可实时切换权限模式，`/plan` 仍用于只读规划。请在可信代码库和受控开发环境中使用，并在高风险任务前先运行 `/plan`。

## 开发与验证

```bash
python -m pytest -q
python -m pytest -q -W error::ResourceWarning
python -m ruff format --check .
python -m ruff check .
python -m compileall -q src examples
python examples/smoke.py --provider openai
```

项目采用 Spec 驱动开发：

- [`spec.md`](spec.md)：需求与验收标准
- [`plan.md`](plan.md)：架构与技术设计

## License

MIT
