# 会话内模型切换与输出样式 Tasks

> 依据：已批准的 `docs/model-style/spec.md` 与 `docs/model-style/plan.md`。

## 执行环境约定

- 本机 `python` 指向 WindowsApps 存根（退出码 9009），所有验证命令统一使用 `F:\anaconda3\python.exe`（Python 3.12.7，已 editable 安装本项目）。CI 中等价命令为 `python -m ...`。
- Shell 为 PowerShell，多命令用 `;` 分隔，不使用 `&&`。
- **基线（已实测）**：`ruff check src tests` 与 `ruff format --check src tests` 全绿；`pytest tests -q` = 278 passed, 1 skipped。
- **范围外的既有噪声（不得修改）**：仓库根 `test_modifications.py`（断言已不存在的 `_deferred_notice_shown`，1 个测试失败 + 14 处 lint 错误）、`CHANGES_SUMMARY.md` 等 3 个 markdown 内嵌代码块的格式差异。工程检查一律限定 `src` 与 `tests` 路径。

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/endless_code/prompt/style.py` | 输出样式预设注册表与查询函数（不依赖 `modules`） |
| 修改 | `src/endless_code/prompt/modules.py` | `build_system_prompt` 增 `style` 参数并插入样式模块 |
| 修改 | `src/endless_code/prompt/__init__.py` | 导出样式 API |
| 修改 | `src/endless_code/command/types.py` | `ModelOption`/`StyleOption`/`SwitchResult`；`SessionInfo` 增两字段 |
| 修改 | `src/endless_code/command/__init__.py` | 导出三个新类型 |
| 修改 | `src/endless_code/command/host.py` | `CommandHost` 增四个方法 |
| 修改 | `src/endless_code/command/builtin.py` | 注册 `/model`、`/style`；`/status` 增两行 |
| 修改 | `src/endless_code/agent/__init__.py` | `output_style` 构造参数 + `set_output_style`；`_run` 传入样式 |
| 修改 | `src/endless_code/session/writer.py` | `write_model_marker` / `write_style_marker` |
| 修改 | `src/endless_code/session/list.py` | `_read_summary` 取最近模型 |
| 修改 | `src/endless_code/tui/app.py` | 切换编排、忙碌保护、host 四方法、状态栏与 `/status` 数据 |
| 修改 | `tests/test_prompt.py` | 样式注入与默认零变化 |
| 修改 | `tests/test_command_builtin.py` | FakeHost 扩展 + `/model`、`/style`、`/status` 用例 |
| 修改 | `tests/test_agent.py` | 样式进入稳定提示、下一轮生效 |
| 修改 | `tests/test_session.py` | marker 写入格式、loader 忽略、列表最近模型 |
| 修改 | `tests/test_tui.py` | 切换保留对话、失败原子性、忙碌保护、样式与状态展示 |

---

## T1: 新建输出样式注册表

**文件：** `src/endless_code/prompt/style.py`（新建）
**依赖：** 无

**步骤：**
1. 文件首行 docstring：`"""输出样式预设：名称、展示名、说明与注入正文。"""`；仅 `from dataclasses import dataclass`，**禁止 import `prompt.modules`**（避免循环导入）。
2. 定义 `@dataclass(frozen=True) class OutputStyle`，字段顺序 `name: str`、`label: str`、`description: str`、`content: str`。
3. 定义常量：`DEFAULT_STYLE_NAME = "default"`、`STYLE_MODULE_NAME = "output_style"`、`STYLE_PRIORITY = 65`。
4. 定义 `STYLES: tuple[OutputStyle, ...]`，四项依次为：
   - `default` / `默认` / `当前默认风格，简洁准确，必要时使用 Markdown。` / `content=""`（空串）
   - `concise` / `简洁` / `只给结论与必要代码，不复述问题、不加背景。` / content 见下
   - `explanatory` / `讲解` / `同样完成工作，但附带原理、权衡与被否决的方案。` / content 见下
   - `learning` / `教学` / `教学取向，在关键决策点给方案与取舍并邀请你参与。` / content 见下
5. 三个非默认 content 均以同一句开头（测试依赖此标记）：
   `The following output requirements take precedence over the tone guidance above.`
   换行后接各自正文：
   - concise：`Be extremely brief. Give the conclusion and the minimal necessary code only. Do not restate the question, do not add background, and do not list alternatives unless the user asked for them.`
   - explanatory：`Keep completing the task fully, and additionally explain the reasoning: why this approach was chosen, which alternatives were rejected, and what trade-offs or risks remain. Add insight after meaningful steps rather than only at the end.`
   - learning：`Act as a collaborative teacher. At meaningful decision points present 2-3 options with trade-offs and invite the user to choose, explain unfamiliar concepts briefly, and let the user attempt small decisions before revealing the answer.`
6. 实现四个函数：
   - `all_styles() -> list[OutputStyle]`：`return list(STYLES)`
   - `find_style(name: str) -> OutputStyle | None`：`needle = (name or "").strip().lower()`，遍历 `STYLES` 比对 `style.name`，未命中返回 `None`
   - `style_content(name: str) -> str`：`find_style` 为 `None` 时返回 `""`，否则返回 `style.content`
   - `style_label(name: str) -> str`：`find_style` 为 `None` 时返回 `(name or "").strip() or DEFAULT_STYLE_NAME`，否则返回 `style.label`

**验证：** 运行
`F:\anaconda3\python.exe -c "from endless_code.prompt.style import all_styles, find_style, style_content, style_label; print([s.name for s in all_styles()]); print(find_style(' CONCISE ').name, style_content('nope') == '', style_label('nope'), style_label(''))"`
预期输出两行：`['default', 'concise', 'explanatory', 'learning']` 与 `concise True nope default`

---

## T2: 系统提示组装接入样式

**文件：** `src/endless_code/prompt/modules.py`
**依赖：** T1

**步骤：**
1. 顶部追加导入：`from endless_code.prompt.style import DEFAULT_STYLE_NAME, STYLE_MODULE_NAME, STYLE_PRIORITY, style_content`。
2. 把 `build_system_prompt` 签名改为
   `def build_system_prompt(instructions: str = "", memory: str = "", style: str = DEFAULT_STYLE_NAME) -> str:`，docstring 补一句「`style` 为未知或默认值时不注入样式模块」。
3. 函数体改为
   `return assemble_system(fixed_modules() + [Module(STYLE_MODULE_NAME, STYLE_PRIORITY, style_content(style))] + optional_modules(instructions, memory))`。
4. `fixed_modules()`、`optional_modules()`、`assemble_system()`、`Module` 一律不改（`assemble_system` 既有逻辑会跳过 content 为空的模块，default 因此不产生任何模块）。

**验证：** 运行
`F:\anaconda3\python.exe -c "from endless_code.prompt.modules import build_system_prompt as b; from endless_code.prompt import SYSTEM_PROMPT; d=b(); c=b('','','concise'); print(d==b('','','default')==b('','','nope')==SYSTEM_PROMPT, c.index('markdown only when it improves') < c.index('precedence over the tone guidance'))"`
预期输出：`True True`

---

## T3: 导出样式 API

**文件：** `src/endless_code/prompt/__init__.py`
**依赖：** T1

**步骤：**
1. 追加导入块：`from endless_code.prompt.style import (DEFAULT_STYLE_NAME, STYLE_MODULE_NAME, STYLE_PRIORITY, OutputStyle, all_styles, find_style, style_content, style_label)`。
2. `__all__` 中按现有字母序插入上述 8 个名称（`DEFAULT_STYLE_NAME`、`OutputStyle`、`STYLE_MODULE_NAME`、`STYLE_PRIORITY`、`all_styles`、`find_style`、`style_content`、`style_label`）。
3. 模块级 `SYSTEM_PROMPT = build_system_prompt()` 保持不变（默认样式，语义等价现状）。

**验证：** 运行
`F:\anaconda3\python.exe -c "from endless_code.prompt import all_styles, find_style, style_content, style_label, DEFAULT_STYLE_NAME, STYLE_PRIORITY, SYSTEM_PROMPT; print(len(all_styles()), DEFAULT_STYLE_NAME, STYLE_PRIORITY, 'precedence' not in SYSTEM_PROMPT)"`
预期输出：`4 default 65 True`

---

## T4: 提示层测试

**文件：** `tests/test_prompt.py`
**依赖：** T2、T3

**步骤：**
1. 导入区补 `from endless_code.prompt import DEFAULT_STYLE_NAME, SYSTEM_PROMPT, all_styles, find_style, style_content` 与 `from endless_code.prompt.modules import build_system_prompt`（若已存在则复用）。
2. 新增 `test_default_style_keeps_prompt_byte_identical`：断言 `build_system_prompt() == build_system_prompt("", "", DEFAULT_STYLE_NAME) == build_system_prompt("", "", "nope") == SYSTEM_PROMPT`。
3. 新增 `test_non_default_styles_inject_after_tone_module`：对 `concise`/`explanatory`/`learning` 三者循环，`prompt = build_system_prompt("", "", name)`，断言 `"precedence over the tone guidance" in prompt`，且 `prompt.index("markdown only when it improves") < prompt.index("precedence over the tone guidance")`。
4. 新增 `test_style_content_ordering_with_instructions_and_memory`：`prompt = build_system_prompt("project rule", "remember this", "concise")`，断言样式正文位置在 `"project rule"` 之前（priority 65 < 80）。
5. 新增 `test_find_style_is_case_insensitive_and_strips`：断言 `find_style(" Concise ").name == "concise"`、`find_style("nope") is None`、`style_content("nope") == ""`、`len(all_styles()) == 4`。

**验证：** 运行 `F:\anaconda3\python.exe -m pytest tests/test_prompt.py -q`，预期全部通过（含既有 5 个用例），无 failed。

---

## T5: 命令层数据类型

**文件：** `src/endless_code/command/types.py`、`src/endless_code/command/__init__.py`
**依赖：** 无

**步骤：**
1. `types.py` 中在 `ParsedCommand` 之后、`SessionInfo` 之前追加三个 frozen dataclass：
   - `ModelOption`：`index: int`、`name: str`、`model: str`、`current: bool`
   - `StyleOption`：`name: str`、`label: str`、`description: str`、`current: bool`
   - `SwitchResult`：`ok: bool`、`message: str`、`name: str = ""`、`model: str = ""`
2. `SessionInfo` 末尾追加两个带默认值字段：`output_style: str = "default"`、`context_window: int = 0`（放在 `message_count` 之后，保证既有关键字构造不受影响）。
3. `command/__init__.py`：在 `from endless_code.command.types import (...)` 中补 `ModelOption`、`StyleOption`、`SwitchResult`，并按字母序加入 `__all__`。

**验证：** 运行
`F:\anaconda3\python.exe -c "from endless_code.command import ModelOption, StyleOption, SwitchResult, SessionInfo; print(ModelOption(1,'a','m',True), SwitchResult(False,'x').model==''); s=SessionInfo(version='v',provider='p',model='m',mode='DEFAULT',tokens_in=0,tokens_out=0,session_id='s',message_count=0); print(s.output_style, s.context_window)"`
预期输出两行：`ModelOption(index=1, name='a', model='m', current=True) True` 与 `default 0`

---

## T6: 扩展 CommandHost 接口

**文件：** `src/endless_code/command/host.py`
**依赖：** T5

**步骤：**
1. 导入区补 `from endless_code.command.types import ModelOption, SessionInfo, StyleOption, SwitchResult`（替换现有仅导入 `SessionInfo` 的那行）。
2. 在「权限模式」分组之后、「状态与记忆」之前插入新分组：
   ```python
       # 模型与输出样式
       def get_model_options(self) -> list[ModelOption]: ...
       def switch_model(self, selector: str) -> SwitchResult: ...
       def get_style_options(self) -> list[StyleOption]: ...
       def set_output_style(self, name: str) -> SwitchResult: ...
   ```
3. 不改动任何既有方法签名。

**验证：** 运行
`F:\anaconda3\python.exe -c "from endless_code.command.host import CommandHost; names=['get_model_options','switch_model','get_style_options','set_output_style']; print(all(hasattr(CommandHost,n) for n in names))"`
预期输出：`True`

---

## T7: 注册 /model、/style 并扩展 /status

**文件：** `src/endless_code/command/builtin.py`
**依赖：** T5、T6

**步骤：**
1. 在 `register_builtin_commands` 内、`status_command` 之后新增 `model_command(host, args: str) -> None`：
   - `needle = args.strip()`；为空时取 `options = host.get_model_options()`：列表为空 → `host.show_notice("未配置可用模型。")`；否则每项拼一行 `f"{o.index}. {o.name} — {o.model}"`，`o.current` 为真时行尾追加 `（当前）`，首行加 `"可用模型："`，整体经 `host.show_notice` 输出。
   - `needle` 非空时 `result = host.switch_model(needle)`：`result.ok` → `host.show_notice(result.message)`，否则 → `host.show_error(result.message)`。
2. 新增 `style_command(host, args: str) -> None`：
   - 为空时取 `options = host.get_style_options()`，首行 `"输出样式："`，每项一行 `f"{o.name}（{o.label}）— {o.description}"`，`o.current` 为真时行尾追加 `（当前）`，经 `show_notice` 输出。
   - 非空时 `result = host.set_output_style(args.strip())`，成功 `show_notice(result.message)`，失败 `show_error(result.message)`。
3. `status_command` 的 `lines` 列表中，在 `f"权限模式：{info.mode}"` 之后插入 `f"输出样式：{info.output_style}"` 与 `f"上下文窗口：{info.context_window}"`（窗口原样输出整数，不做单位换算）。
4. `commands` 列表中，在 `/permissions` 之后、`/status` 之前追加两条 `CommandSpec`：
   - `/model`：description `"列出或切换本会话使用的模型"`，usage `"/model [编号|名称]"`，`kind=CommandKind.UI`，`handler=model_command`，`aliases=("/models",)`，`arg_hint="[编号|名称]"`
   - `/style`：description `"列出或切换输出样式"`，usage `"/style [样式名]"`，`kind=CommandKind.UI`，`handler=style_command`，`aliases=("/styles",)`，`arg_hint="[样式名]"`

**验证：** 运行
`F:\anaconda3\python.exe -c "from endless_code.command.registry import Registry; from endless_code.command.builtin import register_builtin_commands as r; reg=Registry(); r(reg); print([s.name for s in reg.suggest('/mod')], [s.name for s in reg.suggest('/sty')], len(reg.visible()))"`
预期输出：`['/model'] ['/style'] 17`

---

## T8: 命令层测试

**文件：** `tests/test_command_builtin.py`
**依赖：** T7

**步骤：**
1. 导入区补 `ModelOption`、`StyleOption`、`SwitchResult`（从 `endless_code.command.types`）。
2. `FakeHost.__init__` 末尾追加状态：
   `self.model_options = [ModelOption(1, "fake", "fake-model", True), ModelOption(2, "deepseek", "deepseek-chat", False)]`、`self.style_options = [StyleOption("default", "默认", "d", True), StyleOption("concise", "简洁", "c", False)]`、`self.switch_result = SwitchResult(True, "已切换到 deepseek（deepseek-chat）。", "deepseek", "deepseek-chat")`、`self.style_result = SwitchResult(True, "已切换到 简洁 样式，下一轮回答生效。", "concise")`、`self.switch_calls: list[str] = []`、`self.style_calls: list[str] = []`；并把 `self.session_info` 构造补上 `output_style="默认"`、`context_window=1000000`。
3. `FakeHost` 追加四个方法：`get_model_options` 返回 `self.model_options`；`switch_model(selector)` 记录到 `switch_calls` 并返回 `self.switch_result`；`get_style_options` 返回 `self.style_options`；`set_output_style(name)` 记录到 `style_calls` 并返回 `self.style_result`。
4. 新增用例：
   - `test_model_command_lists_options_and_marks_current`：`/model` → `host.notices[-1]` 含 `"1. fake — fake-model（当前）"` 与 `"2. deepseek — deepseek-chat"`，`switch_calls == []`
   - `test_model_command_switch_success_uses_notice`：`/model 2` → `switch_calls == ["2"]`、`notices[-1]` 含 `"已切换到 deepseek"`、`errors == []`
   - `test_model_command_switch_failure_uses_error`：把 `host.switch_result` 改为 `SwitchResult(False, "未找到模型：nope。输入 /model 查看可用列表。")`，执行 `/model nope` → `errors[-1]` 含 `"未找到模型"`、`notices == []`
   - `test_model_command_empty_list`：`host.model_options = []`，执行 `/model` → `notices[-1] == "未配置可用模型。"`
   - `test_style_command_lists_and_switches`：`/style` → `notices[-1]` 含 `"concise（简洁）"` 与 `"（当前）"`；`/style concise` → `style_calls == ["concise"]` 且 `notices[-1]` 含 `"下一轮回答生效"`
   - `test_style_command_unknown_reports_error`：`host.style_result = SwitchResult(False, "未知输出样式：nope。可选：default / concise / explanatory / learning")`，执行 `/style nope` → `errors[-1]` 含 `"可选：default"`
   - `test_status_command_shows_style_and_window`：`/status` → `notices[-1]` 同时含 `"输出样式：默认"` 与 `"上下文窗口：1000000"`
5. 若既有用例断言了 `/help` 命令条数或 `/status` 行数，按新增两条命令与两行输出同步更新断言。

**验证：** 运行 `F:\anaconda3\python.exe -m pytest tests/test_command_builtin.py -q`，预期全部通过，无 failed。

---

## T9: Agent 持有输出样式

**文件：** `src/endless_code/agent/__init__.py`
**依赖：** T3

**步骤：**
1. 从 `endless_code.prompt` 的导入行补 `DEFAULT_STYLE_NAME`（该行现为 `from endless_code.prompt import build_system_prompt, gather_environment, plan_reminder`）。
2. `Agent.__init__` 参数列表末尾（`checkpoint` 之后）追加 `output_style: str = DEFAULT_STYLE_NAME`，并在 `self._checkpoint = checkpoint` 之后加 `self._output_style = output_style`。
3. 新增方法（放在 `run_force_compact` 之前）：
   ```python
       def set_output_style(self, style: str) -> None:
           """设置输出样式；下一轮 run 组装稳定提示时生效。"""
           self._output_style = style
   ```
4. `_run` 中把 `stable_system = build_system_prompt(self._instruction_text, memory_text)` 改为 `stable_system = build_system_prompt(self._instruction_text, memory_text, self._output_style)`。
5. 其余循环、压缩、权限、审计逻辑一律不改。

**验证：** 运行
`F:\anaconda3\python.exe -c "import inspect; from endless_code.agent import Agent; print('output_style' in inspect.signature(Agent.__init__).parameters, hasattr(Agent,'set_output_style'))"`
预期输出：`True True`

---

## T10: Agent 层样式测试

**文件：** `tests/test_agent.py`
**依赖：** T9

**步骤：**
1. 复用文件中已有的 `RequestProvider`（新式单参 `stream(self, request)`，可读取 `request.system.stable`）；若其构造需要 scripts，按其现有签名传入 `[[StreamEvent(text="ok"), StreamEvent(done=True)], [StreamEvent(text="ok2"), StreamEvent(done=True)]]`。
2. 新增 `test_output_style_reaches_stable_system_prompt`：以 `output_style="concise"` 构造 Agent，跑一轮，断言 `"precedence over the tone guidance" in provider.requests[0].system.stable`。
3. 新增 `test_default_style_stable_prompt_unchanged`：以默认样式构造 Agent（不传 `output_style`），跑一轮，断言 `provider.requests[0].system.stable == build_system_prompt("", "")`（需从 `endless_code.prompt` 导入 `build_system_prompt`）。
4. 新增 `test_set_output_style_applies_next_round`：默认样式跑第一轮 → 断言 stable 不含 `"precedence over the tone guidance"`；调用 `agent.set_output_style("explanatory")` 后跑第二轮 → 断言 `provider.requests[1].system.stable` 含该标记且含 `"why this approach was chosen"`。
5. 每个用例沿用文件中既有的 `Conversation()` + `tmp_path` runtime 构造方式，保持与相邻测试一致。

**验证：** 运行 `F:\anaconda3\python.exe -m pytest tests/test_agent.py -q`，预期全部通过（含既有用例），无 failed。

---

## T11: 会话存档切换标记

**文件：** `src/endless_code/session/writer.py`
**依赖：** 无

**步骤：**
1. 在 `write_compact_marker` 之后新增两个方法，复用 `self._lock` 与 `self._write`：
   ```python
       def write_model_marker(self, previous: str, current: str) -> None:
           """记录会话内模型切换，供追溯与最近模型展示。"""
           with self._lock:
               self._model = current
               self._write(
                   {
                       "type": "model_switch",
                       "ts": int(time.time()),
                       "model": current,
                       "previous": previous,
                   }
               )

       def write_style_marker(self, style: str) -> None:
           """记录输出样式切换。"""
           with self._lock:
               self._write({"type": "style_switch", "ts": int(time.time()), "style": style})
   ```
2. `load_session` 不改动（未知 `type` 条目已被忽略）。

**验证：** 运行
`F:\anaconda3\python.exe -c "import json,tempfile,pathlib; from endless_code.session import Writer; from endless_code.llm import Message; d=tempfile.mkdtemp(); w=Writer(d,'m1'); w.append(Message(role='user',content='hi')); w.write_model_marker('m1','m2'); w.write_style_marker('concise'); w.close(); rows=[json.loads(l) for l in (pathlib.Path(d)/'conversation.jsonl').read_text(encoding='utf-8').splitlines()]; print([r.get('type') or r.get('role') for r in rows], rows[1]['model'], rows[1]['previous'], rows[2]['style'])"`
预期输出：`['user', 'model_switch', 'style_switch'] m2 m1 concise`

---

## T12: 会话列表展示最近模型

**文件：** `src/endless_code/session/list.py`
**依赖：** T11

**步骤：**
1. 重写 `_read_summary(path) -> tuple[str, str]` 的扫描逻辑：完整遍历全部行，不再在命中标题后提前 return。
   - `title = ""`、`model = ""`
   - 每行解析失败（`json.JSONDecodeError`）则 `continue`
   - `if isinstance(entry.get("model"), str) and entry["model"]: model = entry["model"]`（**每次都覆盖**，故最终为最后一次出现的模型）
   - `if not title and entry.get("role") == "user" and isinstance(entry.get("content"), str): title = _truncate(entry["content"].strip().replace("\n", " ")) or "（空消息）"`
   - `OSError` 时返回 `("（无法读取）", model)`
2. 循环结束后返回 `(title or "（无标题会话）", model)`；调用处 `model or "未知模型"` 的回落保持不变。
3. `_truncate`、`list_sessions`、`SessionInfo`（session 包的）不改。

**验证：** 运行
`F:\anaconda3\python.exe -c "import tempfile,pathlib; from endless_code.session import Writer, list_sessions; from endless_code.session.list import _read_summary; from endless_code.llm import Message; root=pathlib.Path(tempfile.mkdtemp())/'sessions'/'20260909-120000-abcd'; w=Writer(str(root),'m1'); w.append(Message(role='user',content='第一条标题')); w.append(Message(role='assistant',content='r')); w.write_model_marker('m1','m2'); w.close(); print(_read_summary(root/'conversation.jsonl')); print([ (i.title, i.model) for i in list_sessions(str(root.parent))])"`
预期输出：`('第一条标题', 'm2')` 与 `[('第一条标题', 'm2')]`

---

## T13: 会话层测试

**文件：** `tests/test_session.py`
**依赖：** T11、T12

**步骤：**
1. 新增 `test_writer_records_model_and_style_markers(tmp_path)`：建 `Writer(str(tmp_path / "s1"), "m1")`，append 一条 user 消息后调用两个 marker 方法并 `close()`；逐行 `json.loads` 读回，断言三条记录的类型/role 依次为 `user`、`model_switch`、`style_switch`，且第二条含 `model == "m2"`、`previous == "m1"`、整数 `ts`，第三条含 `style == "concise"`。
2. 新增 `test_loader_ignores_switch_markers(tmp_path)`：写入 user → `write_model_marker` → assistant → `write_style_marker`，`close()` 后 `load_session`，断言 `[m.role for m in loaded.messages] == ["user", "assistant"]` 且内容不变。
3. 新增 `test_list_sessions_reports_latest_model(tmp_path)`：按 T12 验证脚本的方式建一个会话目录（首条 model 为 `m1`，随后 marker 切到 `m2`），断言 `list_sessions(...)` 唯一项的 `model == "m2"`、`title` 为首条 user 文本截断结果。
4. 既有用例（`test_writer_persists_messages_and_compaction_for_loader`、`test_loader_skips_bad_rows_and_lone_tool_call`、`test_list_and_cleanup_only_use_new_session_ids`）不改。

**验证：** 运行 `F:\anaconda3\python.exe -m pytest tests/test_session.py -q`，预期全部通过，无 failed。

---

## T14: TUI 切换编排与 host 实现

**文件：** `src/endless_code/tui/app.py`
**依赖：** T3、T5、T9、T11

**步骤：**
1. 导入区：把 `from endless_code.command import (...)` 补上 `ModelOption`、`StyleOption`、`SwitchResult`；把 `from endless_code.prompt import EXECUTE_DIRECTIVE` 改为 `from endless_code.prompt import (DEFAULT_STYLE_NAME, EXECUTE_DIRECTIVE, all_styles, find_style, style_label)`。
2. `__init__` 中 `self._suggest_specs: list[CommandSpec] = []` 之后追加 `self._output_style: str = DEFAULT_STYLE_NAME`。
3. `_activate_provider` 中构造 `Agent(...)` 的关键字参数末尾补 `output_style=self._output_style`（其余不动，启动路径语义保持不变）。
4. 在 `_activate_provider` 之后新增三个方法：
   - `def _resolve_model_selector(self, selector: str) -> ProviderConfig | None`：`needle = selector.strip()`；空 → `None`；`needle.isdigit()` → `index = int(needle) - 1`，越界返回 `None`，否则返回 `self._providers[index]`；否则对每个 cfg 做 `needle.lower() in (cfg.name.lower(), cfg.model.lower())` 精确匹配，命中即返回，遍历完返回 `None`。
   - `def _switch_provider(self, cfg: ProviderConfig) -> SwitchResult`：严格按 plan「模型切换调用链」步骤 3–7 实现——
     1. `if self._provider is not None and cfg.name == self._provider.name and cfg.model == self._provider.model:` 返回 `SwitchResult(True, f"当前已在使用 {cfg.name}（{cfg.model}）。", cfg.name, cfg.model)`
     2. `try: secret = cfg.resolve_api_key()` / `except Exception as exc:` 返回 `SwitchResult(False, self._safe(f"模型 {cfg.name} 的 API key 不可用：{type(exc).__name__}: {exc}"))`
     3. `try: provider = new_provider(cfg)` / `except Exception as exc:` 返回 `SwitchResult(False, self._safe(f"切换失败：{type(exc).__name__}: {exc}"))`
     4. 提交（此段不再抛异常，若抛则由外层 `try` 兜底并返回失败，且已提交字段无需回滚——因此提交段必须放在两个构造 try 之后）：记录 `old_model = self._provider.model if self._provider else ""`；`self._provider = provider`；`if secret: self._secrets.add(secret)`；`self._runtime.context_window = effective_context_window(cfg)`；`self._runtime.usage_anchor = 0`；`self._runtime.anchor_msg_len = 0`；`if self._writer is not None: self._writer.write_model_marker(old_model, provider.model)`；`if self._memory_manager is not None: self._memory_manager.set_provider(provider, provider.model)`；重建 `self._agent = Agent(provider, self._tool_registry, self._version, self._engine, self._runtime, memory_manager=self._memory_manager, instruction_text=self._instruction_text, audit_writer=self._audit_writer, checkpoint=self._checkpoint, output_style=self._output_style)`；`self._update_status()`
     5. 返回 `SwitchResult(True, f"已切换到 {cfg.name}（{provider.model}）。", cfg.name, provider.model)`
     6. 方法内**不得**关闭或重建 `Writer`/`AuditWriter`/`CheckpointManager`/`Conversation`/`runtime.session`（N2）。
   - `def _command_set_style(self, name: str) -> SwitchResult`：`style = find_style(name)`；`None` → `SwitchResult(False, f"未知输出样式：{name}。可选：" + " / ".join(s.name for s in all_styles()))`；`style.name == self._output_style` → `SwitchResult(True, f"当前已是 {style.label} 样式。", style.name)`；否则提交：`self._output_style = style.name`、`if self._agent is not None: self._agent.set_output_style(style.name)`、`if self._writer is not None: self._writer.write_style_marker(style.name)`、`self._update_status()`，返回 `SwitchResult(True, f"已切换到 {style.label} 样式，下一轮回答生效。", style.name)`。
5. 在 CommandHost 实现区（`set_mode` 附近）新增四个 host 方法，每个方法首行做忙碌保护：
   ```python
       def get_model_options(self) -> list[ModelOption]:
           current = self._provider.name if self._provider else ""
           return [
               ModelOption(i, cfg.name, cfg.model, cfg.name == current)
               for i, cfg in enumerate(self._providers, 1)
           ]

       def switch_model(self, selector: str) -> SwitchResult:
           if self._state is not SessionState.IDLE:
               return SwitchResult(False, "当前任务尚未结束，暂不能切换模型。")
           cfg = self._resolve_model_selector(selector)
           if cfg is None:
               return SwitchResult(
                   False, f"未找到模型：{selector}。输入 /model 查看可用列表。"
               )
           return self._switch_provider(cfg)

       def get_style_options(self) -> list[StyleOption]:
           return [
               StyleOption(s.name, s.label, s.description, s.name == self._output_style)
               for s in all_styles()
           ]

       def set_output_style(self, name: str) -> SwitchResult:
           if self._state is not SessionState.IDLE:
               return SwitchResult(False, "当前任务尚未结束，暂不能切换输出样式。")
           return self._command_set_style(name)
   ```
6. `_update_status`：在 `mode_label` 之后计算 `style = style_label(self._output_style)` 与 `window = self._runtime.context_window if self._runtime else 0`，`sub_title` 改为
   `f"{mode_label} | {provider_name} | {model} | {style} | {window} ctx | ↑{self._usage_in} ↓{self._usage_out} tok | /help 查看命令"`（新增字段插在 model 与 token 之间，保持 `split("|")[0]` 仍是模式，兼容既有测试）。
7. `get_session_info`：返回的 `SessionInfo(...)` 补 `output_style=style_label(self._output_style)`、`context_window=self._runtime.context_window if self._runtime else 0`。

**验证：** 运行
`F:\anaconda3\python.exe -m compileall -q src; F:\anaconda3\python.exe -c "from endless_code.tui.app import EndlessCodeApp as A; print(all(hasattr(A,n) for n in ['get_model_options','switch_model','get_style_options','set_output_style','_switch_provider','_resolve_model_selector','_command_set_style']))"`
预期输出：`True`（compileall 无输出即成功）

---

## T15: TUI 集成测试

**文件：** `tests/test_tui.py`
**依赖：** T14

**步骤：**
1. 复用既有 `_config()`、`_engine()`、`_wait_for_state()`、`_chat_text()` 辅助函数与 `FakeProvider`/`BlockingProvider`；新增辅助
   `_second_config() -> ProviderConfig`：`ProviderConfig(name="deepseek", protocol="deepseek", api_key=TEST_SECRET, model="deepseek-chat", context_window=64_000)`，以及 `_broken_config()`：`api_key="$ENDLESS_CODE_TEST_MISSING_KEY_SWITCH"`（测试前 `os.environ.pop` 该变量）。
2. 新增 `test_model_switch_keeps_conversation_session_and_writer`：providers 传 `[_config(), _second_config()]`，`patch("endless_code.tui.app.new_provider", side_effect=[FakeProvider([[StreamEvent(text="first"), StreamEvent(done=True)]]), FakeProvider([])])`；两个 provider 时启动进 SELECTING，先调用 `app._handle_select_input("1")` 激活第一个 provider 并进入 IDLE（消耗 side_effect 第一项）；跑一轮对话产生历史后记录 `session_id = app._runtime.session.session_id`、`writer_path = app._writer.path`、`count = app._conv.length()`；执行 `app._handle_idle_input("/model 2")`；断言 `app._provider.model == "deepseek-chat"`、`app._conv.length() == count`、`app._runtime.session.session_id == session_id`、`app._writer.path == writer_path`、`app._runtime.context_window == 64_000`、`app._runtime.usage_anchor == 0`、`"deepseek-chat" in app.sub_title`、`app.mode` 未变。
3. 新增 `test_model_switch_failure_keeps_previous_provider`：providers 为 `[_config(), _broken_config()]`，进入 IDLE 后 `_handle_idle_input("/model 2")`，断言聊天区含 `"API key 不可用"`、`app._provider.model == "fake-model"`、`app._conv.length()` 不变、`app.state is SessionState.IDLE`。
4. 新增 `test_model_switch_rejected_while_busy`：用 `BlockingProvider` 起一轮（`await provider.started.wait()`），执行 `app._handle_idle_input("/model 1")`，断言聊天区含 `"尚未结束"`；`app.action_cancel_turn()` 后 `_wait_for_state(IDLE)`，断言 `app._provider is provider`（未被替换）。
5. 新增 `test_model_selector_matches_name_and_rejects_unknown`：IDLE 下 `_handle_idle_input("/model deepseek")` 断言切换成功；再 `_handle_idle_input("/model nope")` 断言聊天区含 `"未找到模型"` 且 provider 未变。
6. 新增 `test_style_switch_updates_agent_and_writes_marker`：IDLE 下 `_handle_idle_input("/style concise")`，断言 `app._output_style == "concise"`、`app._agent._output_style == "concise"`、`"简洁" in app.sub_title`、聊天区含 `"下一轮回答生效"`，并读 `app._writer.path` 断言存在一行 `json.loads(...)["type"] == "style_switch"` 且 `style == "concise"`。
7. 新增 `test_style_switch_unknown_name_keeps_current`：`_handle_idle_input("/style nope")` → 聊天区含 `"未知输出样式"` 与 `"explanatory"`，`app._output_style == "default"`。
8. 新增 `test_style_switch_rejected_while_busy`：`BlockingProvider` 起一轮后执行 `/style concise`，断言聊天区含 `"尚未结束"` 且 `app._output_style == "default"`。
9. 新增 `test_status_command_shows_style_and_window`：`_handle_idle_input("/status")`，断言聊天区同时含 `"输出样式：默认"` 与 `"上下文窗口：1000000"`。
10. 新增 `test_model_switch_marker_feeds_resume_list`：切换到 `_second_config()` 后调用 `app._command_resume()` 前先用 `list_sessions` 断言该会话目录的 `model == "deepseek-chat"`（覆盖 F7 端到端链路：writer marker → list 最近模型）。

**验证：** 运行 `F:\anaconda3\python.exe -m pytest tests/test_tui.py -q`，预期全部通过（含既有用例，尤其 `test_shift_tab_cycles_modes` 对 `sub_title` 的断言），无 failed。

---

## T16: 全量工程检查

**文件：** 无（仅执行）
**依赖：** T1–T15

**步骤：**
1. `F:\anaconda3\python.exe -m ruff format src tests`（若上一步开发中有格式漂移则自动修正），随后运行 `F:\anaconda3\python.exe -m ruff format --check src tests`。
2. 运行 `F:\anaconda3\python.exe -m ruff check src tests`；若报错则修复后重跑，直至通过。
3. 运行 `F:\anaconda3\python.exe -m compileall -q src examples`。
4. 运行 `F:\anaconda3\python.exe -m pytest tests -q`。
5. 不修改、不删除仓库根的 `test_modifications.py`、`closure_demo.py`、`CHANGES_SUMMARY.md`（范围外既有噪声）。

**验证：**
- `ruff format --check src tests` 预期 `N files already formatted`，退出码 0
- `ruff check src tests` 预期 `All checks passed!`，退出码 0
- `compileall` 预期无输出，退出码 0
- `pytest tests -q` 预期 `passed` 数量 ≥ 278 + 本项目新增用例数，`0 failed`（基线 1 skipped 保持）

---

## 执行顺序

```text
T1 -> T2 -> T3 -> T4                     （提示层：样式预设与注入）
T5 -> T6 -> T7 -> T8                     （命令层：类型、host、命令与测试）
T3 -> T9 -> T10                          （Agent 层：样式状态）
T11 -> T12 -> T13                        （会话层：标记与最近模型）
T3,T5,T9,T11 -> T14 -> T15               （TUI 编排与集成测试）
T4,T8,T10,T13,T15 -> T16                 （全量工程检查）
```

并行可行分组：`{T1–T4}`、`{T5–T8}`、`{T11–T13}` 三组互不依赖，可同时推进；`T9` 依赖 `T3`；`T14` 依赖四组产物，必须最后实现。
