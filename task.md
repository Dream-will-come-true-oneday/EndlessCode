~~~Markdown
# MCP 客户端 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 改   | `pyproject.toml` | `dependencies` 增加 `"mcp>=1.0"`；`uv sync` / `pip install -e .` 同步 |
| 新建 | `src/endless_code/mcp/__init__.py` | 暴露 `Config` / `ServerConfig` / `Manager` / `load_config` / `new_manager` |
| 新建 | `src/endless_code/mcp/config.py` | `Config` / `ServerConfig`、`load_config`、`_load_file`、`_expand_vars`、`_apply_expansion`、`_merge_servers`、`_validate_server` |
| 新建 | `tests/test_mcp_config.py` | 两层合并 / `${VAR}` 展开 / 字段校验 / 降级 单测 |
| 新建 | `src/endless_code/mcp/tool.py` | `CallerSession` Protocol、`McpTool`、`adapt_tool`、`execute`、非 text 块告警 once set |
| 新建 | `tests/test_mcp_tool.py` | 命名拼接 / 禁用字符 / Execute 成功 / 远端 IsError / 超时 / 协议错 / 非 text 块跳过 单测 |
| 新建 | `src/endless_code/mcp/manager.py` | `Manager`、`_Session`、`new_manager`（`asyncio.gather` 并发 + 30s 超时）、`close`（5s 兜底）、`tools`；模块级 `connect_timeout` / `close_timeout` |
| 新建 | `tests/test_mcp_manager.py` | 连接成功 / 失败 / 超时、`close` 不死锁、并发写共享状态安全 单测 |
| 新建 | `tests/test_mcp_http.py` | HTTP 端到端：uvicorn 起真实本地 HTTP MCP server，验证连接 / 列工具 / 调用 / 自定义 headers 到达 server / 干净关闭（AC5） |
| 改   | `src/endless_code/cli.py` | 装配 `load_config`、`new_manager`、注册 MCP 工具、`finally: await mgr.close()` |
| 新建 | `docs/mcp/mcp-servers.example.yaml` | 配置示例（含 stdio / http 各一个，用 `${VAR}`） |

---

## T1: 添加 MCP Python SDK 依赖

**文件：** `pyproject.toml`、`uv.lock`（自动生成）
**依赖：** 无
**步骤：**
1. 在 `[project]` 的 `dependencies` 列表追加 `"mcp>=1.0"`。
2. 在仓库根执行 `uv sync`（或 `pip install -e .`）；查看 `uv.lock` 或 `pip list` 确认 `mcp` 与其传递依赖（`pydantic` 等）已装好。
3. 写一段最小试导入（可直接放进后续 `tool.py` 的 import 中）：
   ```python
   from mcp import ClientSession, StdioServerParameters
   from mcp.client.stdio import stdio_client
   from mcp.client.streamable_http import streamablehttp_client
   import mcp.types as mtypes
   ```
   并在 Python REPL 跑一次 `import endless_code.mcp` 雏形，验证可用。

**验证：** `python -c "import mcp; print(mcp.__version__ if hasattr(mcp,'__version__') else 'ok')"` 输出非错误；`uv pip list | grep mcp` 看到包名。

## T2: 配置类型与加载（含两层合并 + 变量展开 + 字段校验）

**文件：** `src/endless_code/mcp/config.py`、`src/endless_code/mcp/__init__.py`、`tests/test_mcp_config.py`
**依赖：** T1
**步骤：**
1. 定义对外类型 `ServerConfig`、`Config`（见 plan.md「核心数据结构」），用 `@dataclass`。
2. 定义内部 `@dataclass class _RawServer`（含全部字段：`type` / `command` / `args` / `env` / `url` / `headers`，全部 Optional 或带默认值）。
3. `_load_file(path: Path) -> dict[str, _RawServer]`：
   - `path.exists() is False` → 返回 `{}`；
   - `yaml.safe_load(path.read_text())` 失败（含 IOError / `yaml.YAMLError`）→ stderr 告警 `[mcp] warn: load <path> failed: <err>` + 返回 `{}`（调用方降级）；
   - 取 `data.get("mcp_servers") or {}`，逐项映射到 `_RawServer`（缺字段用默认）。
