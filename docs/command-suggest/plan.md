# 命令实时建议面板 Plan

> 依据：已批准的 `docs/command-suggest/spec.md`（F1–F4、N1–N3、AC1–AC5）

## 架构概览

复用 `resume-list` 的「隐藏 OptionList 面板」既有模式，在输入框上方增加 `#suggest-list`：

```text
输入变化 (Input.Changed)
   │
on_input_changed ── IDLE 且 "/前缀" 无参数？── 否 → _hide_suggestions()
   │ 是
_update_suggestions(value) → Registry.suggest(前缀) → 渲染候选行（命令 别名 — 描述）

键盘：
↑/↓ → App._on_key 拦截 → 移动 OptionList 高亮
Esc → App._on_key 拦截 → _hide_suggestions()（输入保留）
Enter → on_input_submitted 顶部拦截 → _accept_suggestion() → try_dispatch(spec.name) 直接执行
Tab → action_complete_command（保持现状）
```

## 核心数据结构与接口

### registry.py（新增方法）

```python
def suggest(self, prefix: str) -> list[CommandSpec]:
    """前缀（大小写不敏感）匹配名称或别名的可见命令，按名称排序去重。
    返回 CommandSpec 列表（非字符串，便于渲染描述）。"""
```

### app.py（新增/修改成员）

```python
self._suggest_specs: list[CommandSpec]        # 当前候选，空列表 = 面板关闭

def _suggest_list(self) -> OptionList          # query_one("#suggest-list")
def _update_suggestions(self, value: str) -> None
    # 非 IDLE / 非 "/" 开头 / 含参数 / 无候选 → _hide_suggestions()
    # 否则填充 self._suggest_specs 并渲染 Option，
    # 行文本：f"{spec.name}  {' '.join(spec.aliases)}  — {spec.description}"
    #（无别名时省略别名段），highlighted = 0，移除 hidden
def _hide_suggestions(self) -> None            # 加回 hidden、清空候选
def _accept_suggestion(self) -> None
    # 高亮无效直接 _hide_suggestions()
    # 否则：记录 spec → _hide_suggestions() → self._input.value = "" →
    #        self._dispatcher.try_dispatch(spec.name)
```

OptionList 操作：`action_cursor_up/down`、`highlighted` 属性，与 resume-list 用法一致。

## 模块设计

### registry.py（修改）

- **职责：** 新增前缀建议查询。
- **接口：** `suggest(prefix)`；复用 `_specs` 与 `hidden` 过滤，语义与 `completions` 对齐但返回 spec 对象。
- **依赖：** 无新增。

### tui/app.py（修改）

- **职责：** 面板渲染与键盘路由。
- **改动点：**
  1. `compose`：`streaming-box` 与 `input-area` 之间插入 `OptionList(id="suggest-list", classes="hidden")`。
  2. CSS：`#suggest-list { height: auto; max-height: 12; margin: 0 1; }` 与 `#suggest-list.hidden { display: none; }`（N3 最多约 8 行 + 边框）。
  3. `__init__` 增加 `self._suggest_specs: list[CommandSpec] = []`（`CommandSpec` 从 `endless_code.command` 导入）。
  4. `on_input_changed`：保留 RESUMING 分支；否则调用 `self._update_suggestions(event.value)`。
  5. `_on_key`：在 RESUMING、APPROVING 块之后追加建议面板块（仅 `IDLE` 且 `_suggest_specs` 非空）：`up/down/k/j` 移动高亮并 `event.stop()`；`escape` 隐藏面板并 `event.stop()`。**Enter 不在此处理**。
  6. `on_input_submitted` 顶部（清空输入逻辑之前）插入：若 `_suggest_specs` 非空且状态为 `IDLE` → `self._accept_suggestion()` 后 `return`。此路径保证 Enter 优先于普通提交（技术决策见下表）。
  7. `_start_turn` 开头调用 `self._hide_suggestions()`，防止流式期间残留面板。

## 模块交互

### 输入 "/me" 的完整数据流

```text
按键 / m e → Input.Changed → on_input_changed → _update_suggestions("/me")
  → registry.suggest("/me") → [/mem, /memory]（别名命中去重）
  → 面板显示两行，高亮第 0 行
按 ↓ → _on_key → _suggest_list.action_cursor_down()
按 Enter → Input.Submitted → on_input_submitted → _accept_suggestion()
  → 隐藏面板 → 清空输入 → try_dispatch("/mem") → host.show_notice(记忆索引)
```

## 文件组织

```text
修改：
├── src/endless_code/command/registry.py   # 新增 suggest()
├── src/endless_code/tui/app.py            # 面板、CSS、键盘路由
├── tests/test_command_registry.py         # suggest 单测
└── tests/test_tui.py                      # 面板集成测试
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| Enter 拦截位置 | `on_input_submitted` 顶部而非 `_on_key` | Input 对 Enter 的处理路径中 `Submitted` 事件必然到达 `on_input_submitted`，在此拦截是确定性的；`_on_key` 与 widget 键处理的先后顺序存在框架细节风险 |
| 建议查询返回类型 | `list[CommandSpec]` | 渲染需要描述与别名，避免二次 lookup |
| 面板组件 | OptionList + hidden class | 与 `resume-list` 模式一致，滚动、高亮、样式全部复用 |
| 执行方式 | `try_dispatch(spec.name)` | 复用异常兜底与统一入口；命令名合法，必命中 |
| 状态保护 | 仅 `IDLE` 显示；`_start_turn` 强制隐藏 | 避免与 STREAMING/RESUMING/APPROVING 键盘路由冲突（N2） |
