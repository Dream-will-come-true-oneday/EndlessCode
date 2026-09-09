# 会话内模型切换与输出样式 Plan

> 依据：已批准的 `docs/model-style/spec.md`（F1–F8、N1–N4）。

## 架构概览

改动分布在五层，均为**在既有扩展点上追加**，不引入新子系统：

| 层 | 职责 | 覆盖需求 |
|---|---|---|
| `prompt` | 输出样式预设注册表；把样式作为系统提示的一个模块注入 | F5、F6、N3 |
| `command` | `/model`、`/style` 两条命令；host 能力扩展；`/status` 增行 | F1、F5、F7、F8 |
| `agent` | 持有当前样式名，每轮组装稳定提示时使用 | F5、F6 |
| `session` | 会话存档写入切换标记；会话列表读取「最近模型」 | F7 |
| `tui` | 切换编排（原子性、忙碌保护、状态一致）、状态栏与 `/status` 数据源 | F1–F4、F7、N2、N4 |

关键取舍：**模型切换由 TUI 编排并重建 Agent，样式切换只改 Agent 上的一个字段**。前者需要同时替换 provider、上下文窗口、记忆 provider 并保持 runtime/会话/审计不变；后者是纯提示层状态，无需触碰 provider。

## 核心数据结构与接口

### OutputStyle（`prompt/style.py`，新建）

```python
@dataclass(frozen=True)
class OutputStyle:
    name: str         # 命令参数使用的英文标识，如 "concise"
    label: str        # 界面展示名，如 "简洁"
    description: str  # 一句话说明，用于 /style 列表
    content: str      # 注入系统提示的正文；default 为空串

STYLES: tuple[OutputStyle, ...]     # default / concise / explanatory / learning
DEFAULT_STYLE_NAME: str = "default"
STYLE_MODULE_NAME: str = "output_style"
STYLE_PRIORITY: int = 65

def all_styles() -> list[OutputStyle]
def find_style(name: str) -> OutputStyle | None    # 大小写不敏感、去空格；未命中返回 None
def style_content(name: str) -> str                # 未知或 default 返回空串
```

`style.py` **不 import `prompt.modules`**（只依赖标准库 dataclasses），由 `modules.py` 单向依赖 `style.py` 并用 `style_content()` 的返回值构造 `Module`，避免循环导入。

四个预设的 `content` 取向（英文书写，与既有模块一致）：

- `default`：空串，**不注入任何模块**，提示与现状逐字节相同。
- `concise`：只给结论与必要代码；不复述问题、不加背景铺垫、不列举未被询问的替代方案。
- `explanatory`：完成同样的工作，但在关键步骤附带原理、权衡与被否决的替代方案说明。
- `learning`：教学取向；在关键决策点给出 2–3 个方案与取舍并邀请用户确认，解释涉及的概念与术语。

### 系统提示组装（`prompt/modules.py`）

```python
def build_system_prompt(
    instructions: str = "",
    memory: str = "",
    style: str = DEFAULT_STYLE_NAME,
) -> str
```

- 第三参数带默认值，既有位置调用（含测试）行为不变。
- 实现：`assemble_system(fixed_modules() + [Module(STYLE_MODULE_NAME, STYLE_PRIORITY, style_content(style))] + optional_modules(...))`。样式模块排在 `tone_style`(60) 之后、`custom_instructions`(80) 之前，正文以「以下输出要求优先于上文语气约定」开头，实现后置覆盖而不修改既有模块文本。
- `assemble_system` 既有行为会跳过 content 为空的模块，因此 default 样式（空串）**不产生任何模块**，无需额外分支。
- 未知样式名由 `style_content` 静默返回空串：**提示组装永不抛异常**，用户可见的报错由命令层负责。
- `style_label(name)` 定义在 `style.py`（未知时回落 name 原文），供 TUI 状态栏与命令层展示。
- `prompt/__init__.py` 导出 `OutputStyle`、`all_styles`、`find_style`、`style_content`、`style_label`、`DEFAULT_STYLE_NAME`、`STYLE_PRIORITY`，并加入 `__all__`；模块级常量 `SYSTEM_PROMPT` 保持默认样式，语义不变。

### 命令层类型（`command/types.py`）

```python
@dataclass(frozen=True)
class ModelOption:
    index: int      # 从 1 开始，与 /model <编号> 对应
    name: str       # provider 配置名
    model: str      # 模型标识
    current: bool

@dataclass(frozen=True)
class StyleOption:
    name: str
    label: str
    description: str
    current: bool

@dataclass(frozen=True)
class SwitchResult:
    ok: bool
    message: str    # 成功摘要或失败/拒绝原因（由 TUI 脱敏后填充）
    name: str = ""  # 成功时的 provider 名 / 样式名
    model: str = "" # 成功时的模型标识（样式切换留空）
```