4. `_expand_vars(s: str) -> tuple[str, list[str]]`：
   - 正则 `re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")` 匹配；
   - 用 `os.environ.get(var, "")` 取值；找不到（即 `var not in os.environ`）则记录到 `undefined`。
5. `_apply_expansion(name: str, srv: _RawServer) -> None`：
   - 对 `srv.env`、`srv.headers` 的每个值跑 `_expand_vars`，原地替换；
   - 收集所有 undefined 变量名，去重；首次出现时 `print(f"[mcp] warn: undefined env var ${{{v}}} referenced by server {name}", file=sys.stderr)`。
6. `_merge_servers(user: dict[str, _RawServer], project: dict[str, _RawServer]) -> dict[str, _RawServer]`：
   - 新建 dict，先 `update(user)`，再 `update(project)`（同名直接整对象覆盖）。
7. `_validate_server(name: str, srv: _RawServer) -> ServerConfig | None`：
   - `srv.type` 必为 `"stdio"` 或 `"http"`，否则跳过；
   - `stdio` 必填 `command`；`http` 必填 `url`；缺失则跳过；
   - 违规时 `print(f"[mcp] warn: skip server {name}: {reason}", file=sys.stderr)`；返回 `None`。
8. `load_config(root: str) -> Config`：
   - 用户级 = `Path.home() / ".config" / "endless-code" / "mcp.yaml"`（`Path.home()` 失败时跳过用户层不致错，用 `try/except` 兜底）；项目级 = `Path(root) / ".endless-code" / "mcp.yaml"`。
   - 两层各自 `_load_file`；返回空 dict 即视为该层为空。
   - 对每层各 server 跑 `_apply_expansion`。
   - `_merge_servers` 后逐个 `_validate_server`，收齐合法 server 组装 `Config`。
   - 永不抛出。
9. `src/endless_code/mcp/__init__.py` 中 `from .config import Config, ServerConfig, load_config`。

**验证：** `python -c "from endless_code.mcp import load_config, Config"` 不报错；`pytest tests/test_mcp_config.py` 覆盖：
- 两文件缺失 → `Config.servers` 为空字典、无异常；
- 仅用户级 / 仅项目级 / 都有（同名 server 项目级胜出，断言字段为项目级值）；
- 文件格式非法 → 跳过该层、其它正常加载、`capsys.readouterr().err` 中包含告警；
- `${VAR}` 已定义（用 `monkeypatch.setenv`）→ 展开为环境值；未定义 → 空串 + 告警；`command` / `args` 中含 `${VAR}` → 不展开（保留字面量）；
- type 缺失 / type 非法 / stdio 缺 command / http 缺 url → 该 server 被跳过，其它 server 不受影响。

## T3: 工具适配（McpTool）

**文件：** `src/endless_code/mcp/tool.py`、`tests/test_mcp_tool.py`
**依赖：** T1
**步骤：**
1. `import mcp.types as mtypes`；`from endless_code.tool import Tool, ToolResult`（或对应内置工具协议路径，按现有命名为准）。
2. 定义最小 Protocol `CallerSession` 与 `@dataclass class McpTool`（见 plan.md「核心数据结构」）。
3. 实现 endless-code `Tool` 协议要求的属性 / 方法：`name`（返回 `full_name`）、`description`、`parameters`、`read_only`、`async def execute(args)`。
4. `def adapt_tool(server_name: str, t: mtypes.Tool, session: CallerSession) -> McpTool | None`：
   - `full_name = f"mcp__{server_name}__{t.name}"`；
   - 用包级 `_VALID_NAME = re.compile(r"^[A-Za-z0-9_-]+$")` 校验 `full_name`，不通过 → 返回 `None` + stderr 告警 `[mcp] warn: skip tool <full_name>: name contains illegal characters`。
   - `description = t.description or f"来自 MCP server {server_name} 的工具 {t.name}"`。
   - `parameters`：`t.inputSchema` 已是 `dict[str, Any]`，做浅拷贝 `dict(t.inputSchema)`；若空 / None → `{"type": "object"}` 兜底。
   - `read_only = bool(getattr(t, "annotations", None) and t.annotations.readOnlyHint)`。
