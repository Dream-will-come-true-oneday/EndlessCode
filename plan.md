# 权限系统 Plan

> 技术栈：Python 3.12+；沿用 `anthropic` / `openai` 官方 SDK（本阶段不改 provider 适配层）。权限判定全部落在 agent 编排层与新增的 `endless_code.permission` 模块，与协议无关。

## 架构概览

本阶段新增一个 **permission 子包**承载前四层防御与配置加载，并在 **agent 模块**把判定接入工具执行链、由 agent 编排承担第五层人在回路；**tui 模块**新增「待批准」交互态；**cli** 负责装配引擎并注入。`llm` / provider 适配层不改（N6 跨协议一致天然成立）。

> 五层边界澄清：`permission.Engine.check` 实现**前四层**（黑名单/沙箱/规则/模式兜底），以返回 `Ask` 作为「请走第五层」的信号；第五层人在回路由 agent 在 Ask 后编排驱动（发 ApprovalRequest 事件、await 用户决策）。二者合称五层。

- **permission 子包（新增）**：定义 `Mode`（四档 IntEnum）、`Decision`（Allow/Deny/Ask）、`Category`（Read/Write/Exec）、`Outcome`（三选一）；实现前四层判定 `check`；持有黑名单正则集、沙箱（项目根 + 符号链接解析）、三级规则集（user/project/local 三个配置文件）、模式兜底矩阵、友好名映射与路径提取。对外暴露 `check`、本地规则持久化、配置加载。仅依赖 `llm`（取 `ToolCall`）与标准库 + `pyyaml`。
- **agent 模块（改造）**：`Mode` 类型从本模块迁到 `endless_code.permission`；`Agent` 增加 `engine` 字段；工具执行前调用 `engine.check`。Allow 执行，Deny 直接构造 `ToolResult(is_error=True)` 回灌，Ask 发 `ApprovalRequest` 事件并 await 用户决策。
- **tui 模块（改造）**：`EndlessCodeApp.mode` 改为 `permission.Mode`，持有 `Engine`；新增 `APPROVING` 态与待批准请求渲染/按键处理；全局 ctrl+c/esc 分派覆盖 `STREAMING | APPROVING`；新增 `shift+tab` 循环切换权限模式（仅 idle 态生效）；状态栏左侧改为常驻显示当前权限模式（取代 provider 名）。
- **cli / smoke（改造）**：`cli.main` 用 `Path.cwd().resolve()` 构造 `permission.Engine` 注入 TUI；`examples/smoke.py` 用 cwd 构造引擎并以 `Mode.BYPASS` 运行，避免无人在回路时阻塞在 Ask。

数据流（单个工具调用）：
```
agent.execute_batched(calls, mode)
   └─ read_only 实参由批类别决定（只读批=True / 串行批=False，等价于 registry.is_read_only(name)）
     decision, reason = engine.check(mode, call, read_only)   # 前四层，短路
       ① 黑名单(仅 Exec 类)      → 命中 Deny
       ② 沙箱(仅文件类)          → 逃逸 Deny
       ③ 规则引擎(三级)          → 命中 allow→Allow / deny→Deny
       ④ 模式兜底矩阵            → Allow 或 Ask
  decision==Allow → await tool.execute(...)
  decision==Deny  → ToolResult(tool_call_id, content=reason, is_error=True) 回灌
  decision==Ask   →  emit ApprovalRequest(name, args, reason, respond_future)
                       → await respond_future
              用户三选一(↑↓+回车 / 数字键 1/2/3) → AllowOnce(执行) /
                        AllowForever(engine.persist_local_allow+执行) / DenyOnce(回灌)
```

## 核心数据结构与接口

### permission.Mode（迁自 agent + 扩展）
```python
from enum import IntEnum


class Mode(IntEnum):
    DEFAULT = 0  # 只读 Allow / 文件写 Ask / 命令执行 Ask
    ACCEPT_EDITS = 1  # 文件写 Allow / 命令执行 Ask
    PLAN = 2  # 仅只读工具可见（沿用现有 Plan Mode）；矩阵兜底同 default
    BYPASS = 3  # 全 Allow（黑名单/沙箱仍拦）


def parse_mode(s: str) -> tuple[Mode, bool]:
    """大小写不敏感识别四档名；未知返回 (Mode.DEFAULT, False)。"""
```

