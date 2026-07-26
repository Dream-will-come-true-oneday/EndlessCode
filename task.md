# 多协议 LLM 终端对话客户端 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `pyproject.toml` | 添加依赖（textual, rich, openai, pyyaml），声明脚本入口 |
| 新建 | `.endless-code/config.yaml.example` | 配置文件示例 |
| 新建 | `.gitignore` | 忽略 `.endless-code/config.yaml` |
| 新建 | `src/endless_code/__init__.py` | 包初始化，导出版本号 |
| 新建 | `src/endless_code/__main__.py` | 支持 `python -m endless_code` |
| 新建 | `src/endless_code/config.py` | Config、ProviderConfig 类型，load 函数与校验 |
| 新建 | `src/endless_code/prompt.py` | SYSTEM_PROMPT、CAT_BANNER、render_banner |
| 新建 | `src/endless_code/llm/__init__.py` | Provider Protocol、Message、StreamEvent、new_provider |
| 新建 | `src/endless_code/llm/deepseek_provider.py` | DeepSeek 适配器（基于 AsyncOpenAI） |
| 新建 | `src/endless_code/llm/openai_provider.py` | OpenAI 适配器（基于 AsyncOpenAI） |
| 新建 | `src/endless_code/conversation.py` | Conversation 类，维护多轮历史 |
| 新建 | `src/endless_code/tui/__init__.py` | TUI 子包初始化 |
| 新建 | `src/endless_code/tui/app.py` | EndlessCodeApp、状态机、主逻辑 |
| 新建 | `src/endless_code/cli.py` | 入口：加载配置、打印 banner、启动 TUI |
| 新建 | `README.md` | 项目说明文档 |

---

## T1: 配置项目依赖和入口

**文件：** `pyproject.toml`
**依赖：** 无
**步骤：**
1. 在 `[project.dependencies]` 添加：`textual`、`rich`、`openai`、`pyyaml`
2. 在 `[project.scripts]` 添加入口：`endless_code = "endless_code.cli:main"`
3. 设置 `requires-python = ">=3.12"`

**验证：** 运行 `uv sync` 或 `pip install -e .`，依赖安装成功

---

## T2: 创建配置文件示例和 .gitignore

**文件：** `.endless-code/config.yaml.example`、`.gitignore`
**依赖：** 无
**步骤：**
1. 创建 `.endless-code/` 目录
2. 创建 `config.yaml.example`，包含两个 provider 示例（deepseek 和 openai）
3. 在 `.gitignore` 添加 `.endless-code/config.yaml`（保护真实密钥）

**验证：** 文件存在，示例配置包含必要字段（name, protocol, api_key, model, base_url, thinking）

---

## T3: 定义配置层数据结构

**文件：** `src/endless_code/config.py`
**依赖：** 无
**步骤：**
1. 定义 `ProviderConfig` dataclass，包含字段：name, protocol, api_key, model, base_url, thinking
2. 定义 `Config` dataclass，包含 `providers: list[ProviderConfig]`
3. 定义 `ConfigError` 异常类

**验证：** 运行 `python -c "from endless_code.config import ProviderConfig, Config"`，导入成功

---

## T4: 实现配置加载和校验

**文件：** `src/endless_code/config.py`
**依赖：** T3
**步骤：**
1. 实现 `load(path: str) -> Config` 函数
2. 使用 `pyyaml` 读取 YAML 文件
3. 校验：providers 非空、每项必填字段非空、protocol 必须是 "deepseek" 或 "openai"
4. 校验失败抛出 `ConfigError`，携带具体错误信息

**验证：** 创建测试配置文件，运行 `load()` 函数，正常配置加载成功，错误配置抛出 `ConfigError`

---

## T5: 定义 Prompt 常量和 Banner

**文件：** `src/endless_code/prompt.py`
**依赖：** 无
**步骤：**
1. 定义 `SYSTEM_PROMPT` 常量（简洁的助手 system prompt）
2. 定义 `CAT_BANNER` 常量（ASCII 猫图案）
3. 实现 `render_banner(version: str, cwd: str) -> str` 函数，返回包含版本和工作目录的启动横幅

**验证：** 运行 `python -c "from endless_code.prompt import SYSTEM_PROMPT, CAT_BANNER, render_banner; print(render_banner('0.1.0', '/tmp'))"`，输出正确

---

## T6: 定义 LLM 层协议和类型

