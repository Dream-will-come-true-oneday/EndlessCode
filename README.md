# endless-code

一个基于 Textual TUI 的命令行 AI 编程助手，支持流式多轮对话与工具调用，能读写文件、执行命令、搜索代码——从聊天机器人到能干活的 Agent。

## 特性

- 终端内交互式对话界面，回复以流式逐字显示
- 多供应商支持：OpenAI、DeepSeek（及任何 OpenAI 兼容端点）
- 多轮对话，进程内保留上下文记忆
- **工具系统**：模型可自主调用 6 个核心工具（read_file、write_file、edit_file、bash、glob、grep）
- **单轮闭环**：模型请求工具 → 执行 → 结果回灌 → 最终答复
- Claude Code 风格工具行呈现（`● read_file(path)` + 结果摘要）
- 结构化错误处理，工具失败不中断会话
- 助手回复支持 Markdown 渲染
- 扩展思考模式（DeepSeek）

## 安装

```bash
git clone <repo-url>
cd endless-code
pip install -e .
```

要求 Python 3.12 及以上版本。

## 配置

1. 复制示例配置：

```bash
cp .endless-code/config.yaml.example .endless-code/config.yaml
```

2. 编辑 `.endless-code/config.yaml`，填入你的 API 密钥：

```yaml
providers:
  - name: deepseek
    protocol: deepseek
    model: deepseek-chat
    base_url: https://api.deepseek.com
    api_key: $DEEPSEEK_API_KEY   # 支持环境变量引用或明文
    thinking: false              # 是否启用扩展思考（仅 DeepSeek 生效）

  - name: openai
    protocol: openai
    model: gpt-4o
    api_key: $OPENAI_API_KEY     # base_url 可省略，默认使用官方端点
    thinking: false              # OpenAI 忽略此字段
```

`api_key` 支持环境变量引用（`$VAR_NAME`）或明文。推荐使用环境变量引用以避免泄露密钥。

配置文件按以下顺序查找：

1. 当前目录 `.endless-code/config.yaml`
2. 用户目录 `~/.config/endless-code/config.yaml`

> 注意：配置中没有 `active` 字段。若只配置了一个供应商则自动启用；若配置了多个，启动时会出现选择列表，输入编号选择即可。

## 使用

```bash
# 直接运行
endless-code

# 或通过 python 模块运行
python -m endless_code
```

启动后：

- 单供应商：直接进入对话界面
- 多供应商：先显示编号列表，输入编号选择供应商后进入对话

### 快捷键 / 命令

- `Enter` —— 提交消息
- `Ctrl`+`D` —— 退出程序
- `/exit` 或 `/quit` —— 退出程序

## 支持的供应商

| 供应商 | protocol | 说明 |
|--------|----------|------|
| OpenAI | `openai` | GPT-4o 等模型，`base_url` 可省略 |
| DeepSeek | `deepseek` | 支持 `thinking: true` 启用扩展思考 |

## 项目结构

```
src/endless_code/
├── cli.py              # CLI 入口：加载配置、构造工具注册中心、启动 TUI
├── config.py           # 配置层：加载、校验、环境变量展开
├── conversation.py     # 会话层：进程内多轮历史管理（含工具调用回合）
├── prompt.py           # 系统提示词与启动 banner
├── llm/
│   ├── __init__.py     # Provider 抽象接口、ToolCall/ToolResult/ToolDefinition
│   ├── openai_provider.py   # OpenAI 适配器（含工具调用解析与回灌）
│   └── deepseek_provider.py # DeepSeek 适配器
├── tool/
│   ├── __init__.py     # Tool Protocol、Registry、Result、new_default_registry
│   ├── read_file.py    # 读文件（带行号）
│   ├── write_file.py   # 写文件（自动创建父目录）
│   ├── edit_file.py    # 唯一匹配替换
│   ├── bash.py         # 执行 shell 命令（带超时）
│   ├── glob_tool.py    # 按模式查找文件
│   └── grep_tool.py    # 正则搜索文件内容
├── agent/
│   └── __init__.py     # Agent 单轮闭环编排
└── tui/
    ├── __init__.py
    └── app.py          # Textual TUI 主应用、工具行渲染
```

## 开发流程

本项目采用 Spec 驱动开发，四份递进文档依次细化并经审批：

- [`spec.md`](spec.md) —— 做什么
- [`plan.md`](plan.md) —— 怎么做
- [`task.md`](task.md) —— 按什么顺序做
- [`checklist.md`](checklist.md) —— 做对了没

## License

MIT