### permission.Decision / Category / Outcome
```python
class Decision(IntEnum):
    ALLOW = 0
    DENY = 1
    ASK = 2


class Category(IntEnum):
    READ = 0
    WRITE = 1
    EXEC = 2


class Outcome(IntEnum):
    DENY_ONCE = 0  # 拒绝本次
    ALLOW_ONCE = 1  # 允许本次，不留规则
    ALLOW_FOREVER = 2  # 永久允许，写本地层精确规则并执行
```

### permission.Rule / RuleSet
```python
@dataclass
class Rule:
    tool: str  # 友好名：Bash/Read/Write/Edit/Glob/Grep
    pattern: str  # 模式段；"" 表示匹配该工具全部调用
    allow: bool


@dataclass
class RuleSet:
    allow: list[Rule]
    deny: list[Rule]

    def match(self, friendly: str, target: str) -> tuple[Decision, bool]:
        """先 deny 后 allow；返回 (Allow|Deny, 是否命中)。"""
```

### permission.Settings（单个 YAML 文件结构）
```python
@dataclass
class PermissionsBlock:
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)


@dataclass
class Settings:
    default_mode: str = ""  # default/acceptEdits/plan/bypassPermissions
    permissions: PermissionsBlock = field(default_factory=PermissionsBlock)
```

### permission.Engine（核心，前四层 + 配置）
```python
@dataclass
class Engine:
    root: str  # 项目根（绝对、已解析符号链接）
    blacklist: list[re.Pattern]  # 内置危险命令正则（不可配置，N1）
    user: RuleSet  # 用户级
    project: RuleSet  # 项目级
    local: RuleSet  # 本地级
    local_path: str  # 本地层写入目标（.endless-code/settings.local.yaml）
    start_mode: Mode  # 启动默认模式
```

### agent.ApprovalRequest（新增，人在回路）
```python
@dataclass
class ApprovalRequest:
    name: str  # 工具内部名，用于展示
    args: str  # 原始参数 JSON；TUI 展示前会脱敏/摘要
    reason: str  # 触发 Ask 的原因（模式 + 类别）
    respond: asyncio.Future[Outcome]  # 单次 future：TUI 回传用户选择
```
当前 `event.Event` 新增一个可选字段 `approval: ApprovalRequest | None = None`。

## 核心接口

### permission 子包
```python
def new_engine(root: str) -> tuple[Engine, Exception | None]:
    """
    解析项目根、加载三层配置、编译黑名单、确定启动模式。
    即使发生致命错误（仅当项目根不可解析时），也返回非 None 的空规则安全引擎 + err。
    配置文件格式错误绝不导致引擎构造失败，只降级跳过该文件。
    """


def check(
    engine: Engine, mode: Mode, call: ToolCall, read_only: bool
) -> tuple[Decision, str]:
    """前四层判定，返回 (裁决, 理由)。"""


def persist_local_allow(engine: Engine, call: ToolCall) -> None:
    """把精确 allow 规则写入本地层文件 + 内存。"""
```
这些函数也可挂为 `Engine` 方法（`engine.check(...)`、`engine.persist_local_allow(...)`）。

**check → reason 文案来源表：**

| 裁决来源 | reason 文案（示例） |
|---|---|
| 黑名单命中 | `命中危险命令黑名单：<命令片段>` |
| 沙箱逃逸 | `路径在项目目录之外：<target>` |
| deny 规则命中 | `匹配 deny 规则：<Tool(pattern)>` |
| 模式兜底 Ask | `<mode> 模式下 <category> 类操作需确认` |
| Allow（各来源） | 空串 |

