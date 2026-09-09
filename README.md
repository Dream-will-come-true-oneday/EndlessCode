# Endless Code

> 面向多模型的终端智能编程助手

Endless Code 是一个运行在终端中的智能编程助手，让 AI 代理自动完成代码分析、修改和验证的完整工作流。

**核心特性：**
- 🔄 **多轮自动化**：自动执行"分析 → 调用工具 → 读取结果 → 继续行动"的完整流程
- 🛡️ **安全优先**：五层权限拦截 + 危险命令黑名单 + 项目沙箱隔离
- 🔌 **MCP 扩展**：支持通过 MCP 协议接入无限远端工具
- 💾 **智能记忆**：跨会话记忆管理，自动上下文压缩

---

## 🚀 快速开始（2 分钟上手）

### 前提条件
- ✅ Python 3.12+

### 安装与运行

```bash
# 1. 克隆仓库
git clone https://github.com/Dream-will-come-true-oneday/Endless-Coding.git
cd Endless-Coding

# 2. 安装依赖
python -m pip install -e .

# 3. 创建配置文件（PowerShell）
Copy-Item .endless-code/config.yaml.example .endless-code/config.yaml
```

### 配置 API Key

推荐使用环境变量（不要写入配置文件）：

**PowerShell:**
```powershell
$env:ANTHROPIC_API_KEY = "your-key-here"
# 或使用 OpenAI/DeepSeek
$env:OPENAI_API_KEY = "your-key-here"
```

**Bash/macOS/Linux:**
```bash
export ANTHROPIC_API_KEY="your-key-here"
```

### 启动项目

```bash
endless-code
```

首次启动后可选择支持的 Provider（Anthropic/OpenAI/DeepSeek）并开始对话！

---

## 💬 常用操作

| 输入 | 功能 |
|------|------|
| `任务描述` | Agent 自动分析和执行（如：*修复测试失败*） |
| `/plan` | 只读模式，仅调查不修改 |
| `/do` | 执行计划模式的调查结论 |
| `Ctrl+C` | 取消当前操作 |
| `Ctrl+D` 或 `/quit` | 退出程序 |

**示例指令：**
```
定位这个项目的测试失败原因，修复后运行相关测试。
```

---

## 📚 文档索引

- [MCP 工具扩展](docs/mcp/mcp-servers.example.yaml) - 接入远端工具
- [Spec 驱动开发文档](spec.md) - 需求与设计规范

---

### 多 Provider 配置

编辑 `.endless-code/config.yaml` 可同时配置多个模型服务商：

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
```

启动后可自由选择要使用的模型。

### OpenAI 兼容 API 与自定义模型

任何兼容 OpenAI API 的服务（DeepSeek、Moonshot、本地 vLLM/Ollama 等）都可以通过 `protocol: openai` + `base_url` 接入，无需改代码。

**关键字段：**

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 是 | 显示名称，可自定义（启动时用于选择模型） |
| `protocol` | 是 | `anthropic` / `openai` / `deepseek`（兼容 OpenAI API 的服务填 `openai`） |
| `model` | 是 | 模型名，由服务商决定 |
| `api_key` | 是 | 推荐使用 `$VAR_NAME` 环境变量引用，密钥不落盘 |
| `base_url` | 否 | 兼容端点地址；`protocol: openai` 时省略则使用 OpenAI 官方端点 |

**示例：接入 DeepSeek 与本地模型**

```yaml
providers:
  - name: deepseek
    protocol: openai                     # 走 OpenAI 兼容协议
    model: deepseek-chat
    base_url: https://api.deepseek.com
    api_key: $DEEPSEEK_API_KEY

  - name: local-llm                      # 本地 Ollama / vLLM 等
    protocol: openai
    model: qwen2.5-coder
    base_url: http://localhost:11434/v1
    api_key: $LOCAL_API_KEY
```

接入新服务只需替换 `base_url` 和 `model` 两项。

**密钥管理建议：** `api_key` 始终使用 `$VAR_NAME` 引用环境变量（如上方“配置 API Key”一节），不要将明文密钥写入配置文件或提交到仓库。

更多信息请查看:
- [完整配置说明](.endless-code/config.yaml.example)
- [MCP 服务配置](docs/mcp/mcp-servers.example.yaml)

---

## 👨‍💻 开发者

项目采用 Spec 驱动开发：

- [`spec.md`](spec.md)：需求与验收标准
- [`plan.md`](plan.md)：架构与技术设计

### 运行测试

```bash
python -m pytest -q
```

### 评测体系

项目采用“确定性测试为主、真实模型评测为辅”的评测方式：日常套件零 API 花费、完全可复现；真实模型评测可选启用，仅用于质量观测。

**确定性测试（每次提交必跑）**

| 层 | 覆盖内容 |
|------|------|
| 权限红队矩阵 | 44 个攻击/合法用例覆盖五层拦截，高危漏报一票否决 |
| E2E 场景 | 命令系统、会话恢复/压缩/清除、MCP 真实子进程连接 |
| Golden 快照 | 系统提示词、审计记录等文本契约回归 |
| 稳定性注入 | 磁盘满、Provider 流中断、连续取消等异常不崩溃 |
| 性能基线 | 10 轮工具调用循环的墙钟时间与 token 成本 |
| 覆盖率门禁 | pytest-cov 阈值 85%，低于即失败 |

**指标汇总与外部看板**

```bash
python -m pytest -q        # 全量测试（带覆盖率）
python evals/runner.py     # 汇总指标，生成 evals/reports/dashboard.html 看板
```

**真实模型评测（可选，需要 API key）**

```bash
export DEEPSEEK_API_KEY="your-key-here"   # 仅通过环境变量注入，勿提交密钥
python evals/llm_eval.py
```

以 LLM-as-judge 方式检查三项质量指标：**压缩保留率**（长历史压缩后关键事实是否还在）、**输出一致性**（同提示词重复执行的稳定性）、**成本基线**（token 用量偏差超 ±20% 告警）。单次全量预算约 17 次调用，结果同样汇入看板。

---

## License

MIT