**文件：** `src/endless_code/llm/__init__.py`
**依赖：** 无
**步骤：**
1. 定义 `Message` dataclass（role: Literal["user", "assistant"], content: str）
2. 定义 `StreamEvent` dataclass（text: str, done: bool, err: Exception | None）
3. 定义 `Provider` Protocol，包含属性 name、model 和方法 `stream(msgs: list[Message]) -> AsyncIterator[StreamEvent]`
4. 声明 `new_provider(cfg: ProviderConfig) -> Provider` 函数签名（暂不实现）

**验证：** 运行 `python -c "from endless_code.llm import Message, StreamEvent, Provider"`，导入成功

---

## T7: 实现 DeepSeek 适配器

**文件：** `src/endless_code/llm/deepseek_provider.py`
**依赖：** T6
**步骤：**
1. 创建 `DeepSeekProvider` 类，接收 `ProviderConfig`
2. 初始化 `openai.AsyncOpenAI`，使用 `cfg.base_url` 和 `cfg.api_key`
3. 实现 `stream()` 方法：
   - 插入 system message（使用 `SYSTEM_PROMPT`）
   - 调用 `client.chat.completions.create()` with `stream=True`
   - 如果 `cfg.thinking=True`，添加 reasoning_effort 参数（DeepSeek R1 支持）
   - 用 `async for` 迭代 chunks，yield `StreamEvent(text=delta)`
   - 正常结束 yield `StreamEvent(done=True)`
   - 异常时 yield `StreamEvent(err=exc)`

**验证：** 编写简单测试脚本，创建 DeepSeekProvider 实例并调用 stream()（使用 mock 或真实 API）

---

## T8: 实现 OpenAI 适配器

**文件：** `src/endless_code/llm/openai_provider.py`
**依赖：** T6
**步骤：**
1. 创建 `OpenAIProvider` 类，接收 `ProviderConfig`
2. 初始化 `openai.AsyncOpenAI`，使用 `cfg.base_url`（可选）和 `cfg.api_key`
3. 实现 `stream()` 方法：
   - 插入 system message
   - 调用 `client.chat.completions.create()` with `stream=True`
   - 忽略 `cfg.thinking` 字段
   - 用 `async for` 迭代 chunks，yield `StreamEvent(text=delta)`
   - 正常结束 yield `StreamEvent(done=True)`
   - 异常时 yield `StreamEvent(err=exc)`

**验证：** 编写简单测试脚本，创建 OpenAIProvider 实例并调用 stream()

---

## T9: 实现 Provider 工厂函数

**文件：** `src/endless_code/llm/__init__.py`
**依赖：** T7, T8
**步骤：**
1. 实现 `new_provider(cfg: ProviderConfig) -> Provider` 函数
2. 根据 `cfg.protocol` 返回 `DeepSeekProvider` 或 `OpenAIProvider` 实例
3. 如果 protocol 不支持，抛出 `ValueError`

**验证：** 运行 `python -c "from endless_code.llm import new_provider; from endless_code.config import ProviderConfig; p = new_provider(ProviderConfig(name='test', protocol='openai', api_key='sk-xxx', model='gpt-4'))"`，对象创建成功

---

## T10: 实现 Conversation 类

**文件：** `src/endless_code/conversation.py`
**依赖：** T6
**步骤：**
1. 定义 `Conversation` 类
2. 初始化 `_messages: list[Message] = []`
3. 实现 `add_user(text: str)` 方法，追加 user 消息
4. 实现 `add_assistant(text: str)` 方法，追加 assistant 消息
5. 实现 `messages() -> list[Message]` 方法，返回消息副本

**验证：** 运行单元测试，验证消息添加和读取逻辑

---

## T11: 实现 TUI App 骨架和状态机

**文件：** `src/endless_code/tui/app.py`
**依赖：** T6, T9, T10
**步骤：**
1. 创建 `EndlessCodeApp(App)` 类，继承自 `textual.app.App`
2. 定义 `SessionState` 枚举（SELECTING, IDLE, STREAMING）
3. 初始化成员：providers, provider, conv, state, cur_reply, turn_start
4. 实现 `compose()` 方法，返回基础布局：Header、RichLog（对话区）、TextArea（输入框）、Footer（状态栏）
5. 实现状态切换逻辑（暂不实现流式）

**验证：** 运行 `EndlessCodeApp(providers=[...]).run()`，TUI 界面显示正常，可以切换状态

---

## T12: 实现 Provider 选择逻辑