**内部辅助函数：**
```python
# settings.py
def friendly_name(internal: str) -> str:
    """bash→Bash, read_file→Read, write_file→Write,
    edit_file→Edit, glob→Glob, grep→Grep；未知原样返回。"""


def categorize(internal: str, read_only: bool) -> Category:
    """read_only→READ；否则 write_file/edit_file→WRITE；其余（含 bash、未知）→EXEC。"""


def extract_target(call: ToolCall) -> tuple[str, bool, bool]:
    """解析参数；返回 (target, is_file, ok)。"""


# rule.py
def parse_rule(s: str) -> tuple[Rule, bool]: ...
def match_pattern(pattern: str, target: str) -> bool: ...


# engine.py
def mode_fallback(mode: Mode, cat: Category) -> Decision:
    """只产 Allow/Ask。"""


# blacklist.py
def hits_blacklist(command: str) -> bool: ...


# sandbox.py
def sandbox_ok(engine: Engine, path: str) -> bool: ...
```

**`extract_target` 解析与失败归属（N7/AC15）：**
- `call.input`（str 或 dict）做 json 解析（若是 str）；`read_file/write_file/edit_file` 取 `path`；`glob/grep` 取搜索根 `path`（空 → "."）；`bash` 取 `command`；未知工具返回 `("", False, False)`。
- 返回 `(target, is_file, ok)`；`ok=False` 表示解析失败或缺必填字段。
- 失败归属：
  - 文件类工具 `ok=False` → `check` 在沙箱层直接判 Deny（无法解析文件路径参数，安全拒绝），不静默放行。
  - bash `ok=False` → 命令视为空串，不命中黑名单，落到规则/模式兜底（Exec→Ask），由人在回路兜底，绝不直接 Allow。
  - 未知工具 → `is_file=False`，走 Exec 类模式兜底 Ask。

### agent 模块（改造现有类，不新增工厂）
```python
class Agent:
    def __init__(
        self,
        provider: Provider,
        registry: Registry,
        version: str = "0.1.0",
        engine: Engine | None = None,
    ) -> None: ...
```
`Agent` 在 TUI/CLI/smoke 中统一注入 `engine`；若未传入可退化为 `permission.new_engine(str(Path.cwd().resolve()))[0]`，方便测试与脚本使用。

### tui 模块（现有 EndlessCodeApp，不拆分文件）
```python
class EndlessCodeApp(App):
    def __init__(
        self,
        providers: list[ProviderConfig],
        registry: Registry | None = None,
        version: str = __version__,
        engine: Engine | None = None,
    ) -> None: ...
```

## 模块设计

### permission 子包

**职责：** 前四层判定、配置加载与合并、黑名单、沙箱、规则匹配、模式矩阵、本地规则写入。

**关键点：**
- **check 流水线（F6，短路）**：
  1. `cat == Category.EXEC and target != "" and hits_blacklist(target)` → `DENY`（N1，最高优先级，bypass 也拦）。
  2. 文件类（`is_file`）：`not ok` → `DENY`（路径参数不可解析）；否则 `not sandbox_ok(target)` → `DENY`。
  3. 规则引擎：按 `local → project → user` 顺序，每层 `match(friendly, target)`；命中 allow→Allow、deny→Deny，就近命中即返回。
  4. 未命中 → `mode_fallback(mode, cat)` → Allow 或 Ask。
- **黑名单（F1/N1）**：模块级一组编译好的 `re.Pattern`，匹配命令串。示例模式：`rm -rf /`、`dd of=/dev/`、fork bomb、`mkfs.`、重定向覆盖磁盘设备、`chmod -R 777 /` 等。注释标明「启发式、非完备、不可配置放开」。
- **沙箱（F2/N2）**：`sandbox_ok(path)`：空 path 视为 root；相对路径相对 root；`resolved = eval_symlinks_or_ancestor(abs_path)`（存在则 `Path.resolve(strict=True)`；不存在则逐级回退到最近已存在祖先解析后拼接剩余段）；返回 `resolved == root or resolved.startswith(root + os.sep)`。用 `pathlib` / `os.sep`，不硬编码 `/`。
- **规则解析**：`parse_rule("Bash(git *)")` → `Rule(tool="Bash", pattern="git *", allow=True)`；`parse_rule("Read")` → `pattern=""` 全匹配。加载时 allow/deny 两列分别解析；非法条目跳过并降级（N5）。
- **匹配（match_pattern）**：命令串用 glob；`*` 匹配任意字符（含空格），`**` 等价 `*`；文件路径按 `/` 分段，`*` 段内任意字符，`**` 跨段（参考 `tool/glob_tool.py` 的实际遍历思路）。
- **persist_local_allow（永久放行）**：据 `extract_target` 生成精确规则（`Bash(<command>)` / `Write(<relpath>)` 等），追加到本地文件 `permissions.allow` 且去重，并同步内存；失败向上抛，由 agent 侧捕获仅记日志不阻断。
- **配置加载**：`load_settings(path)`：文件不存在 → 空 Settings、不抛；`yaml.safe_load` 失败 → 抛 `SettingsError`，由 `new_engine` 降级跳过。`new_engine` 依次加载 user/project/local；`start_mode` 依次取 local/project/user 的 `default_mode`（`parse_mode` 成功者优先，local 优先），皆无 → `Mode.DEFAULT`。

