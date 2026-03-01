# 多协议 LLM 终端对话客户端 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [ ] 配置层已实现（验证：`python -c "from endless_code.config import load; load('.endless-code/config.yaml.example')"`，加载成功）
- [ ] Prompt 层已实现（验证：`python -c "from endless_code.prompt import SYSTEM_PROMPT, render_banner; print(render_banner('0.1.0', '/tmp'))"`，输出正确）
- [ ] LLM 层 Provider Protocol 已定义（验证：`python -c "from endless_code.llm import Provider, Message, StreamEvent"`，导入成功）
- [ ] DeepSeek 适配器已实现（验证：创建 DeepSeekProvider 实例，调用 stream() 方法返回 AsyncIterator）
- [ ] OpenAI 适配器已实现（验证：创建 OpenAIProvider 实例，调用 stream() 方法返回 AsyncIterator）
- [ ] Provider 工厂函数已实现（验证：`new_provider()` 根据 protocol 返回正确的适配器实例）
- [ ] Conversation 类已实现（验证：add_user/add_assistant/messages 方法正常工作）
- [ ] TUI App 已实现（验证：`EndlessCodeApp([...]).run()` 启动成功，显示界面）
- [ ] CLI 入口已实现（验证：运行 `python -m endless_code`，程序启动）

## 功能验收（对应 spec.md 的 AC1-AC8）

### AC1: 配置加载和校验

- [ ] 正常配置加载成功（验证：创建有效的 config.yaml，运行程序不报错）
- [ ] 配置文件不存在时报错并退出（验证：删除 config.yaml，运行程序，显示错误信息并 exit(1)）
- [ ] 配置字段缺失时报错并退出（验证：删除 api_key 字段，运行程序，显示具体错误信息）
- [ ] protocol 非法时报错并退出（验证：设置 protocol: "invalid"，运行程序，显示错误信息）

### AC2: TUI 启动和 Provider 选择

- [ ] 单 provider 时直接进入对话（验证：配置 1 个 provider，启动后直接显示输入框，无选择界面）
- [ ] 多 provider 时显示选择列表（验证：配置 2+ providers，启动后显示选择界面，列出所有 provider 名称和模型）
- [ ] 选择 provider 后进入对话（验证：在选择界面按 Enter，进入对话界面，状态栏显示选定的 provider）

### AC3: 对话输入和提交

- [ ] 输入框接收文本（验证：在输入框输入文字，文字正常显示）
- [ ] Enter 提交消息（验证：输入消息并按 Enter，消息显示在对话区）
- [ ] Alt+Enter 插入换行（验证：按 Alt+Enter，输入框换行而不提交）
- [ ] 提交后输入框清空（验证：提交消息后，输入框内容清空）

### AC4: 流式回复显示

- [ ] 提交消息后显示 loading 状态（验证：提交消息，状态栏显示 "Imagining… (Ns)"）
- [ ] 模型回复逐字显示（验证：观察回复过程，文字逐渐出现而非一次性显示）
- [ ] 回复完成后渲染为 Markdown（验证：回复包含 **粗体**、`代码`、列表等，完成后正确渲染）
- [ ] 计时器显示正确（验证：流式过程中，状态栏秒数递增，完成后停止）

### AC5: 多轮对话

- [ ] 第二轮对话包含上下文（验证：提问 "我叫张三"，回复确认；再问 "我叫什么"，回复包含 "张三"）
- [ ] 历史消息正确维护（验证：连续 3 轮对话，每轮回复都基于之前的上下文）

### AC6: Thinking 模式

- [ ] DeepSeek provider 启用 thinking 时正常工作（验证：配置 deepseek provider，thinking: true，提交复杂问题，模型返回回复）
- [ ] OpenAI provider 忽略 thinking 配置（验证：配置 openai provider，thinking: true，程序正常运行，无错误）
- [ ] Thinking 内容不显示在对话区（验证：DeepSeek 启用 thinking，对话区只显示最终回复，不显示思考过程）

### AC7: 错误处理

- [ ] API 调用失败时显示错误（验证：使用无效 api_key，提交消息，对话区显示红色错误信息）
- [ ] 错误后程序不退出（验证：出错后，输入框仍可用，可继续输入）
- [ ] 网络超时显示友好提示（验证：断网后提交消息，显示超时错误信息）

### AC8: 退出命令