`SessionInfo` 追加两个**带默认值**字段，避免破坏既有关键字构造：

```python
    output_style: str = "default"
    context_window: int = 0
```

### CommandHost 扩展（`command/host.py`）

```python
    # 模型切换
    def get_model_options(self) -> list[ModelOption]: ...
    def switch_model(self, selector: str) -> SwitchResult: ...

    # 输出样式
    def get_style_options(self) -> list[StyleOption]: ...
    def set_output_style(self, name: str) -> SwitchResult: ...
```

四个方法均为同步；实现方（TUI）负责忙碌保护、原子性与脱敏，命令层只负责渲染与反馈。

### Agent 扩展（`agent/__init__.py`）

```python
Agent.__init__(..., output_style: str = DEFAULT_STYLE_NAME)   # 追加在 checkpoint 之后
def set_output_style(self, style: str) -> None                # 幂等赋值，下一轮 run 生效
```

`_run` 内唯一改动：`build_system_prompt(self._instruction_text, memory_text, self._output_style)`。其余循环、压缩、权限、审计逻辑零改动。

### 会话存档标记（`session/writer.py`）

```python
def write_model_marker(self, previous: str, current: str) -> None
    # {"type":"model_switch","ts":<int>,"model":<current>,"previous":<previous>}
def write_style_marker(self, style: str) -> None
    # {"type":"style_switch","ts":<int>,"style":<style>}
```

- 复用既有 `_write` 路径（加锁、flush、fsync），与 `write_compact_marker` 同构。
- 两个方法都会把内部 `_model` 更新为最新值，使 `clear_session` 等重建路径拿到当前模型。
- `load_session` **无需改动**：它只处理 `type == "compact"` 与 role 条目，未知 type 自动忽略。

### 最近模型读取（`session/list.py`）

`_read_summary(path) -> tuple[str, str]` 改为完整遍历：

- 标题：仍取第一条含非空文本的 user 消息（截断逻辑不变）。
- 模型：取**最后一条**带字符串 `model` 字段的条目（首条消息的 `model` 或 `model_switch` 标记），因此标题命中后不再提前 return，而是继续扫描剩余行。
- 无任何 model 字段时返回空串，调用方保留 `model or "未知模型"` 回落。

## 模块设计

### `prompt/style.py`（新建）
- 职责：样式预设的唯一定义处（名称、展示名、说明、注入正文）与查询/模块化工厂。
- 对外接口：见上。
- 依赖：仅标准库 `dataclasses`。**不依赖 `prompt.modules`**，依赖方向为 `modules → style` 单向，无环。

### `command/builtin.py`
- 职责：注册 `/model`、`/style`，并扩展 `/status` 输出。
- `/model`（kind=UI，aliases `("/models",)`，arg_hint `"[编号|名称]"`）：
  - 无参：`host.get_model_options()` → 每项一行 `f"{index}. {name} — {model}"`，当前项追加 `（当前）`；列表为空时提示「未配置可用模型」。
  - 有参：`host.switch_model(args.strip())`；`ok=True` 走 `show_notice(result.message)`，`ok=False` 走 `show_error(result.message)`。
- `/style`（kind=UI，aliases `("/styles",)`，arg_hint `"[样式名]"`）：
  - 无参：首行显示当前样式，其后每项一行 `f"{name}（{label}）— {description}"`，当前项追加 `（当前）`。
  - 有参：`host.set_output_style(...)`，反馈方式同 `/model`。
- `/status`：在「权限模式」行之后插入 `f"输出样式：{info.output_style}"` 与 `f"上下文窗口：{info.context_window}"`（窗口按 `1000000` 形式原样展示，不做单位换算，避免歧义）。
- 两条新命令自动进入 `/help`、Tab 补全与建议面板（均由 Registry 现有逻辑提供，无需额外改动）。

### `agent/__init__.py`
- 职责：把样式状态带入每轮稳定提示。
- 依赖：`prompt.build_system_prompt`、`prompt.DEFAULT_STYLE_NAME`。
- 不感知 provider 切换：切换后 TUI 重建 Agent 实例，构造参数带入当前样式。

### `session/writer.py` / `session/list.py`
- 职责：切换可追溯（写标记）与会话列表展示最近模型（读标记）。
- 依赖：无新增外部依赖。

### `tui/app.py`
- 职责：切换编排、忙碌保护、原子性、状态展示、host 四个新方法。
- 新增实例状态：`self._output_style: str = DEFAULT_STYLE_NAME`（`__init__` 中初始化）。
- 新增私有编排方法（签名与行为见下节交互流程）：