**依赖：** `llm`（`ToolCall`）、`pyyaml`、标准库（`re`、`pathlib`、`json`、`os`）。不依赖 agent/tool/tui。

### agent 模块（`agent/__init__.py`）

**职责：** 在工具执行链接入前四层判定；承担第五层人在回路；模式类型迁移。

**关键点：**
- `Mode` 相关定义从本模块删除，`from endless_code.permission import Mode`；`run` 形参 `mode: Mode`；`mode == Mode.PLAN` 沿用只读工具集 + `plan_reminder` 注入。
- `Agent` 增加 `engine: Engine`；`__init__` 增加形参。
- `ApprovalRequest` 加入 `Event` 联合（直接增加可选字段）。
- `request_approval`：创建 `asyncio.Future[Outcome]`，`yield Event(approval=ApprovalRequest(...))`，然后 `return await respond`；`asyncio.CancelledError` 原样上抛走取消收尾。
- 工具执行：Allow 执行；Deny 统一构造 `ToolResult(tool_call_id=..., content=reason, is_error=True)`；Ask 按 `Outcome` 处理：AllowOnce 执行、AllowForever 先 `persist_local_allow`（异常仅日志）再执行、DenyOnce 构造被拒结果。
- 只读批：逐个 `engine.check(..., True)`；Deny 直接预置 `ToolResult(is_error=True)` 且不进入 `asyncio.gather`；Allow 仍并发执行（只读永不 Ask，N3）。
- 有副作用批：串行 `engine.check(..., False)`；Ask 时暂停等待用户决策。

### tui 模块（`tui/app.py`，不新建 stream/view）

**职责：** 新增待批准交互态；模式切换命令；状态栏模式徽标；全局取消覆盖 approving 态。

**关键点：**
- `EndlessCodeApp.mode: permission.Mode`（初值 `engine.start_mode()`）；加 `engine`、`pending: ApprovalRequest | None`、`approve_cursor: int = 0`。
- `SessionState.APPROVING` 枚举值；`on_key` 在 `APPROVING` 分派 `update_approving`；全局 ctrl+c/esc 分派条件从仅 `STREAMING` 改为 `STREAMING | APPROVING`；approving 态取消时先 `self.pending.respond.set_result(Outcome.DENY_ONCE)`，再取消本轮并收尾。
- `shift+tab`：仅 `IDLE` 生效，`self.mode = next_mode(self.mode)`，`next_mode = Mode((int(m) + 1) % 4)`，循环 `DEFAULT→ACCEPT_EDITS→PLAN→BYPASS→DEFAULT`；写一行提示。
- `submit` 保留 `/plan`（→`Mode.PLAN`）、`/do`（→`Mode.DEFAULT`，固定回 default 并注入执行指令）、`/exit`；不新增 `/mode` 命令。
- `status_bar` 左侧改为常驻显示当前权限模式（取代 provider 名）；右侧模型名 + token 用量不变。
- `ApprovalRequest` 渲染：多行待批准块 `● <动作名>(<参数摘要>)`、灰色触发原因、三行菜单（当前项 `> ` + 高亮），底部灰色键位提示。

### cli / smoke