**文件：** `src/endless_code/tui/app.py`
**依赖：** T11
**步骤：**
1. 在 `on_mount()` 中判断 `len(providers)`
2. 如果只有 1 个 provider，直接调用 `new_provider()` 并进入 IDLE 状态
3. 如果多个 provider，显示 `OptionList`，进入 SELECTING 状态
4. 监听选择事件，用户选定后调用 `new_provider()` 并进入 IDLE 状态

**验证：** 运行 App，单 provider 直接进入 IDLE，多 provider 显示选择列表

---

## T13: 实现用户输入提交逻辑

**文件：** `src/endless_code/tui/app.py`
**依赖：** T12
**步骤：**
1. 监听 TextArea 的 Enter 键事件（绑定 `submit` 方法）
2. 实现 `async def submit(text: str)` 方法：
   - 识别 `/exit` 命令，调用 `self.exit()`
   - 调用 `conv.add_user(text)`
   - 在 RichLog 中追加用户消息块
   - 清空输入框
   - 设置 `turn_start = time.monotonic()`
   - 切换到 STREAMING 状态
   - 启动流式任务（暂时使用 placeholder）

**验证：** 输入文本并按 Enter，用户消息显示在对话区，输入框清空

---

## T14: 实现流式消费逻辑

**文件：** `src/endless_code/tui/app.py`
**依赖：** T13
**步骤：**
1. 实现 `async def _consume_stream()` 方法
2. 调用 `self.provider.stream(self.conv.messages())`
3. 用 `async for event in stream:` 迭代事件
4. `event.text` 非空时，追加到 `cur_reply` 并更新动态显示区
5. `event.done` 时，用 `rich.markdown.Markdown` 渲染完整回复，写入 RichLog，调用 `conv.add_assistant(cur_reply)`，切换回 IDLE
6. `event.err` 时，显示错误消息，切换回 IDLE

**验证：** 提交消息后，模型回复逐字显示，完成后渲染为 Markdown

---

## T15: 实现流式计时显示

**文件：** `src/endless_code/tui/app.py`
**依赖：** T14
**步骤：**
1. 在 STREAMING 状态启动时，调用 `self.set_interval(0.1, self._tick)`
2. 实现 `_tick()` 方法，计算 `elapsed = time.monotonic() - turn_start`
3. 更新状态栏显示 "Imagining… (Ns)"

**验证：** 流式回复时，状态栏显示实时计时

---

## T16: 实现 CLI 入口

**文件：** `src/endless_code/cli.py`
**依赖：** T4, T5, T11
**步骤：**
1. 定义 `main()` 函数
2. 调用 `config.load(".endless-code/config.yaml")`
3. 捕获 `ConfigError`，打印错误信息并 `sys.exit(1)`
4. 打印 `render_banner(version="0.1.0", cwd=os.getcwd())`
5. 调用 `EndlessCodeApp(cfg.providers).run()`

**验证：** 运行 `python -m endless_code`，加载配置、显示 banner、启动 TUI

---

## T17: 添加 __init__.py 和 __main__.py

**文件：** `src/endless_code/__init__.py`、`src/endless_code/__main__.py`、`src/endless_code/tui/__init__.py`
**依赖：** T16
**步骤：**
1. 在 `__init__.py` 定义 `__version__ = "0.1.0"`
2. 在 `__main__.py` 导入并调用 `cli.main()`
3. 在 `tui/__init__.py` 导入 `EndlessCodeApp`

**验证：** 运行 `python -m endless_code` 和 `endless_code` 命令，都能正常启动

---

## T18: 编写 README.md

**文件：** `README.md`
**依赖：** T17
**步骤：**
1. 编写项目简介
2. 说明安装方法（`uv sync` 或 `pip install -e .`）
3. 说明配置方法（复制 `config.yaml.example` 到 `.endless-code/config.yaml`）
4. 说明运行方法（`endless_code` 或 `python -m endless_code`）
5. 列出支持的 provider（deepseek, openai）

**验证：** 按 README 步骤操作，新用户能成功运行

---

## 执行顺序

```
T1 → T2
     ↓
T3 → T4
     ↓
T5 → T6 → T7 → T9
          ↓      ↓
          T8 ----┘
               ↓
          T10 → T11 → T12 → T13 → T14 → T15
                                         ↓
                                    T16 → T17 → T18
```

---

## 说明

- 任务粒度控制在 2-5 分钟，每个任务聚焦单一职责
- T7/T8（两个适配器）可并行开发
- T14（流式消费）是核心逻辑，依赖前面所有 LLM 和 TUI 基础
- 每个任务都带有明确的验证步骤