5. `async def McpTool.execute(self, args: dict[str, Any] | None) -> ToolResult`：
   - `arg_map = args or None`（空 dict 视作无参数）；不再 try/except 解析 JSON——上层已经传 dict。
   - ```python
     try:
         result = await asyncio.wait_for(
             self.caller.call_tool(self.remote_name, arg_map),
             timeout=30,
         )
     except asyncio.TimeoutError:
         return ToolResult(content="MCP 工具调用超时 (30s)", is_error=True)
     except Exception as e:
         return ToolResult(content=f"MCP 工具调用失败: {e}", is_error=True)
     ```
   - 遍历 `result.content`：
     - `isinstance(block, mtypes.TextContent)` → `texts.append(block.text)`；
     - 非 text 块：通过包级 `_non_text_warn_once: set[str]` 对 `full_name` 做 `if full_name not in _non_text_warn_once: _non_text_warn_once.add(full_name); print("[mcp] warn: tool ... returned non-text content blocks (dropped)", file=sys.stderr)`。
   - 返回 `ToolResult(content="\n".join(texts), is_error=bool(result.isError))`。

**验证：** `pytest tests/test_mcp_tool.py` 覆盖：
- 合法 server 名 + 工具名 → `adapt_tool` 返回 `McpTool` 实例；含 `.` / `@` 等非法字符 → 返回 `None` + `capsys` 告警；
- description 空 → 兜底文案出现；schema None → `{"type": "object"}`；schema 透传成功；
- `t.annotations is None` → `read_only is False`（不报错）；`readOnlyHint=True` → `read_only is True`；
- Execute：注入 stub `CallerSession`（用 `class StubSession:` + `async def call_tool(...)` 返回构造好的 `CallToolResult`），覆盖：
  - 成功（多 text 块拼接，断言 `"\n".join` 顺序）；
  - 远端 `isError=True` 映射；
  - `call_tool` 抛异常 → `is_error=True`，content 含 `MCP 工具调用失败`；
  - 阻塞至超时（stub `await asyncio.Event().wait()` + 模块级 timeout `monkeypatch` 改 200ms）→ `is_error=True`，content 含 `超时`；
  - 非 text 块跳过 + `texts` 仅含 text + `_non_text_warn_once` 同 `full_name` 多次调用只告警一次。

## T4: 连接管理器（Manager）

**文件：** `src/endless_code/mcp/manager.py`、`src/endless_code/mcp/__init__.py`（追加导出）、`tests/test_mcp_manager.py`
**依赖：** T2、T3
**步骤：**
1. 模块级变量（非常量，便于单测改）：
   ```python
   connect_timeout: float = 30.0
   close_timeout: float = 5.0
   ```
2. 定义 `@dataclass class _Session(name: str, session: ClientSession)` 与 `class Manager`（见 plan.md「核心数据结构」，含 `_stack: AsyncExitStack`、`_lock: asyncio.Lock`）。
3. `async def new_manager(cfg: Config, version: str) -> Manager`：
   - `mgr = Manager()`；`mgr._stack = AsyncExitStack(); await mgr._stack.__aenter__()`（或在内部封装，让 `close` 调 `aclose`）。
   - `tasks = [asyncio.create_task(_connect_one(mgr, name, srv, version)) for name, srv in cfg.servers.items()]`；
   - `await asyncio.gather(*tasks, return_exceptions=True)`（异常吸收：`_connect_one` 内部已捕获，不应传出，但 `return_exceptions=True` 多一层保险）；
   - `mgr._tools.sort(key=lambda t: t.full_name)`；
   - 返回 `mgr`。
4. `async def _connect_one(mgr, name, srv, version)`：
   ```python
   try:
       await asyncio.wait_for(_do_connect(mgr, name, srv, version), timeout=connect_timeout)
   except asyncio.TimeoutError:
       print(f"[mcp] warn: connect server {name} timeout after {connect_timeout}s", file=sys.stderr)
   except Exception as e:
       print(f"[mcp] warn: connect server {name} failed: {e}", file=sys.stderr)
   ```
