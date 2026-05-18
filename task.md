# 权限系统 Tasks

> 包名：`endless_code`（Python 3.12+）。源码位于 `src/endless_code/`，新增子包 `endless_code.permission`。

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/endless_code/permission/__init__.py` | 包对外门面：导出 `Mode`/`Decision`/`Category`/`Outcome`/`Engine`/`new_engine`/`persist_local_allow`/`ApprovalError`；`Mode` 四档 + `str`/`parse_mode` |
| 新建 | `src/endless_code/permission/blacklist.py` | 内置危险命令正则集 + `hits_blacklist`（不可配，N1） |
| 新建 | `src/endless_code/permission/sandbox.py` | `resolve_root`、`sandbox_ok`、`eval_symlinks_or_ancestor`（N2） |
| 新建 | `src/endless_code/permission/rule.py` | `Rule`/`RuleSet`、`parse_rule`、`match`、`match_pattern`（glob） |
| 新建 | `src/endless_code/permission/settings.py` | `Settings` YAML、`load_settings`、`to_rule_set`、`friendly_name`、`categorize`、`extract_target` |
| 新建 | `src/endless_code/permission/engine.py` | `Engine`、`new_engine`、`check` 前四层流水线、`mode_fallback`、`start_mode` |
| 新建 | `src/endless_code/permission/persist.py` | `rule_for`、`persist_local_allow`（写本地层文件） |
| 新建 | `tests/test_permission_*.py` | 黑名单/沙箱（含祖先回退）/规则/优先级/矩阵/加载降级/解析失败单测 |
| 改 | `src/endless_code/agent/__init__.py` | 删 `Mode`（迁 permission）；`Agent` 加 `engine`；执行链接入 `check`；`request_approval`；`ApprovalRequest` 事件；Deny 用 `ToolResult` 构造 |
| 改 | `tests/test_agent.py` | 权限集成(Allow/Deny/Ask/永久)、保序回灌、只读并发不退化、取消、模式迁移 |
| 改 | `src/endless_code/tui/app.py` | `mode`→`permission.Mode`、加 `engine`/`pending`/`approve_cursor`；`APPROVING` 态分派；全局 ctrl+c/esc 覆盖 approving；`shift+tab` 循环模式 |
| 改 | `tests/test_tui.py` | `shift+tab` 循环、approval 态按键回传、Esc 取消兜底、状态栏模式、模式跨轮保持；既有 `/plan`·`/do` 用例适配新 `Mode` |
| 改 | `src/endless_code/cli.py` | 构造 `permission.new_engine(root)` 注入 `tui.EndlessCodeApp` |
| 改 | `examples/smoke.py` | 新增 cwd、构造引擎、`Mode.BYPASS` 运行 |
| 改 | `.gitignore` | 追加 `.endless-code/settings.local.yaml` |
| 新建 | `.endless-code/settings.yaml.example` | 权限配置示例（default_mode + allow/deny） |

---

## T1: permission 基础类型

**文件：** `src/endless_code/permission/__init__.py`
**依赖：** 无

**步骤：**
1. `class Mode(IntEnum)`：`DEFAULT`/`ACCEPT_EDITS`/`PLAN`/`BYPASS`。
2. `Mode.__str__` → `"default"`/`"acceptEdits"`/`"plan"`/`"bypassPermissions"`。
3. `def parse_mode(s: str) -> tuple[Mode, bool]`：大小写不敏感识别四档名，未知返回 `(Mode.DEFAULT, False)`。
4. `class Decision(IntEnum)`：`ALLOW`/`DENY`/`ASK`；`class Category(IntEnum)`：`READ`/`WRITE`/`EXEC`。
5. `class Outcome(IntEnum)`：`DENY_ONCE`/`ALLOW_ONCE`/`ALLOW_FOREVER`（人在回路三选一）。
6. 暴露后续会被 agent/tui import 的符号：`Mode`、`Decision`、`Category`、`Outcome`、`Engine`、`new_engine`、`persist_local_allow`、`ApprovalError`。

**验证：** `python -c "from endless_code.permission import Mode, parse_mode, Outcome; assert parse_mode('bypassPermissions') == (Mode.BYPASS, True); assert parse_mode('x') == (Mode.DEFAULT, False)"` 跑通。

## T2: 危险命令黑名单

**文件：** `src/endless_code/permission/blacklist.py`
**依赖：** 无

**步骤：**
1. 模块级 `_BLACKLIST: list[re.Pattern]`，编译一组高危模式：`rm -rf /`、`rm -fr ~`、`dd of=/dev/`、fork bomb、`mkfs.`、重定向覆盖磁盘设备、`chmod -R 777 /` 等。
2. `def hits_blacklist(command: str) -> bool`：`any(p.search(command) for p in _BLACKLIST)`。
3. 顶部 docstring 声明「启发式、非完备、不可配置放开」（N1）。

**验证：** `python -c "from endless_code.permission.blacklist import hits_blacklist; assert hits_blacklist('rm -rf /'); assert not hits_blacklist('git status')"` 跑通。

## T3: 路径沙箱

**文件：** `src/endless_code/permission/sandbox.py`
**依赖：** T1（Engine）

**步骤：**
1. `def resolve_root(root: str) -> str`：`Path(root).expanduser().resolve(strict=True)`（失败抛 `FileNotFoundError`）。
2. `def eval_symlinks_or_ancestor(abs_path: str) -> str`：存在则 `Path.resolve(strict=True)`；不存在则逐级取最近已存在祖先 `resolve(strict=True)` 后拼接剩余段。
3. `def sandbox_ok(engine: Engine, path: str) -> bool`：空 path 视为 root；相对路径相对 `engine.root` 解析为绝对；`resolved = eval_symlinks_or_ancestor(abs_path)`；返回 `resolved == root or resolved.startswith(root + os.sep)`。用 `pathlib` / `os.sep`，不硬编码 `/`。

**验证：** 单测用 `tmp_path` 造 root 内/外文件、符号链接、多级未创建中间目录，断言 root 内放行、`/etc/passwd`/`../outside`/指向外部软链接被拒。

## T4: 规则与匹配

**文件：** `src/endless_code/permission/rule.py`
**依赖：** T1

**步骤：**
1. `@dataclass class Rule`：`tool`/`pattern`/`allow`；`@dataclass class RuleSet`：`allow`/`deny` 列表。
2. `def parse_rule(s: str) -> tuple[Rule, bool]`：解析 `Tool(pattern)` 或 `Tool`；括号不配对/空串非法返回 `(Rule("", "", False), False)`。
3. `def match_pattern(pattern: str, target: str) -> bool`：`pattern == ""` → True；命令串用 glob（`*` 任意字符、`**` 等价 `*`）；文件路径按 `/` 分段，`*` 段内任意字符、`**` 跨段。
4. `RuleSet.match(friendly, target)`：先遍历 deny 再 allow；命中返回 `(Decision.DENY/ALLOW, True)`，否则 `(Decision.ALLOW, False)`。

**验证：** 单测 `Bash(git *)` 放行 `git status`、不放过 `npm i`；`Write(src/**)` 放行 `src/a/b.py`、不放 `docs/x`；同层 deny 优先。

## T5: 配置加载与映射

**文件：** `src/endless_code/permission/settings.py`
**依赖：** T1、T4

**步骤：**
1. `@dataclass class Settings`：`default_mode: str`、`permissions: PermissionsBlock`。
2. `def load_settings(path: str) -> Settings`：文件不存在 → 空 `Settings`；`yaml.safe_load` 失败 → 抛 `SettingsError`（调用方降级，N5）。
3. `def to_rule_set(s: Settings) -> RuleSet`：allow/deny 各条 `parse_rule`，非法条目跳过。
4. `def friendly_name(internal: str) -> str`：`bash→Bash, read_file→Read, write_file→Write, edit_file→Edit, glob→Glob, grep→Grep`；未知原样返回。
5. `def categorize(internal: str, read_only: bool) -> Category`：`read_only→READ`；否则 `write_file/edit_file→WRITE`；其余（含 bash、未知）→EXEC（N7 最严）。
6. `def extract_target(call: ToolCall) -> tuple[str, bool, bool]`：`read_file/write_file/edit_file` 取 `path`（is_file=True）；`glob/grep` 取搜索根 `path`，空→"."（is_file=True）；`bash` 取 `command`（is_file=False）；未知工具→("", False, False)；`json.loads` 失败或缺必填字段→`ok=False`。

**验证：** 缺失文件得空且不抛；非法 YAML 抛 `SettingsError`；`to_rule_set` 跳过非法项；`friendly_name`/`categorize`/`extract_target` 各分支正确。

## T6: 引擎与前四层流水线

**文件：** `src/endless_code/permission/engine.py`
**依赖：** T1、T2、T3、T4、T5

**步骤：**
1. `@dataclass class Engine`：`root`、`blacklist`、`user/project/local RuleSet`、`local_path`、`start_mode`。
2. `def new_engine(root: str) -> tuple[Engine, Exception | None]`：
   - `try: root = resolve_root(root) except Exception as e`：失败时 `engine.root` 退化为传入值、四层规则空、`start_mode=Mode.DEFAULT`，仍返回非 None engine + e。
   - 加载三层：user=`~/.config/endless-code/settings.yaml`、project=`<root>/.endless-code/settings.yaml`、local=`<root>/.endless-code/settings.local.yaml`；各 `load_settings`→`to_rule_set`；单文件读/解析失败仅降级跳过。
   - `local_path = <root>/.endless-code/settings.local.yaml`。
   - `start_mode`：依次取 local/project/user 的 `default_mode`（parse_mode 成功者优先，local 优先），皆无→`Mode.DEFAULT`。
3. `def mode_fallback(mode: Mode, cat: Category) -> Decision`：只产 Allow/Ask，矩阵见 spec F5。
4. `Engine.check(mode, call, read_only)`：
   - `cat = categorize(call.name, read_only)`；`friendly = friendly_name(call.name)`；`target, is_file, ok = extract_target(call)`。
   - ① `cat == EXEC and target != "" and hits_blacklist(target)` → `(DENY, "命中危险命令黑名单：<target>")`。
   - ② `is_file`：`not ok` → `(DENY, "无法解析文件路径参数，安全拒绝")`；否则 `not sandbox_ok(...)` → `(DENY, "路径在项目目录之外：<target>")`。
   - ③ 按 `local → project → user` 顺序 `match(friendly, target)`；命中即返回。
   - ④ `mode_fallback(mode, cat)` → `(ALLOW, "")` 或 `(ASK, "<mode> 模式下 <category> 类操作需确认")`。
5. `Engine.start_mode()` 返回 `self._start_mode`。

**验证：** 单测逐层短路、跳层放行、模式矩阵、三级优先级、`resolve_root` 失败仍得非 None 引擎。

## T7: 本地永久规则写入

**文件：** `src/endless_code/permission/persist.py`
**依赖：** T5、T6

**步骤：**
1. `def rule_for(call: ToolCall) -> tuple[Rule, str, bool]`：据 `extract_target` + `friendly_name` 生成精确规则；`Bash(<command>)` / `Write(<relpath>)` 等；glob 元字符转义；解析失败→`(Rule("","",False), "", False)`。
2. `Engine.persist_local_allow(call)`：`load_settings(local_path)`（缺失则空）→ 追加规则到 `permissions.allow` 并去重 → `yaml.safe_dump` → 确保父目录存在 → 写文件 → 同步 `self.local.allow`。异常向上抛，由 agent 侧捕获仅记日志不阻断。

**验证：** 单测（`tmp_path` 作 root）：`persist_local_allow` 后本地文件含 allow 条目；重新 `new_engine` 后同一调用判 Allow；重复持久化不重复写。
## T8: agent 接入权限（模式迁移 + 判定 + 人在回路）

**文件：** `src/endless_code/agent/__init__.py`
**依赖：** T6、T7

**步骤：**
1. **模式迁移**：删除 agent 内 `Mode` 定义，`from endless_code.permission import Mode`；`run` 形参 `mode: Mode`；`mode == Mode.PLAN` 处不变（defs 选只读、plan_reminder 注入）。
2. `Agent.__init__` 增加 `engine: Engine | None = None`；未传入时用 `permission.new_engine(str(Path.cwd().resolve()))[0]`。
3. 新增 `@dataclass class ApprovalRequest`：`name`/`args`/`reason`/`respond`；`Event` 增加 `approval: ApprovalRequest | None = None`。
4. `request_approval(call, reason) -> Outcome`：创建 `asyncio.Future[Outcome]`，发 `Event(approval=ApprovalRequest(...))`，`await respond`；`CancelledError` 原样上抛走取消收尾。
5. `execute_batched`（沿用现有 `_execute_events` 结构，增加 mode 参数）：
   - 只读批：每个 `k` 先 `engine.check(mode, calls[k], True)`；Deny 直接预置 `ToolResult(..., is_error=True)` 且不纳入 `asyncio.gather`；Allow 照旧并发；不产 ApprovalRequest。
   - 串行批：先 `engine.check(mode, calls[i], False)`；Allow 执行；Deny 构造被拒结果；Ask → `outcome = await request_approval(...)`；`ALLOW_ONCE` 执行；`ALLOW_FOREVER` 先 `persist_local_allow`（异常仅 logger.warning）再执行；`DENY_ONCE` 构造被拒结果。
   - Deny 与 Allow 项结果按原调用序与各自 `tool_call_id` 配对，互不串位。
6. `run` 调用 `_execute_events(calls, cancel, execution, mode)`，并把 `self.engine` 传给执行链路。

**验证：** `python -c "from endless_code.agent import Agent"` 不抛；轻量自检：表驱动断言 `request_approval` 在 `asyncio.CancelledError` 抛出时正确传播、不阻塞。

## T9: agent 单测

**文件：** `tests/test_agent.py`
**依赖：** T8

**步骤：**
1. 既有用例适配：`Agent(provider, registry, version, engine)`；`Mode.NORMAL`→`Mode.DEFAULT`；fake provider 签名不变。
2. 新增：
   - **Deny 回灌不中断**：构造沙箱外路径或 deny 规则 → 工具结果 `is_error`，Loop 继续到次轮。
   - **保序回灌**：单批含「被拒调用 + 放行调用」→ 结果按原 `calls` 下标、各自 `tool_call_id` 正确配对。
   - **Ask 人在回路**：default 下请求 `write_file` → 收到 `ApprovalRequest` → `respond.set_result(Outcome.ALLOW_ONCE/DENY_ONCE)`，断言执行/回灌生效。
   - **永久放行**：选 `ALLOW_FOREVER` → 断言 `local_path` 文件被写、含 allow 条目。
   - **只读并发不退化**：一批只读不产生任何 `ApprovalRequest`；被沙箱拦的只读得 errResult、其余仍并发完成。
   - **取消**：在 `ApprovalRequest` 等待中 `task.cancel()` → Loop 干净收尾、历史合法、无挂起 task。
   - **plan 迁移**：`Mode.PLAN` 仍只放只读工具、注入计划提醒。

**验证：** `pytest tests/test_agent.py -q`；`pytest --timeout=30 tests/test_agent.py` 无超时；`python -X dev` 跑测试无 `RuntimeWarning: coroutine ... was never awaited`。

## T10: TUI 接入（模式切换 + 待批准态）

**文件：** `src/endless_code/tui/app.py`
**依赖：** T8

**步骤：**
1. `EndlessCodeApp.mode: permission.Mode`；加 `engine`、`pending: ApprovalRequest | None`、`approve_cursor: int = 0`；`__init__` 增加 `engine: Engine | None = None`，默认 `permission.new_engine(str(Path.cwd().resolve()))[0]`，`self._mode = engine.start_mode()`。
2. `SessionState.APPROVING` 枚举值；`on_key` 在 `APPROVING` 分派 `update_approving`。
3. **全局 ctrl+c/esc**：条件从 `self._state == SessionState.STREAMING` 改为 `self._state in (SessionState.STREAMING, SessionState.APPROVING)`；approving 态取消时先 `self.pending.respond.set_result(Outcome.DENY_ONCE)` 再取消本轮。
4. **shift+tab**：新增 `case "shift+tab":`（仅 `IDLE` 生效）`self._mode = next_mode(self._mode)` 并写提示；`next_mode(m: Mode) -> Mode` 为模块小函数，`Mode((int(m) + 1) % 4)`，循环 `DEFAULT→ACCEPT_EDITS→PLAN→BYPASS→DEFAULT`。
5. `_consume_agent_events`：`if event.approval is not None:` → 保存 `self.pending`、重置光标、切 `APPROVING`、渲染待批准块；等待用户回传后继续 `async for`。
6. `update_approving(key)`：`up`/`k`、`down`/`j` 循环移动光标；`enter`/`space` 提交当前项；数字键 `1`/`2`/`3` 直选；`y`=ALLOW_ONCE、`n`/`d`=DENY_ONCE；提交后 `respond.set_result(outcome)`、回 `STREAMING`、清 pending。
7. `submit`：保留 `/plan`（→`Mode.PLAN`）、`/do`（→`Mode.DEFAULT`，固定回 default 并触发执行）、`/exit`；不新增 `/mode` 命令。
8. `status_bar`：左侧常驻显示当前权限模式（`DEFAULT`/`ACCEPT EDITS`/`PLAN`/`BYPASS`，取代 provider 名）；右侧模型名 + token 用量不变。

**验证：** `python -m endless_code` 启动可进 idle；自动化部分见 T11。

## T11: TUI 单测

**文件：** `tests/test_tui.py`
**依赖：** T10

**步骤：**
1. 既有 `/plan`·`/do` 用例适配 `permission.Mode`（`Mode.PLAN`/`Mode.DEFAULT`）。
2. 新增（Textual `App.run_test()` + `Pilot`）：
   - 连续 `shift+tab`（idle）→ `app.mode` 依次 `DEFAULT→ACCEPT_EDITS→PLAN→BYPASS→DEFAULT`，每次有提示写入 RichLog。
   - 注入 `ApprovalRequest` 事件 → `app.state == APPROVING`、`pending` 已设、`approve_cursor == 0`；`down`+`enter` → respond 收到 `ALLOW_FOREVER`；数字键 `1`→`ALLOW_ONCE`、`3`→`DENY_ONCE`。
   - approving 态按 `escape`/`ctrl+c` → respond 收到兜底 `DENY_ONCE`，应用未退出。
   - `status_bar` 左侧显示对应模式名，不包含 provider 名。
   - **模式跨轮保持**：Shift+Tab 切到 `ACCEPT_EDITS` 后再 `begin_turn`，`app.mode` 仍为 `ACCEPT_EDITS`。

**验证：** `pytest tests/test_tui.py -q`（带 pytest-asyncio + Textual 测试工具）。

## T12: cli / smoke / 配置文件接线

**文件：** `src/endless_code/cli.py`、`examples/smoke.py`、`.gitignore`、`.endless-code/settings.yaml.example`
**依赖：** T6、T8、T10

**步骤：**
1. `cli.py`：`root = str(Path.cwd().resolve())`；`engine, err = permission.new_engine(root)`；`if err is not None: print("权限引擎降级:", err, file=sys.stderr)` 后继续；`app = EndlessCodeApp(cfg.providers, registry, version=__version__, engine=engine)`。
2. `examples/smoke.py`：`cwd = str(Path.cwd().resolve())`；`engine, _ = permission.new_engine(cwd)`；`agent = Agent(provider, new_default_registry(), __version__, engine)`；`await agent.run(conversation, mode=Mode.BYPASS)`。
3. `.gitignore`：追加 `.endless-code/settings.local.yaml`。
4. `.endless-code/settings.yaml.example`：示例 `default_mode: default`；`permissions.allow: ["Bash(git *)", "Bash(pytest)"]`；`permissions.deny: ["Bash(rm *)", "Read(.env)", "Write(.env)"]`；注释说明三层文件与优先级，并注明只读类默认即 Allow，allow 规则主要用于提前放行 Bash/Write，deny 规则可对只读做围栏。

**验证：** `python -m endless_code` 能正常启动进对话；`python examples/smoke.py` 在 `Mode.BYPASS` 下不阻塞、跑完；`git check-ignore .endless-code/settings.local.yaml` 命中。

## T13: 全量编译测试与规范

**文件：** —
**依赖：** T1—T12

**步骤：**
1. `python -m ruff format --check .` 通过（本地 `ruff format .` 已统一）。
2. `python -m ruff check .` 无告警（permission 子包按本地包分组，import 顺序正确）。
3. `python -m pytest` 通过；`pytest --timeout=30 tests/test_agent.py tests/test_permission_*.py tests/test_tui.py` 无超时。
4. （可选）`python -m mypy src/endless_code` 通过（含 permission 子包）。
5. 确认 `.endless-code/settings.local.yaml` 已被 gitignore；检索输出无 api_key 明文。
6. 端到端：default 下写文件触发 Ask 弹窗；Shift+Tab 切到 bypassPermissions 后不再 Ask、状态栏左侧显示 `BYPASS`；`rm -rf /` 在 bypass 下仍被拦。

**验证：** 全部通过。

## 执行顺序

```text
T1(类型) → T2(黑名单) → T3(沙箱) → T4(规则) → T5(配置/映射) → T6(引擎/流水线) → T7(规则写入)
T6,T7 → T8(agent 接入) → T9(agent 单测)
T8 → T10(TUI 接入) → T11(TUI 单测)
T6,T8,T10 → T12(cli/smoke/配置)
全部 → T13(ruff/pytest/mypy/端到端)
```

（依赖：T5→{T1,T4}；T6→{T1,T2,T3,T4,T5}；T7→{T5,T6}；T8→{T6,T7}；T9→T8；T10→T8；T11→T10；T12→{T6,T8,T10}；T13→全部。）