- [ ] `/exit` 命令退出程序（验证：输入 `/exit` 并按 Enter，程序正常退出）
- [ ] Ctrl+C 退出程序（验证：按 Ctrl+C，程序正常退出）
- [ ] 退出时清理流式任务（验证：在流式回复过程中按 Ctrl+C，程序立即退出，无残留进程）

## 集成验证

- [ ] Config 正确传递给 TUI App（验证：配置中的 provider 列表在 TUI 中正确显示）
- [ ] TUI 正确调用 Provider.stream()（验证：提交消息，Provider 接收到完整的消息历史）
- [ ] Provider 正确注入 SYSTEM_PROMPT（验证：首轮对话，模型回复符合 system prompt 的角色定位）
- [ ] Conversation 正确维护历史（验证：多轮对话后，Conversation.messages() 返回完整的 user/assistant 交替序列）
- [ ] 状态机正确切换（验证：IDLE → STREAMING → IDLE 状态切换顺利，无卡顿或状态不一致）

## 编译与测试

- [ ] 项目可正常安装（验证：运行 `uv sync` 或 `pip install -e .`，无错误）
- [ ] 所有模块可正常导入（验证：`python -c "import endless_code.config, endless_code.llm, endless_code.tui"`，无 ImportError）
- [ ] CLI 入口可执行（验证：运行 `endless_code` 命令，程序启动）
- [ ] `python -m endless_code` 可执行（验证：运行 `python -m endless_code`，程序启动）

## 端到端场景

### 场景 1：首次使用流程

**操作：**
1. 复制 `config.yaml.example` 到 `.endless-code/config.yaml`
2. 填写有效的 api_key
3. 运行 `endless_code`
4. 在输入框输入 "你好，请介绍一下自己"
5. 按 Enter 提交

**预期结果：**
- 启动后显示 ASCII 猫 banner 和版本信息
- 单 provider 直接进入对话，多 provider 显示选择界面
- 提交后状态栏显示 "Imagining… (Ns)"
- 模型回复逐字显示
- 回复完成后渲染为 Markdown 格式
- 输入框恢复可用状态

### 场景 2：多轮对话上下文保持

**操作：**
1. 第一轮：输入 "我最喜欢的颜色是蓝色"，提交
2. 等待回复完成
3. 第二轮：输入 "我喜欢什么颜色？"，提交

**预期结果：**
- 第一轮回复确认收到信息
- 第二轮回复包含 "蓝色"，证明上下文保持正确

### 场景 3：错误恢复

**操作：**
1. 使用无效的 api_key 配置
2. 运行程序并提交消息
3. 观察错误显示
4. 退出程序
5. 修正 api_key
6. 重新运行并提交消息

**预期结果：**
- 第一次提交显示红色错误信息（认证失败）
- 程序不退出，输入框仍可用
- 修正后重新运行，消息提交成功

### 场景 4：多 Provider 切换（可选）

**操作：**
1. 配置 2 个 providers（deepseek 和 openai）
2. 启动程序
3. 在选择界面选择 deepseek
4. 提交一个消息，观察回复
5. 退出并重新启动
6. 选择 openai
7. 提交相同的消息

**预期结果：**
- 两次都能正常回复
- 状态栏正确显示当前选择的 provider 名称和模型
- DeepSeek 和 OpenAI 的回复风格可能不同，但都正确显示

### 场景 5：优雅退出

**操作：**
1. 启动程序
2. 提交一个消息
3. 在流式回复过程中按 Ctrl+C

**预期结果：**
- 程序立即退出
- 终端恢复正常状态（无残留 raw mode）
- 无僵尸进程或挂起的网络连接

## 非功能验收

- [ ] 界面响应流畅（验证：对话过程中滚动对话区，无卡顿）
- [ ] Markdown 渲染正确（验证：提交包含代码块、列表、链接的消息，回复正确渲染）
- [ ] 对话区最大宽度适配（验证：调整终端窗口大小，内容自动换行，最大宽度不超过 80 列）
- [ ] 密钥不泄露（验证：config.yaml 在 .gitignore 中，git status 不显示该文件）
- [ ] 启动 banner 包含版本和工作目录（验证：启动时显示 "endless-code v0.1.0" 和当前工作目录）

## 检查清单使用说明

1. 按顺序逐项执行
2. 每项执行后记录实际结果
3. 通过的打 ✓，不通过的记录问题和修复方案
4. 所有项通过后，项目验收完成
