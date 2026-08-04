# 系统提示工程化 Checklist

## Prompt 与环境

- [ ] 固定模块按 priority 升序拼装，模块间只有一个空行（验证：运行 python -m pytest tests/test_prompt.py -q -k 'module or assemble'，断言七个固定模块顺序正确）。(AC1/F1)
- [ ] 三个可选空模块不会出现在输出或产生多余空行（验证：运行 python -m pytest tests/test_prompt.py -q -k optional，断言空 content 被跳过）。(AC1/F1)
- [ ] 新增测试模块无需修改装配函数即可参与输出（验证：运行 python -m pytest tests/test_prompt.py -q -k extension，断言注入模块按 priority 出现）。(AC1/F1)
- [ ] 环境段包含工作目录、平台、日期、版本和模型（验证：运行 python -m pytest tests/test_prompt.py -q -k environment，断言 render 文本包含各字段）。(AC2/F2)
- [ ] 非 Git 目录、Git 命令失败或超时时环境采集可降级（验证：运行 python -m pytest tests/test_prompt.py -q -k git_fallback，断言 git_status 为空且函数返回）。(AC2/N4)
- [ ] 环境段不包含模拟 API key 或环境变量值（验证：运行 python -m pytest tests/test_prompt.py tests/test_security.py -q -k 'environment or redact'，断言原始 key 不在渲染文本）。(N5)

## 稳定前缀与缓存

- [ ] 相同工具集的多次装配得到逐字节相同的 stable system（验证：运行 python -m pytest tests/test_prompt.py -q -k stable，断言两次值相等）。(AC3/F3/N1)
- [ ] 环境、轮次和 reminder 的变化不改变 stable system 或工具定义顺序（验证：运行 python -m pytest tests/test_agent.py -q -k stable_prefix，断言 Request.system.stable 和 Request.tools 未变化）。(AC3/F3/N1)
- [ ] Anthropic 请求稳定 system 块含 cache_control.type=ephemeral，环境块没有 cache_control（验证：运行 python -m pytest tests/test_anthropic_provider.py -q -k cache_control，断言 payload）。(AC4/F3)
- [ ] Anthropic usage 解析缓存创建和读取 token（验证：运行 python -m pytest tests/test_anthropic_provider.py -q -k usage，断言 cache_write/cache_read 精确值）。(AC4/F4)
- [ ] OpenAI usage 解析 prompt_tokens_details.cached_tokens，缺字段为零（验证：运行 python -m pytest tests/test_llm.py -q -k 'openai and cache'，断言两条分支）。(AC4/F4)
- [ ] DeepSeek 可用 prompt cache 字段被解析，缺字段为零（验证：运行 python -m pytest tests/test_llm.py -q -k 'deepseek and cache'，断言两条分支）。(AC4/F4)
- [ ] 缓存 usage 经 Agent Event 对外透传（验证：运行 python -m pytest tests/test_agent.py -q -k cache_usage，断言 input/output/cache_write/cache_read）。(AC4/F4)

## 工具约定与 reminder

- [ ] 工具使用系统提示强调专用工具优先和编辑前先读（验证：运行 python -m pytest tests/test_prompt.py -q -k tool_conventions，断言两条约定）。(AC5/F5)
- [ ] bash 与 edit_file 的工具描述具有同样的关键约定（验证：运行 python -m pytest tests/test_tool.py -q -k description，断言 description 文本）。(AC5/F5)
- [ ] 每个 reminder 以 system-reminder 标签包裹（验证：运行 python -m pytest tests/test_prompt.py -q -k reminder，断言开闭标签和正文）。(AC6/F6)
- [ ] reminder 只进入 Request，不写入 Conversation 持久历史（验证：运行 python -m pytest tests/test_agent.py -q -k reminder_history，断言 messages 中没有 reminder）。(AC6/F6/N3)
- [ ] Plan Mode 首轮使用完整 reminder（验证：运行 python -m pytest tests/test_agent.py -q -k plan_reminder，断言第 1 轮内容完整）。(AC7/F7)
- [ ] Plan Mode 的第 2 轮使用精简 reminder，固定间隔轮次恢复完整 reminder（验证：运行 python -m pytest tests/test_agent.py -q -k plan_reminder，断言 1、2、5 轮内容）。(AC7/F7)
- [ ] Plan Mode 仅导出 read_file、glob、grep，/do 后恢复六个工具（验证：运行 python -m pytest tests/test_agent.py tests/test_tui.py -q -k 'plan_mode or do_command'）。(AC7/F7)

## Provider 与配置