- `cli.py`：`root = str(Path.cwd().resolve())`；`engine, err = permission.new_engine(root)`；`if err is not None: print(..., file=sys.stderr)` 后继续；`app = EndlessCodeApp(cfg.providers, registry, version=__version__, engine=engine)`。
- `examples/smoke.py`：`cwd = str(Path.cwd().resolve())`；`engine, _ = permission.new_engine(cwd)`；`agent = Agent(provider, new_default_registry(), version, engine)`；`await agent.run(conversation, mode=Mode.BYPASS)`。

## 模块交互

```
cli → permission.new_engine(root) → tui.EndlessCodeApp(..., engine=engine)
TUI ── shift+tab            → self.mode 循环切换 DEFAULT→ACCEPT_EDITS→PLAN→BYPASS→DEFAULT（跨轮保持）
TUI ── begin_turn           → Agent(provider, registry, version, engine).run(conv, self.mode)
  agent.execute_batched(calls, mode):
    decision, reason = engine.check(mode, call, read_only(批类别))   # 前四层
    Allow  → await tool.execute(...)
    Deny   → ToolResult(content=reason, is_error=True) ──回灌──→ conv.add_tool_results
    Ask    → ApprovalRequest(..., respond) ──→ TUI(APPROVING)
                 → TUI responds: respond.set_result(outcome)
                 → ALLOW_FOREVER 时 engine.persist_local_allow(call)
                 → 执行(ALLOW_ONCE/ALLOW_FOREVER) 或回灌(DENY_ONCE)
```

依赖方向（无环）：`tui → {agent, permission, config, llm, ...}`；`agent → {permission, llm, tool, conversation, prompt}`；`permission → llm`。`llm` 不 import permission。

## 文件组织

