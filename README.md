# endless-code

一个基于 Textual TUI 的命令行 AI 对话助手，支持流式多轮对话，可切换 OpenAI 与 DeepSeek 等多种供应商。

## 特性

- 终端内交互式对话界面，回复以流式逐字显示（打字机效果）
- 多供应商支持：OpenAI、DeepSeek
- 多轮对话，进程内保留上下文记忆
- 助手回复支持 Markdown 渲染
- 扩展思考模式（DeepSeek，通过 `thinking` 开关）

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
├── cli.py              # CLI 入口：加载配置、打印 banner、启动 TUI
├── config.py           # 配置层：加载、校验、环境变量展开
├── conversation.py     # 会话层：进程内多轮历史管理
├── prompt.py           # banner 等提示文本渲染
├── llm/
│   ├── __init__.py     # Provider 抽象接口、统一数据结构
│   ├── openai_provider.py
│   └── deepseek_provider.py
└── tui/
    ├── __init__.py
    └── app.py           # Textual TUI 主应用、状态机、对话逻辑
```

## 开发流程

本项目采用 Spec 驱动开发，四份递进文档依次细化并经审批：

- [`spec.md`](spec.md) —— 做什么
- [`plan.md`](plan.md) —— 怎么做
- [`task.md`](task.md) —— 按什么顺序做
- [`checklist.md`](checklist.md) —— 做对了没

## License

MIT