5. `async def _do_connect(mgr, name, srv, version)`：
   - 按 `srv.type` 构造 transport 上下文（`stdio_client` / `streamablehttp_client`）；
   - 通过 `mgr._stack.enter_async_context(...)` 进入 transport 上下文，拿到 `(read, write)` 或 `(read, write, _metadata)`；
   - 再进入 `ClientSession(read, write, client_info=mtypes.Implementation(name="endless-code", version=version))` 上下文；
   - `await session.initialize()`；
   - `listed = await session.list_tools()`；
   - 对 `listed.tools` 调 `adapt_tool(name, t, session)`，收齐 list；
   - `async with mgr._lock:` 内 `mgr._sessions.append(_Session(name, session)); mgr._tools.extend(adapted)`。
6. `def Manager.tools(self) -> list[McpTool]`：返回 `list(self._tools)` 副本。
7. `async def Manager.close(self)`：
   ```python
   try:
       await asyncio.wait_for(self._stack.aclose(), timeout=close_timeout)
   except asyncio.TimeoutError:
       print(f"[mcp] warn: close timeout ({close_timeout}s), some sessions may leak", file=sys.stderr)
   ```
8. `src/endless_code/mcp/__init__.py` 追加 `from .manager import Manager, new_manager`、`from .tool import McpTool`。

**验证：** `pytest tests/test_mcp_manager.py`（`pytest-asyncio` `@pytest.mark.asyncio`）覆盖：
- 空 `cfg` → `Manager.tools()` 为空、`close()` 立即返回；
- 失败隔离：构造一个 stdio server 指向不存在的 command（`command="/no/such/bin"`）+ 一个用单测注入 stub 的成功"server"（通过 monkeypatch `_do_connect` 让某 name 走 stub 路径），断言 stub 工具被注册、失败 server 仅产生告警；
- 超时收尾：注入一个会卡住的连接 stub（`async def stub_connect(...): await asyncio.Event().wait()`），把 `connect_timeout` 临时改为 0.2，断言 `new_manager` 在 ~0.2s 内返回且 stderr 有 timeout 告警；
- close 兜底：注入一个 close 阻塞的 fake context manager（`__aexit__` 内 `await asyncio.Event().wait()`），把 `close_timeout` 改 0.2，断言 `close()` 在 0.2s 内返回；
- 并发安全：`pytest --asyncio-mode=auto` 默认就跑在单线程 event loop；额外检查 `_tools` 顺序由 `sort` 决定而非 task 完成顺序。

## T5: cli 接线

**文件：** `src/endless_code/cli.py`
**依赖：** T2、T3、T4
**步骤：**
1. import `asyncio`、`endless_code.mcp as mcp_client`。
2. 把现有 `main()` 拆为 `async def _amain() -> int` + `def main() -> None: raise SystemExit(asyncio.run(_amain()))`（若已是 async 结构则直接接线）。
3. 在 `registry = tool.default_registry()` 之后插入：
   ```python
   mcp_cfg = mcp_client.load_config(root)
   mgr = await mcp_client.new_manager(mcp_cfg, version=__version__)
   try:
       for t in mgr.tools():
           registry.register(t)
       # 既有：构造 PermissionEngine、EndlessCodeApp，await app.run_async()
       ...
   finally:
       await mgr.close()
   ```
4. `root` 复用 `os.getcwd()`；`version` 复用 `__version__`。

**验证：** `python -m endless_code` 无 MCP 配置时进 TUI、内置 6 工具可用；配一个 command 不存在的 stdio server 时进 TUI 不阻塞、stderr 显示连接失败告警。

## T6: 配置示例

