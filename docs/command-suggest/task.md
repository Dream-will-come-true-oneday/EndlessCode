# 命令实时建议面板 Tasks

> 依据：已批准的 `plan.md`。接口签名以 plan.md「核心数据结构与接口」为准。
> 环境：Windows PowerShell（用 `;` 分隔）；解释器 `F:\anaconda3\python.exe`。

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `src/endless_code/command/registry.py` | 新增 `suggest()` |
| 修改 | `src/endless_code/tui/app.py` | 建议面板、CSS、键盘路由 |
| 修改 | `tests/test_command_registry.py` | suggest 单测 |
| 修改 | `tests/test_tui.py` | 面板集成测试 |

## T1: Registry.suggest

**文件：** `src/endless_code/command/registry.py`
**依赖：** 无

**步骤：**
1. 在 `completions` 之后新增 `suggest(prefix: str) -> list[CommandSpec]`：
   `needle = prefix.strip().lower()`；收集 `_specs` 中 `not hidden` 且 `key.startswith(needle)` 的 spec，按 `spec.name` 去重排序返回。

**验证：** 运行 `F:\anaconda3\python.exe -m pytest tests/test_command_registry.py -q`，预期全部通过（新用例在 T3 补）。

## T2: 建议面板 UI

**文件：** `src/endless_code/tui/app.py`
**依赖：** T1

**步骤：**
1. CSS 追加：`#suggest-list { height: auto; max-height: 12; margin: 0 1; }` 与 `#suggest-list.hidden { display: none; }`。
2. `compose`：在 `streaming-box` 与 `input-area` 之间插入 `OptionList(id="suggest-list", classes="hidden")`；新增 `_suggest_list` 属性。
3. 导入 `CommandSpec`（自 `endless_code.command`）；`__init__` 增加 `self._suggest_specs: list[CommandSpec] = []`。
4. 按 plan.md 实现 `_update_suggestions` / `_hide_suggestions` / `_accept_suggestion` 三个方法。
5. `on_input_changed`：RESUMING 分支保留，末尾追加 `self._update_suggestions(event.value)`。
6. `on_input_submitted` 顶部（`text = event.value.strip()` 之前）插入 Enter 拦截：`_suggest_specs` 非空且 `IDLE` → `self._accept_suggestion()` + `return`。
7. `_on_key`：RESUMING、APPROVING 块之后追加建议块（`IDLE` 且 `_suggest_specs` 非空）：`up/k`、`down/j` 调用 `_suggest_list.action_cursor_up()/action_cursor_down()` 并 `event.stop()`；`escape` 调 `_hide_suggestions()` 并 `event.stop()`。
8. `_start_turn` 开头调用 `self._hide_suggestions()`。

**验证：** 运行 `F:\anaconda3\python.exe -m pytest tests/test_tui.py -q`，预期现有 18 个用例全部通过（零回归）。

## T3: 单元测试

**文件：** `tests/test_command_registry.py`
**依赖：** T1

**步骤：**
1. 新增用例：`suggest` 按名称与别名前缀命中并去重（`/per` → `/permissions` 一个 spec）；大小写不敏感；隐藏命令排除；无匹配返回空列表；按名称排序。

**验证：** 运行 `F:\anaconda3\python.exe -m pytest tests/test_command_registry.py -q`，预期全部通过。

## T4: 集成测试与全量检查

**文件：** `tests/test_tui.py`
**依赖：** T2

**步骤：**
1. 新增用例（沿用现有 FakeProvider 与 `_wait_for_state` 模式）：
   - 输入 `/me`（`pilot.press("/", "m", "e")`）后 `#suggest-list` 移除 hidden 且候选含 `/memory`；继续输入普通字母面板隐藏；
   - 输入 `/st` 后按 Enter：命令被执行（聊天区含状态文本）、无 AI 请求、输入框被清空、面板隐藏；
   - 按 Esc：面板隐藏、`inp.value` 保留；重新输入后面板复现；
   - 按 ↓ 后 Enter：执行的是第二个候选（`/me` → `/mem` 与 `/memory`，验证排序与移动）。
2. 全量工程检查（与 CI 一致）：`ruff format --check .`、`ruff check .`、`compileall -q src examples`、`pytest -q`。

**验证：** 四条命令全部零失败；全部测试通过后提交代码。

## 执行顺序

```text
T1 -> T2 -> T3 -> T4
```