```text
endless-code/
├── src/endless_code/permission/
│   ├── __init__.py           # 新：Mode 四档 + str/parse_mode；Decision/Category/Outcome；Engine/new_engine/持久化导出
│   ├── engine.py             # 新：Engine、new_engine、check 前四层流水线、mode_fallback、start_mode
│   ├── blacklist.py          # 新：内置危险命令正则集 + hits_blacklist（不可配，N1）
│   ├── sandbox.py            # 新：resolve_root、sandbox_ok、eval_symlinks_or_ancestor（N2）
│   ├── rule.py               # 新：Rule/RuleSet、parse_rule、match、match_pattern（glob）
│   ├── settings.py           # 新：Settings YAML、load_settings、to_rule_set、friendly_name、categorize、extract_target
│   └── persist.py            # 新：rule_for、persist_local_allow（写本地层文件）
├── src/endless_code/agent/__init__.py  # 改：删 Mode（迁 permission）；Agent 加 engine；执行链接入 check；request_approval；ApprovalRequest 事件；Deny 用 ToolResult 构造
├── src/endless_code/tui/app.py          # 改：mode→permission.Mode、加 engine/pending/approve_cursor；Approving 态分派；全局 ctrl+c/esc 覆盖 approving；shift+tab 循环模式
├── src/endless_code/cli.py              # 改：构造 permission.Engine 注入 tui
├── examples/smoke.py                    # 改：cwd + 构造引擎、Mode.BYPASS 运行
├── tests/
│   ├── test_permission_*.py             # 新：黑名单/沙箱(含祖先回退)/规则/优先级/矩阵/加载降级/解析失败
│   ├── test_agent.py                    # 改：权限集成(Allow/Deny/Ask/永久)、保序、只读并发不退化、取消、模式迁移
│   └── test_tui.py                      # 改：shift+tab 循环、approval 态按键回传、Esc 取消兜底、状态栏模式、模式跨轮保持
├── .gitignore                           # 改：追加 .endless-code/settings.local.yaml
└── .endless-code/settings.yaml.example  # 新：权限配置示例
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 权限判定落点 | 独立 `permission` 子包（前四层） + agent 编排层（第五层） | 与 provider 解耦（N6 免费）；逻辑内聚、可单测；不污染 tool/llm。 |
| 五层短路 | `check` 顺序 黑名单→沙箱→规则→模式，单函数 early-return；Ask 作第五层信号 | 满足 F6；黑名单/沙箱按类别跳过；规则就近命中即返回；人在回路在 agent。 |
| 黑名单不可配 | 模块内编译好的 `re.Pattern` 常量列表、无加载入口 | N1：任何配置/模式都碰不到；bypass 也拦。 |
| 黑名单完备性 | 启发式、显式声明非完备 | 不可能穷尽危险命令；纵深由沙箱、规则 + 人在回路补。 |
| 沙箱解析顺序 | 先 `Path.resolve(strict=True)`（或最近祖先）再前缀比对 | N2：防软链接逃逸；新建文件按已存在祖先判，避免误判。 |
| 沙箱不管 bash | bash 不做路径围栏 | 无法可靠静态解析任意命令的文件访问；交黑名单、规则、模式兜底。 |
| glob/grep 沙箱盲区 | extract_target 取搜索根 `path` 做围栏；pattern 不参与沙箱 | glob/grep 遍历由工具内部 `Path.glob/rglob`（不跟随目录软链接）限制；沙箱对搜索根尽力围栏。 |
| Mode 归属 | 迁到 `permission` 模块、四档统一 | 权限概念共享给 agent/tui；四档全循环含 BYPASS。 |
| 模式切换方式 | Shift+Tab 循环四档；保留 `/plan`、`/do` | 与项目既有 Plan Mode 兼容；`/do` 固定回 default；不新增 `/mode` 命令。 |
| 状态栏左侧内容 | 常驻显示当前权限模式，取代 provider 名 | 用户拍板「别展示 provider 名、展示权限模式」；右侧模型名 + token 用量不变。 |
| plan 语义 | 沿用现有 Plan Mode 硬化：仅只读工具集 + plan reminder；`/do` 执行 | `/plan` 与 default_mode=plan 都按 `Mode.PLAN` 应用。 |
| 模式兜底值域 | 只产 Allow/Ask（无 Deny 档） | 用户拍板矩阵；Deny 仅来自黑名单/沙箱/deny 规则/人在回路拒绝。 |
| 规则优先级 | 本地>项目>用户；同一层 deny 优先 allow | 用户拍板「越靠近会话越优先」；deny 优先更安全。 |
| 永久放行落点 | 写 `.endless-code/settings.local.yaml`（gitignore） | 不进 git、不影响队友；对齐 Claude Code don't-ask-again。 |
| 自动规则泛化 | 不泛化，只生成精确规则 | 自动猜泛化有误放行风险；泛化交由用户手写配置。 |
| 规则命名 | 友好名 Bash/Read/Write/Edit/Glob/Grep → 内部名映射 | 与用户示例一致，规则可读。 |
| 参数解析失败归属 | 文件类不可解析→Deny；bash 缺 command→Ask；未知工具→Exec/Ask | N7/AC15 安全默认，绝不静默 Allow。 |
| 人在回路选项目 | 三选一（允许本次/永久/拒绝），菜单 ↑↓+回车、数字键直选、默认高亮允许本次 | 用户拍板；永久精确写本地配置。 |
| 人在回路回传 | ApprovalRequest 事件 + agent 内 `await asyncio.Future` | Textual 单线程事件循环；future 可被用户选择/取消解阻塞（N4）。 |
| approving 态取消 | 全局 ctrl+c/esc 分派覆盖 Approving | 否则 approving 态 ctrl+c 会退出程序，违反 N4。 |
| 会话/永久规则写入位置 | agent 在 Loop 内调引擎；TUI 只回传 Outcome | 引擎状态变更集中一处；职责清晰。 |
| 只读权限检查 | 批内逐个 check，但只读永不 Ask | N3：保留既有并发；只读最多被沙箱/deny 规则拦为 Deny，无交互。 |
| settings 与 config 分离 | 新增 `.endless-code/settings.yaml` 而非塞进 config.yaml | provider 凭据与权限规则职责不同；config.yaml 已 gitignore 含密钥；项目级权限设置可提交。 |
| smoke 运行模式 | Mode.BYPASS、基于 cwd | 非交互无法人在回路；bypass 跳过 Ask（黑名单/沙箱仍在）；用例落 cwd 内。 |
| new_engine 失败处理 | 致命错误（仅 resolve_root）也返回非 None 安全引擎 + err | cli 注入永不 None；check 不抛；配置格式错只降级（N5）。 |