**文件：** `docs/mcp/mcp-servers.example.yaml`
**依赖：** 无（可与 T2 并行）
**步骤：**
1. 内容（用 YAML 注释说明放置位置与覆盖语义）：
   ```yaml
   # 项目级放 <root>/.endless-code/mcp.yaml；用户级放 ~/.config/endless-code/mcp.yaml。
   # 同名 server 项目级完整覆盖用户级。
   # env / headers 的值支持 ${VAR} 从宿主环境变量展开；command/args 不展开。
   mcp_servers:
     github:
       type: stdio
       command: npx
       args: ["-y", "@modelcontextprotocol/server-github"]
       env:
         GITHUB_TOKEN: "${GITHUB_TOKEN}"
     local-sqlite:
       type: stdio
       command: python
       args: ["-m", "mcp_server_sqlite", "--db", "./data.db"]
     example-http:
       type: http
       url: "https://mcp.example.com/mcp"
       headers:
         Authorization: "Bearer ${EXAMPLE_TOKEN}"
   ```

**验证：** 在 `tests/test_mcp_config.py` 增加一个用例，读取此示例文件断言三个 server 都被解析成功（`monkeypatch.setenv("GITHUB_TOKEN", "x")` 等避免 undefined 噪音）。

## T7: tmux 端到端实跑（CLAUDE.md 开发原则）

**文件：** —
**依赖：** T1–T6
**步骤：**
1. 准备一个真实可用的 stdio MCP server。优先用 `npx -y @modelcontextprotocol/server-everything`（官方示例 server，自带 echo / add 等基础工具）；若无 npx，可临时用一个最小 Python server（`uv run mcp dev examples/...` 风格）。
2. 在项目根写一个临时 `.endless-code/mcp.yaml` 指向它：
   ```yaml
   mcp_servers:
     demo:
       type: stdio
       command: npx
       args: ["-y", "@modelcontextprotocol/server-everything"]
   ```
3. `tmux` 起 endless-code：
   - 启动日志（stderr）显示 server 连接成功 + 工具数；TUI 状态栏正常；
   - 让模型调用 `mcp__demo__echo` 一类工具：default 模式下弹人在回路 → 允许本次 → 工具结果回灌 → 模型续答；
   - 选"永久允许"后，本地权限规则被写入；重启 endless-code 后再调同工具不再弹窗（验证永久规则与 MCP 命名空间联动）；
   - 切到 bypassPermissions：调用不弹窗；但让模型跑 `rm -rf /` 仍被内置黑名单拦下（MCP 工具不绕过黑名单的内置作用域）；
   - Esc 取消弹窗：干净回到 idle，不退出程序；
   - 退出 endless-code（`/exit` 或 Ctrl+C）后 `ps -ef | grep server-everything` 确认子进程已终止；
4. 配置一个 command 不存在的 server + 一个能跑的 server：启动 stderr 有失败告警，能跑的 server 工具仍可用。

**验证：** 上述全部观察通过；删除临时 `.endless-code/mcp.yaml`，恢复项目根干净。

## T8: 全量编译测试与规范

**文件：** —
**依赖：** T1–T7
**步骤：**
1. `ruff format --check .`（应无 diff）；`ruff check .`（应无告警）。
2. （可选）`mypy src/endless_code/mcp`（启用 strict 子集亦可）。
3. `pytest`（含新增的 `tests/test_mcp_*.py`）。
4. `pytest --asyncio-mode=auto tests/test_mcp_manager.py tests/test_agent/` 之类——重点守护 Manager 并发连接、共享状态、close 兜底无悬挂 task / 死锁。
5. `git grep -E '(Bearer|sk-|ghp_|github_pat_)[A-Za-z0-9_-]{16,}'`（应无命中：凭据不落盘）。
6. `git check-ignore -q docs/mcp/mcp-servers.example.yaml` 不需要忽略（示例只含 `${VAR}`）。

**验证：** 全部通过。

## 执行顺序

```
T1(SDK 依赖) ─┬─→ T2(config) ─┐
              │                ├─→ T4(manager) ─→ T5(cli 接线) ─→ T7(tmux 实跑) ─→ T8(规范)
              └─→ T3(tool)   ─┘
                                 └─→ T6(配置示例)（可与 T2 并行）
```
依赖：T2,T3 ← T1；T4 ← {T2,T3}；T5 ← {T2,T3,T4}；T6 独立于 T3、T4（可在 T2 完成后做）；T7 ← T1–T5；T8 ← 全部。
~~~