# Endless Code 上下文管理 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/endless_code/compact/__init__.py` | 导出公共接口 |
| 新建 | `src/endless_code/compact/const.py` | 阈值常量 |
| 新建 | `src/endless_code/compact/state.py` | 会话和并发状态 |
| 新建 | `src/endless_code/compact/token.py` | token 估算 |
| 新建 | `src/endless_code/compact/layer1.py` | 工具结果落盘与预览 |
| 新建 | `src/endless_code/compact/summary_prompt.py` | 摘要 prompt 和解析 |
| 新建 | `src/endless_code/compact/recovery.py` | 恢复段 |
| 新建 | `src/endless_code/compact/layer2.py` | 摘要、PTL 重试和熔断 |
| 新建 | `src/endless_code/compact/compact.py` | 上下文编排 |
| 修改 | `src/endless_code/config.py` | context window 字段和默认值 |
| 修改 | `src/endless_code/conversation.py` | 深拷贝替换历史、长度接口 |
| 修改 | `src/endless_code/llm/__init__.py` | PTL 哨兵 |
| 修改 | `src/endless_code/llm/anthropic_provider.py` | Anthropic PTL 包装 |
| 修改 | `src/endless_code/llm/openai_provider.py` | OpenAI 兼容 PTL 包装 |
| 修改 | `src/endless_code/agent/__init__.py` | runtime、上下文接入、紧急重试、压缩事件 |
| 修改 | `src/endless_code/tui/app.py` | runtime、命令和压缩状态渲染 |
| 新建 | `src/endless_code/tui/commands.py` | 内置命令注册表 |
| 修改 | `src/endless_code/cli.py` | 进程级 SessionRuntime |
| 修改 | `.endless-code/config.yaml.example` | context_window 示例 |
| 修改 | `.gitignore` | 忽略 sessions |
| 新建 | `tests/test_compact.py` | compact 核心和集成测试 |
| 修改 | `tests/test_config.py`、`tests/test_conversation.py`、`tests/test_agent.py`、`tests/test_tui.py` | 新接口回归测试 |

## T1：建立状态和常量

**依赖：** 无

实现 `const.py`、`state.py`、`__init__.py`。使用 UTF-8 字节计量，创建 `.endless-code/sessions/<id>/tool-results`，实现决策冻结、文件快照和自动熔断器。

**验证：** `python -c "from endless_code.compact import new_session_context"` 成功，临时目录存在且 session id 唯一。

## T2：实现 token 估算和 Conversation 替换

**依赖：** T1

给 `Conversation` 增加 `length()`、`replace_history()`，使用 `copy.deepcopy`；实现 usage 锚点和消息增量估算。

**验证：** `pytest tests/test_conversation.py tests/test_compact.py -k 'conversation or token'` 通过。

## T3：实现第一层工具结果压缩

**依赖：** T1、T2

实现 `spill_single`、`build_preview`、`offload_and_snip`。返回新消息列表，不修改入参；处理单条阈值、消息聚合阈值、落盘失败降级和冻结账本。

**验证：** 60000 字节结果会生成同名落盘文件和稳定预览；三条 80000 字节结果的剩余聚合不超过 200000 字节。

## T4：实现摘要 prompt 和恢复段

**依赖：** T1、T2

实现 `summary_prompt.py` 和 `recovery.py`，包括确定性序列化、九节摘要模板、`<summary>` 提取、五文件上限、5000 token 头部截断、工具 schema 和边界提示。

**验证：** 同一消息和工具定义连续渲染结果逐字节一致。

## T5：实现近期尾部和摘要 PTL 重试

**依赖：** T4

实现 `pick_recent_tail`、tool call/result 配对修正、`group_by_user_turn`、`summarize_once` 和按规则丢组的 `ptl_retry`。

**验证：** 摘要请求不含工具；连续 PTL 时最多按规则重试且不发送空消息。

## T6：实现上下文编排

**依赖：** T3、T5

实现 `manage_context`、自动摘要、强制摘要、熔断和 Conversation 替换。AUTO 先第一层再按新估算判断，MANUAL 跳过第一层和阈值，EMERGENCY 先第一层再强制摘要。

**验证：** 自动阈值触发摘要，低于阈值不触发，连续三次失败后仅自动路径跳过。

## T7：接入配置和 Provider PTL

**依赖：** T2、T6

追加 `context_window` 和 `effective_context_window`；增加 `PromptTooLongError`；Anthropic/OpenAI 兼容 Provider 将典型上下文超限 SDK 错误包装为该哨兵，其他错误保持原样。

**验证：** 旧 YAML 可加载；三个协议默认窗口正确；Provider 单测能区分 PTL 和普通异常。

## T8：接入 Agent runtime 和主循环

**依赖：** T6、T7

新增 `SessionRuntime` 和压缩事件。Agent 每轮只计算一次工具定义并复用；请求前调用 AUTO；捕获 PTL 后执行 EMERGENCY 并最多重试主请求一次；主对话 usage 更新锚点；成功 `read_file` 内容写入恢复状态。

**验证：** 现有 Agent ReAct、工具批处理、取消和权限测试继续通过；新增 fake Provider 场景覆盖自动和紧急压缩。

## T9：接入 TUI 和 CLI

**依赖：** T8

新增 `BUILTIN_COMMANDS`，迁移 `/exit`、`/plan`、`/do` 并加入 `/compact` 和未知命令提示。TUI 保存 runtime，渲染压缩开始/结束事件，手动压缩与 Agent run 通过锁串行。CLI 为每次进程启动建立 session context。

**验证：** Textual `run_test` 中 `/compact` 不增加用户消息、不调用普通 run；`/unknown` 不调用 Provider；启动后可显示实际配置中的全部 provider。

## T10：补充测试和工程检查

**依赖：** T1-T9

补充状态、token、layer1、摘要恢复、PTL、配置、Agent、TUI 测试；修正 `.gitignore` 和示例配置。

**验证：** `python -m compileall src`、`ruff check .`、`ruff format --check .` 和 `python -m pytest -q` 均通过。

## 执行顺序

```text
T1 -> T2 -> T3 -> T4 -> T5 -> T6 -> T7 -> T8 -> T9 -> T10
```