- [ ] 配置接受 anthropic、deepseek、openai 三种 protocol（验证：运行 python -m pytest tests/test_config.py -q -k protocols）。(F9)
- [ ] Anthropic 默认官方 base_url，自定义 base_url 生效（验证：运行 python -m pytest tests/test_config.py -q -k anthropic）。(F9)
- [ ] OpenAI 的自定义 base_url 和 DeepSeek 默认 base_url 保持可用（验证：运行 python -m pytest tests/test_config.py tests/test_llm.py -q -k 'base_url or deepseek'）。(F9)
- [ ] 缺失 API key 环境变量只产生结构化 ConfigError，不泄露变量值（验证：运行 python -m pytest tests/test_config.py tests/test_tui.py -q -k 'missing or provider_error'）。(F9/N5)
- [ ] Anthropic 正确映射文本、工具调用、工具结果和 reminder，且流关闭（验证：运行 python -m pytest tests/test_anthropic_provider.py -q）。(F8)
- [ ] OpenAI 与 DeepSeek 以 Request 接口保持相同 stable/environment/reminder 语义（验证：运行 python -m pytest tests/test_llm.py -q）。(F8)
- [ ] Anthropic、DeepSeek、OpenAI Provider 工厂都能构造对应适配器（验证：运行 python -m pytest tests/test_config.py tests/test_llm.py -q -k provider_factory）。(F8/F9)

## Agent、TUI 与历史回归

- [ ] 多轮工具任务仍自动推进，并把 tool result 带入下一轮（验证：运行 python -m pytest tests/test_agent.py -q -k multi_round）。(AC8/N2)
- [ ] 流式取消、工具取消和流错误后历史末尾为 assistant，并可继续对话（验证：运行 python -m pytest tests/test_agent.py -q -k 'cancel or stream_error or history'）。(AC8/N2/N3)
- [ ] 批量只读工具并发、写工具串行且结果按调用顺序回灌（验证：运行 python -m pytest tests/test_agent.py -q -k batch）。(N2)
- [ ] TUI 在三 Provider 配置和新 Agent 构造参数下可挂载、选择 Provider、进入空闲状态（验证：运行 python -m pytest tests/test_tui.py -q -k 'mount or provider_error'）。(N2)
- [ ] TUI 的 /plan、/do、Esc、Ctrl+C、usage 和 iteration 行为不退化（验证：运行 python -m pytest tests/test_tui.py -q -k 'plan or do_command or escape or ctrl_c or usage or iteration'）。(N2)
- [ ] 所有可见文本继续脱敏（验证：运行 python -m pytest tests/test_security.py tests/test_tui.py -q -k 'redact or redaction'，断言原始模拟 key 未出现）。(N5)

## 工程检查

- [ ] Python 源码可编译（验证：运行 python -m compileall -q src，退出码为 0）。(AC10)
- [ ] Ruff 格式检查通过（验证：运行 python -m ruff format --check .，输出已格式化）。(AC10)
- [ ] Ruff 静态检查无告警（验证：运行 python -m ruff check .，输出 All checks passed）。(AC10)
- [ ] 全量自动化测试通过（验证：运行 python -m pytest -q，失败数为 0）。(AC10)
- [ ] 取消和 Provider 流无 ResourceWarning 或 pending task 警告（验证：运行 python -m pytest -q -W error::ResourceWarning，退出码为 0）。(AC10/N2)
- [ ] 依赖关系一致（验证：在隔离虚拟环境运行 python -m pip check，输出 No broken requirements found）。(AC10/N6)
- [ ] Git diff 无空白错误且不包含真实密钥（验证：运行 git diff --check；运行 git status --short .endless-code/config.yaml 无输出）。(AC10/N5)

## 端到端

- [ ] Anthropic 缓存场景：使用有效 Anthropic 配置连续发起两轮 smoke 请求（验证：运行 python examples/smoke.py --provider anthropic，首轮在端点支持时显示 cache_write，大于零；次轮显示 cache_read，大于零；端点不返回字段时显示 0 且不失败）。(AC4/F8)
- [ ] OpenAI 官方或兼容端点场景：使用有效 OpenAI 配置与可选 base_url 连续运行 smoke（验证：运行 python examples/smoke.py --provider openai，输出两轮 usage，存在 cached_tokens 时显示 cache_read）。(AC4/F8/F9)
- [ ] DeepSeek 场景：使用有效 DeepSeek 配置连续运行 smoke（验证：运行 python examples/smoke.py --provider deepseek，输出两轮 usage，缓存字段缺失不影响完成）。(AC4/F8)
- [ ] Plan 到执行场景：在 headless TUI 使用 FakeProvider 输入 /plan、任务、/do（验证：运行 python -m pytest tests/test_tui.py -q -k plan_and_do，检查只读工具、写工具和历史）。(AC7/AC8)
- [ ] 提醒历史场景：Plan 多轮后取消，再追加普通用户消息（验证：运行 python -m pytest tests/test_agent.py -q -k 'reminder_history and cancel'，断言后续回合正常完成）。(AC6/AC8)