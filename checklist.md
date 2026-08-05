# Endless Code 上下文管理 Checklist

## 功能与集成

- [ ] compact 包可从 `endless_code.compact` 导入，常量集中且 session 目录自动创建。
- [ ] `Conversation.replace_history` 深拷贝输入，空列表和 `None` 不报错。
- [ ] 单条工具结果超过 50000 UTF-8 字节时落盘，预览包含字节数、头部、路径和 `read_file` 提示。
- [ ] 单条工具消息聚合超过 200000 字节时按大小落盘，剩余结果合计不超过阈值。
- [ ] 落盘失败保留原文且不写冻结账本；成功后同一 id 的决策和预览跨轮稳定。
- [ ] 摘要 prompt 不传工具定义，正式结果只保留 `<summary>`，包含九个固定章节。
- [ ] 摘要恢复段包含最近五个文件、当前实际工具 schema 和边界提示。
- [ ] 近期原文同时满足 10000 token 和 5 条消息，且不拆开 tool call/result 配对。
- [ ] usage 锚点按主对话最新 usage 替换，新增消息按字符/3.5 估算。
- [ ] 自动阈值、手动 `/compact`、紧急 PTL 路径和熔断行为符合 spec。
- [ ] PTL 摘要重试按用户分组丢弃旧消息，不发送空摘要请求。
- [ ] `/exit`、`/plan`、`/do`、`/compact` 和未知斜杠命令均不发送给 LLM；既有命令行为保持不变。
- [ ] TUI 显示自动/紧急压缩状态和前后 token，手动压缩失败不退出应用。
- [ ] Anthropic 默认 200000，OpenAI/DeepSeek 默认 128000，显式 `context_window` 优先。

## 工程检查

- [ ] `python -m compileall src` 通过。
- [ ] `ruff check .` 通过。
- [ ] `ruff format --check .` 通过。
- [ ] `python -m pytest -q` 通过，包含原有 Agent、权限、MCP、Provider 和 TUI 测试。
- [ ] 配置示例可由 `yaml.safe_load` 解析，不含真实密钥。
- [ ] `.endless-code/sessions/` 不进入 Git。

## 端到端场景

### E1：长会话

Fake Provider 连续 30 轮返回工具调用，每轮产生 30KB 结果，使用小 context window。预期 Agent 完成且至少触发一次自动摘要，Conversation 长度明显小于原始历史。

### E2：大结果和聚合

一轮返回 80KB 工具结果，下一次 Provider 请求看到稳定预览且落盘文件为 80KB；一条工具消息包含三个 80KB 结果时，至少两个被替换且剩余聚合不超过 200KB。

### E3：手动命令

输入 `/compact` 时即使 token 远低于自动阈值也发送一次无工具摘要请求，显示 token 变化；输入 `/unknown` 时仅显示命令提示；`/exit` 正常退出。

### E4：紧急压缩

主请求第一次返回 `PromptTooLongError`，预期先落盘/摘要再重试一次；重试再次 PTL 时不发第三次主请求。

### E5：恢复一致性

压缩前读取七个文件并注册多个工具。压缩后只显示最近五个文件，工具名和 schema 与下一次 Request 完全一致，用户原文仍可在摘要恢复内容中定位。

### E6：真实启动

复制 `.endless-code/config.yaml.example` 并填入本机密钥，执行 `python -m endless_code`，能进入 Provider 选择或会话界面；配置三个 provider 时列表显示全部配置项。