```python
def _resolve_model_selector(self, selector: str) -> ProviderConfig | None
def _switch_provider(self, cfg: ProviderConfig) -> SwitchResult
def _command_set_style(self, name: str) -> SwitchResult
```

- host 实现：`get_model_options()` 由 `self._providers` 与当前 `self._provider.name` 比对生成；`switch_model()` = 忙碌检查 + selector 解析 + `_switch_provider`；`get_style_options()` 由 `all_styles()` 与 `self._output_style` 生成；`set_output_style()` = 忙碌检查 + `_command_set_style`。
- `_update_status()`：sub_title 追加样式标签与窗口，形如
  `f"{mode_label} | {provider_name} | {model} | {style_label} | {window} ctx | ↑{in} ↓{out} tok | /help 查看命令"`。
- `get_session_info()`：补 `output_style=self._output_style`、`context_window=self._runtime.context_window if self._runtime else 0`。
- `_activate_provider`（启动路径）**保持不变**：它新建 session runtime 与 writer，语义是「开新会话」，不可用于运行期切换。

## 模块交互

### 模型切换调用链（F1、F2、F3、F4、N2、N4）

```text
用户输入 "/model 2"
  → Dispatcher.try_dispatch → model_command(host, "2")
  → host.switch_model("2")
      1. state != IDLE → SwitchResult(ok=False, "当前任务尚未结束，暂不能切换模型。")  [F4]
      2. _resolve_model_selector：纯数字→按 index；否则对 name/model 大小写不敏感精确匹配；
         未命中 → ok=False, "未找到模型：<sel>。输入 /model 查看可用列表。"
      3. 目标即当前 → ok=True, "当前已在使用 <name>（<model>）。"（不改任何状态）
      4. cfg.resolve_api_key()  → ConfigError → ok=False, "模型 <name> 的 API key 不可用：<原因>"  [F2]
      5. new_provider(cfg)      → 异常     → ok=False, "切换失败：<类型>: <原因>"（原状态零变更）  [F2]
      6. 提交（全部成功才执行）：                                                        [F3、N2]
         old_model = self._provider.model
         self._provider = provider；self._secrets.add(secret)
         self._runtime.context_window = effective_context_window(cfg)
         self._runtime.usage_anchor = 0；self._runtime.anchor_msg_len = 0
         self._writer.write_model_marker(old_model, provider.model)                      [F7]
         self._memory_manager.set_provider(provider, provider.model)
         self._agent = Agent(provider, self._tool_registry, self._version, self._engine,
                             self._runtime, memory_manager=..., instruction_text=...,
                             audit_writer=self._audit_writer, checkpoint=self._checkpoint,
                             output_style=self._output_style)
         self._update_status()                                                           [F7]
      7. 返回 ok=True, "已切换到 <name>（<model>）。"
  → 命令层 show_notice / show_error（文本已过 _safe 脱敏）                                [N4]
```

保持不变的对象：`Conversation`（历史与已显示聊天内容）、`SessionRuntime.session`（会话目录与标识）、`Writer`、`AuditWriter`、`CheckpointManager`、`Engine` 与 `self._mode`。因此 N2（不新建会话目录、不清历史与审计、不重置权限规则）与 N4（权限判定链完全不受切换影响）由构造顺序直接保证。

下一轮对话时：`Agent._run` 用新窗口计算 `auto_threshold`，锚点已归零故按消息重新估算；若历史超过新窗口阈值，走既有 `manage_context` 自动压缩路径（AC3）。动态环境段由 `gather_environment(version, self._provider.model)` 自动带出新模型。

### 样式切换调用链（F5、F6）

```text
"/style concise" → style_command → host.set_output_style("concise")
  1. state != IDLE → ok=False, "当前任务尚未结束，暂不能切换输出样式。"
  2. find_style(name) is None → ok=False, "未知输出样式：<name>。可选：default / concise / explanatory / learning"
  3. name == self._output_style → ok=True, "当前已是 <label> 样式。"
  4. self._output_style = style.name
     if self._agent is not None: self._agent.set_output_style(style.name)
     if self._writer is not None: self._writer.write_style_marker(style.name)
     self._update_status()
  5. ok=True, "已切换到 <label> 样式，下一轮回答生效。"
```

历史消息与消息数不受影响（F6/AC6）；同一样式持续期间 `build_system_prompt` 输出恒定，稳定段不变（N3）。

### 会话恢复列表（F7）

```text
/resume → list_sessions → _read_summary 逐行扫描 → model 取最后一次出现的值
        → OptionList 行内展示该模型（既有渲染格式不变）
```

