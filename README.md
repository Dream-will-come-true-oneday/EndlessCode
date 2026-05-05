# Endless Code

> 面向多模型的终端智能编程代理

Endless Code 是一个运行在终端中的智能编程助手。它以可取消的多轮 Agent Loop 为核心，让模型持续分析代码库、调用工具、读取结果并调整行动，直到任务完成或触发明确的停止条件。

项目基于 Textual 构建 TUI，统一支持 Anthropic、OpenAI、OpenAI 兼容端点和 DeepSeek。

## 核心能力

- **多轮 Agent Loop**：自动完成“分析 -> 调用工具 -> 读取结果 -> 继续行动”的工作流。
- **Plan / Do 模式**：`/plan` 仅开放只读工具进行调查，`/do` 恢复完整工具集并执行计划。
- **六个内置工具**：`read_file`、`write_file`、`edit_file`、`glob`、`grep` 和 `bash`。
- **安全的工具调度**：连续只读调用可并发执行，写入和命令调用保持顺序边界。
- **流式响应与可取消**：实时显示文本、工具调用、结果、迭代轮次和 Token usage；支持 `Esc` 与 `Ctrl+C` 取消。
- **系统提示工程化**：稳定提示模块化，环境信息、缓存前缀和 `system-reminder` 分离管理。
- **缓存 usage 观测**：兼容 Anthropic、OpenAI 和 DeepSeek 返回的缓存读写字段。
- **输出脱敏**：API key 不会显示在工具预览、错误信息或对话界面中。

## 支持的 Provider

| Provider | 配置方式 | 特性 |
| --- | --- | --- |
| Anthropic | `protocol: anthropic` | 官方 Messages API、稳定 system 缓存断点 |
| OpenAI | `protocol: openai` | 官方 API 或任意 OpenAI 兼容 `base_url` |
| DeepSeek | `protocol: deepseek` | DeepSeek 默认 endpoint、thinking 和 prompt cache 字段 |

DeepSeek 也可以通过 OpenAI 兼容配置使用：将 `protocol` 设置为 `openai`，再指定 `base_url`。专门的 `deepseek` 配置适合需要 `thinking` 或 DeepSeek 缓存 usage 的场景。

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

  - name: openai
    protocol: openai
    model: gpt-4o
    api_key: $OPENAI_API_KEY
    # 可选：第三方 OpenAI 兼容服务
    # base_url: https://example.com/v1

  - name: deepseek
    protocol: deepseek
    model: deepseek-chat
    base_url: https://api.deepseek.com
    api_key: $DEEPSEEK_API_KEY
    thinking: false
```

`api_key` 支持 `$VAR_NAME` 环境变量引用，也支持明文值，但生产环境应优先使用环境变量。配置文件查找顺序为：

1. 当前目录 `.endless-code/config.yaml`
2. 用户目录 `~/.config/endless-code/config.yaml`

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
| `Esc` | 取消当前回合 |
| `Ctrl+C` | 运行时取消回合，空闲时退出 |
| `Ctrl+D` | 退出程序 |
| `/exit`、`/quit` | 退出程序 |

## 系统提示与缓存

稳定系统提示由身份、约束、任务模式、动作执行、工具约定、表达风格和输出格式等模块按优先级组装。工具定义与稳定提示保持固定顺序，便于使用 Provider 的前缀缓存。

每轮请求另外注入：

- 当前工作目录、平台、日期、版本、模型和可用 Git 状态。
- 不写入持久化对话历史的 `<system-reminder>`。
- Plan Mode 的完整或精简轮次提醒。

Anthropic 使用 `cache_control.type: ephemeral` 标记稳定 system 块；OpenAI 和 DeepSeek 使用各自返回的缓存 usage 字段。端点不提供缓存字段时，usage 会以 `0` 展示，不影响对话继续。

## 工具与安全边界

`edit_file` 编辑前必须先读取目标文件，`bash` 描述明确优先使用专用读写搜索工具。工具输出、错误信息和 TUI 内容会进行敏感值脱敏。

当前版本不提供文件系统沙箱或工具执行审批。模型可以访问当前用户有权限访问的路径，也可以执行 shell 命令。请在可信代码库和受控开发环境中使用，并在高风险任务前先运行 `/plan`。

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
- [`task.md`](task.md)：实现顺序与验证步骤
- [`checklist.md`](checklist.md)：功能、集成和端到端验收

## License

MIT