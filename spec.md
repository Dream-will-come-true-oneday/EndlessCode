# 系统提示工程化 Spec

## 背景

`endless-code` 已具备可取消的多轮 Agent Loop、Plan Mode，以及 Anthropic、DeepSeek/OpenAI 的流式工具调用能力；但当前系统提示仍以单个固定字符串维护，环境上下文和模式提醒通过零散的字符串拼接传递。稳定指令、工具定义、环境信息和临时提醒没有清晰边界，难以扩展，也无法稳定利用 OpenAI 兼容端点的前缀缓存。

本阶段将系统提示工程化：把稳定指令模块化，把随请求变化的环境信息和提醒移出稳定前缀，并在不破坏既有 Agent Loop 的前提下，为 Anthropic、DeepSeek 与 OpenAI 提供一致的请求装配和缓存用量观测能力。

## 目标

- 将系统级指令拆分为有名称和优先级的模块，形成确定的稳定系统提示。
- 将稳定指令与工具定义保持为跨轮不变的请求前缀；环境信息、历史和临时提醒按轮动态构造。
- 为模型提供当前工作目录、平台、日期、Git 状态、应用版本和模型名称等安全的运行环境信息。
- 通过不写入持久历史的 `<system-reminder>` 机制注入临时系统补充指令。
- 让 Plan Mode 按 Agent 轮次注入完整或精简提醒，同时继续只开放只读工具。
- 从 Anthropic、DeepSeek/OpenAI 返回的可用 usage 字段解析缓存命中信息，并保持字段缺失时的兼容性。

## 功能需求

- F1: 模块化稳定系统提示
  系统提示由按优先级排列的模块拼装，模块间以空行分隔。固定模块包括：身份、系统约束、任务模式、动作执行、工具使用、语气风格和文本输出。保留自定义指令、已激活 Skill、长期记忆三个空模块位置；空内容必须跳过，不留下多余分隔符。新增模块不应要求修改拼装逻辑。

- F2: 环境信息
  每个 Agent 回合构造独立环境信息段，包含工作目录、平台、当前日期、Git 状态、应用版本和当前模型。该段与稳定系统提示逻辑分离，并且不得包含 API 密钥或环境变量值。

- F3: 稳定前缀与缓存友好请求
  稳定系统提示和当前工具定义必须保持确定的顺序与字节内容，使其可作为 Anthropic、DeepSeek/OpenAI 兼容请求的稳定前缀。Anthropic 请求在稳定系统块上设置显式缓存断点；OpenAI 兼容端点依赖稳定前缀缓存。环境信息、持久会话历史和本轮 reminder 必须位于动态部分，不能改变稳定模块本身。项目不依赖任何特定端点一定支持前缀缓存。

- F4: 缓存用量观测
  Usage 对外暴露输入、输出、缓存写入和缓存读取数量。Anthropic 解析 `cache_creation_input_tokens` 与 `cache_read_input_tokens`；OpenAI 解析 `prompt_tokens_details.cached_tokens`；DeepSeek 解析其可用的 prompt cache 命中字段。字段缺失、为空或端点不支持时以零处理，不中断会话。缓存写入只有端点提供明确字段时才报告。

- F5: 关键工具约定双重强化
  系统提示的工具使用模块与对应工具描述都必须强调：优先使用专用读写搜索工具，而不是用 `bash` 拼凑；编辑文件前必须先读取目标内容。

- F6: 补充消息注入
  系统能以 `<system-reminder>...</system-reminder>` 包裹临时补充指令，并将其加入本轮 provider 请求。该提醒不能写入 `Conversation` 的持久历史，不得被作为普通用户问题展示或回显。

- F7: Plan Mode 轮次提醒
  `/plan` 后继续只向模型提供 `read_file`、`glob`、`grep`。首轮注入完整 Plan 提醒，固定间隔轮次再次注入完整提醒，其余轮次注入精简提醒；`/do` 恢复完整工具集，并以执行指令开始下一回合。