## 文件组织

```text
src/endless_code/
├── prompt/
│   ├── style.py            # 新建：OutputStyle 预设注册表、find_style、style_content、style_label（不依赖 modules）
│   ├── modules.py          # 改：import style，build_system_prompt 增加 style 参数并插入样式模块
│   └── __init__.py         # 改：导出样式 API 与 DEFAULT_STYLE_NAME
├── command/
│   ├── types.py            # 改：ModelOption / StyleOption / SwitchResult；SessionInfo 增两字段
│   ├── host.py             # 改：CommandHost 增四个方法
│   └── builtin.py          # 改：注册 /model、/style；/status 增输出样式与上下文窗口
├── agent/
│   └── __init__.py         # 改：output_style 构造参数 + set_output_style；_run 传入样式
├── session/
│   ├── writer.py           # 改：write_model_marker / write_style_marker
│   └── list.py             # 改：_read_summary 取最近模型
└── tui/
    └── app.py              # 改：_output_style 状态、_resolve_model_selector、_switch_provider、
                            #     _command_set_style、四个 host 实现、_update_status、get_session_info

tests/
├── test_prompt.py          # 改：默认样式逐字节不变；三种样式注入且位置在 tone_style 之后；未知样式降级
├── test_command_builtin.py # 改：FakeHost 补四个方法与 SessionInfo 新字段；/model、/style、/status 用例
├── test_agent.py           # 改：set_output_style 后下一轮稳定提示包含样式正文
├── test_session.py         # 改：两种 marker 写入格式；load_session 忽略 marker；list 取最近模型
└── test_tui.py             # 改：切换保留对话与状态、失败原子性、忙碌保护、样式切换与状态栏
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 模型切换落点 | TUI 编排 + 重建 Agent | runtime/conv/writer/audit/checkpoint 全部保留即可满足 N2；避免给 Agent 增加 provider 可变性，也避免沿用 `_restore_session` 里改私有属性的做法 |
| 是否复用 `_activate_provider` | 不复用，另写 `_switch_provider` | 前者会关闭旧 writer、新建 session runtime（即开新会话），与「保留当前对话」直接冲突；且它先关 writer 再构造 provider，非原子 |
| 原子性顺序 | 先解析密钥与构造 provider，全部成功后才提交状态 | F2 要求失败时原模型与对话完全不变；构造期抛错不触碰任何字段即可保证 |
| 样式生效方式 | Agent 持样式名，每轮组装稳定提示时读取 | 与现有 `_run` 每轮调用 `build_system_prompt` 一致；天然「下一轮生效」且不改写历史（F6） |
| default 样式注入内容 | 空串，不产生模块 | 保证默认路径提示与现状完全一致，零回归且不打破既有提示缓存（N1、N3） |
| 样式模块所在文件 | `prompt/style.py` 只出数据，`Module` 由 `modules.py` 构造 | 若 `style.py` 反向 import `Module` 会与 `modules.py` 形成循环导入，启动即失败 |
| 样式模块优先级 | 65，紧随 `tone_style`(60) | 后置覆盖语气约定，无需改写既有模块文本，降低回归面 |
| 未知样式名处理 | 提示层静默降级为 default，命令层报错 | 组装函数不可失败（被 provider 回退路径复用）；用户可见反馈集中在命令层 |
| 切换记录载体 | 复用 JSONL marker 机制（同 `compact`） | `load_session` 已忽略未知 type，无需新文件格式或迁移；审计风格一致 |
| 会话列表模型来源 | `_read_summary` 取最后一次出现的 model | 满足 F7「最近使用的模型」；标题逻辑不变，改动局限在一个函数 |
| 忙碌保护判据 | host 方法内检查 `SessionState.IDLE` | 与 `set_mode`、`_command_compact`、`_command_rewind` 完全一致，覆盖 STREAMING/APPROVING/RESUMING/REWINDING/SELECTING（F4） |
| 列表渲染方式 | 命令层拼纯文本经 `show_notice` | 不给 host 增加渲染能力，命令逻辑可在无 Textual 环境下单测 |
| SessionInfo 扩字段 | 新增字段带默认值 | 既有测试与构造点（`test_command_builtin.py` 的 FakeHost）不被破坏 |
| 命令 kind | 两条均为 UI | 与 `/permissions` 同类：改变应用状态、需忙碌保护、不产生 AI 请求 |
| 别名 | `/models`、`/styles` | 与既有 `/perm`、`/mem` 风格一致，且参与前缀建议（F8） |
| 选择持久化 | 不持久化 | spec「不做的事」明确排除；重启回配置默认，`_activate_provider` 路径不变 |
