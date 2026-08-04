# endless-code

> 一个会持续执行，直到任务完成的终端 AI 编程 Agent。

endless-code 是一个基于 Textual 的轻量级 TUI 编程助手。它连接 DeepSeek、OpenAI
及 OpenAI 兼容端点，让模型在同一个用户回合中持续分析、调用工具、读取结果并调整行动，
直到自然完成或触发明确的停止条件。

它的核心不是“在终端里聊天”，而是让模型真正完成代码库中的多步工作。

## 核心能力

- **自主 Agent Loop**：连续执行“判断 -> 调用工具 -> 回灌结果 -> 继续判断”，无需用户逐步催促。
- **六个内置工具**：读取、写入、精确编辑文件，执行 Shell 命令，以及 Glob/Grep 搜索。
- **Plan / Do 工作流**：`/plan` 只开放只读工具进行调研，`/do` 恢复全部工具并按计划执行。
- **保序并发调度**：连续只读工具并发执行；写文件、编辑和命令等副作用工具保持串行边界。
- **可取消、可恢复**：流式响应或工具运行期间可随时取消，清理任务和子进程后继续对话。
- **完整运行反馈**：实时展示文本、工具调用、结果摘要、迭代轮次和累计 Token 用量。
- **双 Provider 支持**：DeepSeek 与 OpenAI 共用一致的流式工具调用和历史回灌语义。
- **输出脱敏**：API 密钥不会出现在工具预览、结果摘要、错误信息或对话界面中。

## 工作方式

```text
用户任务
   |
   v
模型流式响应 ---- 无工具调用 ----> 最终答复
   |
   | 工具调用
   v
只读工具并发 / 副作用工具串行
   |
   v
结果按原顺序写回会话
   |
   +--------------------------> 下一轮模型判断
```

循环会在模型自然完成、达到 25 轮上限、连续请求未知工具、响应出错或用户取消时停止。
所有停止路径都会补全合法会话历史，下一条消息可以正常继续。

## 快速开始

要求 Python 3.12 或更高版本。

```bash
git clone https://github.com/Dream-will-come-true-oneday/Endless-Coding.git
cd Endless-Coding
python -m pip install -e .
```

复制示例配置：

```bash
# macOS / Linux
cp .endless-code/config.yaml.example .endless-code/config.yaml

# Windows PowerShell
Copy-Item .endless-code/config.yaml.example .endless-code/config.yaml
```

推荐通过环境变量提供 API 密钥：

```bash
# macOS / Linux
export DEEPSEEK_API_KEY="your-key"
export OPENAI_API_KEY="your-key"
```

```powershell
# Windows PowerShell
$env:DEEPSEEK_API_KEY = "your-key"
$env:OPENAI_API_KEY = "your-key"
```

启动应用：

```bash
endless-code

# 或
python -m endless_code
```

## 配置

`.endless-code/config.yaml` 可以配置一个或多个 Provider：

```yaml
providers:
  - name: deepseek
    protocol: deepseek
    model: deepseek-chat
    base_url: https://api.deepseek.com
    api_key: $DEEPSEEK_API_KEY
    thinking: false

  - name: openai
    protocol: openai
    model: gpt-4o
    api_key: $OPENAI_API_KEY
```

`api_key` 支持 `$VAR_NAME` 环境变量引用或明文值。建议始终使用环境变量，避免密钥进入配置文件、
终端历史或版本控制。

配置文件按以下顺序查找：

1. 当前目录 `.endless-code/config.yaml`
2. 用户目录 `~/.config/endless-code/config.yaml`

只配置一个 Provider 时自动启用；配置多个 Provider 时，启动后输入编号选择。OpenAI 兼容服务可通过
`protocol: openai` 与自定义 `base_url` 接入。DeepSeek 可设置 `thinking: true` 启用扩展思考。

## 使用

直接描述希望完成的结果，例如：

```text
检查这个项目的测试失败，定位原因，修复后运行相关测试。
```

Agent 会根据需要自行搜索代码、读取文件、修改实现并执行验证。常用命令和快捷键如下：

| 输入 | 行为 |
|---|---|
| `Enter` | 提交消息 |
| `/plan` | 进入只读计划模式 |
| `/do` | 退出计划模式并立即执行已有计划 |
| `Esc` | 取消当前 Agent 回合 |
| `Ctrl+C` | 运行时取消当前回合；空闲时退出 |
| `Ctrl+D` | 退出程序 |
| `/exit`、`/quit` | 退出程序 |

### Plan / Do 示例

```text
/plan
分析认证模块的耦合点，并给出重构计划。

/do
```

Plan Mode 同时通过系统提示和工具定义限制写入：模型只能使用 `read_file`、`glob`、`grep`。
执行 `/do` 后恢复 `write_file`、`edit_file`、`bash`，并立即基于上文计划开始工作。

## 工具与调度

| 工具 | 类型 | 用途 |
|---|---|---|
| `read_file` | 只读 | 带行号读取文件 |
| `glob` | 只读 | 按 Glob 模式查找文件 |
| `grep` | 只读 | 使用正则搜索文件内容 |
| `write_file` | 有副作用 | 写入文件并创建父目录 |
| `edit_file` | 有副作用 | 对唯一匹配内容进行替换 |
| `bash` | 有副作用 | 执行 Shell 命令并返回退出码与输出 |

同一轮中的连续只读调用可以并发执行。副作用工具构成串行边界，所有结果仍按模型原始调用顺序展示并回灌。
大文件、长命令输出和大量搜索结果会被截断并明确标记。

## 安全边界

endless-code 会清理取消或超时后的异步任务和命令子进程，并对可见输出进行密钥脱敏。

当前版本**没有文件系统沙箱，也没有工具执行前审批**。模型可以访问当前用户有权限访问的路径，
并可执行 Shell 命令。请在可信代码库和权限受限的开发环境中运行，并在执行前使用 `/plan`
检查高风险任务的操作方案。

## 项目结构

```text
src/endless_code/
├── agent/              # 可取消 ReAct Agent Loop、停止条件与工具调度
├── llm/                # Provider 抽象、DeepSeek 与 OpenAI 适配器
├── tool/               # 工具协议、注册中心和六个内置工具
├── tui/                # Textual 界面、事件渲染、模式与取消交互
├── config.py           # YAML 配置加载、校验与环境变量展开
├── conversation.py     # 多轮对话与工具历史
├── prompt.py           # 系统提示、Plan Mode 与执行指令
├── security.py         # 输出脱敏与工具参数摘要
└── cli.py              # 命令行入口
```

## 开发与验证

```bash
python -m pytest -q
python -m ruff format --check .
python -m ruff check .
```

项目采用 Spec 驱动开发：

- [`spec.md`](spec.md)：需求与验收标准
- [`plan.md`](plan.md)：架构与技术设计
- [`task.md`](task.md)：实现顺序与验证步骤
- [`checklist.md`](checklist.md)：功能、集成和端到端验收

## License

MIT