- F8: 三协议一致性
  Anthropic、DeepSeek 与 OpenAI Provider 使用相同的稳定系统提示、环境信息和 reminder 语义；三者都保持工具调用、流式文本、usage、取消和历史回灌的既有行为。允许各端点在缓存字段和缓存写入能力上不同。

- F9: Provider 配置
  配置文件支持 `protocol: anthropic`、`protocol: deepseek` 和 `protocol: openai`。Anthropic 使用 `ANTHROPIC_API_KEY`、默认官方 base URL 和配置的 model；OpenAI 使用 `OPENAI_API_KEY`、可选 `base_url` 和配置的 model；DeepSeek 保持现有配置。多 Provider 选择和缺失环境变量降级行为保持不变。

## 非功能需求

- N1: 缓存确定性
  在相同工具集下，多轮请求的稳定系统提示和工具定义顺序必须一致；环境、日期、轮次和 reminder 不得混入稳定部分。

- N2: 既有行为不回退
  多轮 Agent Loop、保序工具调度、取消、流错误恢复、历史一致性、TUI 模式切换、Token 统计和密钥脱敏必须继续工作。

- N3: 历史合法性
  动态 reminder 不持久化，不得造成工具调用/结果配对异常或使后续请求的消息角色序列非法。

- N4: 环境采集可降级
  Git 信息不可用、目录不是 Git 仓库或平台信息读取失败时，环境信息应省略对应部分而不是阻塞或终止请求。

- N5: 安全性
  环境信息、日志、TUI 和错误输出均不得泄露 API 密钥或敏感环境变量值。

- N6: 兼容性与工程质量
  项目在声明的 Python 与 Textual 版本范围内运行；格式检查、静态检查和自动化测试通过。

## 不做的事

- 不做项目重命名、目录迁移或新的模型协议；本阶段仅新增 Anthropic，并保留 DeepSeek 与 OpenAI（含兼容 base_url）。
- 不加载 `CLAUDE.md`、项目指令文件、长期记忆或真实 Skill 内容；相关模块仅保留为空的扩展位置。
- 不增加 MCP、自动评测、上下文压缩、缓存 TTL 配置或缓存状态栏展示。
- 不承诺所有 Anthropic、OpenAI 兼容端点或 DeepSeek 模型均返回缓存统计或提供缓存命中。
- 不改变工具权限模型，不新增沙箱或工具执行审批。

## 验收标准

- AC1: 固定模块按优先级稳定拼装，空的可选模块被跳过；新增测试模块可在不修改拼装逻辑的情况下参与输出。(F1)
- AC2: 每次请求均含独立环境信息，至少可观察到工作目录、平台、日期、版本和模型；Git 信息不可用时请求仍可正常构造。(F2/N4)
- AC3: 相同工具集的两轮请求中，稳定系统提示和工具定义序列完全一致；只改变环境或 reminder 不影响稳定部分。(F3/N1)
- AC4: Anthropic、DeepSeek/OpenAI Provider 在各自可用字段存在时将缓存读写值写入 Usage；字段缺失时 Usage 的缓存值为零且流不中断。(F4)
- AC5: 工具使用系统提示与 `bash`、`edit_file` 描述都可观察到“专用工具优先”和“编辑前先读”的约定。(F5)
- AC6: reminder 使用 `<system-reminder>` 标签进入当轮请求，且不会出现在 `Conversation.messages()` 的持久历史中。(F6/N3)
- AC7: Plan Mode 首轮与间隔轮次使用完整 reminder，其余轮次使用精简 reminder；其工具集始终只读，`/do` 后恢复全部工具。(F7)
- AC8: 使用 FakeProvider 覆盖多轮工具、取消、流错误和 Plan/Do 场景后，既有 Agent/TUI 历史和事件行为仍通过。(F8/N2/N3)
- AC9: Anthropic、DeepSeek/OpenAI 的请求装配在系统提示、环境信息、reminder 和缓存 usage 行为上具有一致语义；已有协议差异不导致错误，OpenAI 兼容 `base_url` 可用。(F8/F9)
- AC10: `ruff format --check .`、`ruff check .`、`python -m compileall -q src` 与全量 pytest 均通过，且测试输出与可见 UI 文本不含测试密钥原文。(N5/N6)
