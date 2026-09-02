# 命令实时建议面板 Checklist

> 依据：已批准的 `spec.md`（AC1–AC5）。环境：PowerShell；解释器 `F:\anaconda3\python.exe`。

## 功能与集成

- [x] 输入 `/me` 实时弹出含 `/memory`（含别名与描述）的建议面板；输入普通文本面板消失（AC1；验证：`pytest tests/test_tui.py -q`，建议面板用例通过）
- [x] ↑↓ 循环移动高亮，Enter 执行高亮命令：无 AI 请求、聊天区出现命令输出、输入清空、面板关闭（AC2；验证：同上）
- [x] Esc 关闭面板且输入保留，重新输入面板复现并实时过滤（AC3；验证：同上）
- [x] Tab 补全行为与现状一致；RESUMING 搜索与审批键盘行为不变（AC4；验证：`pytest tests/test_tui.py -q`，存量 18 用例全部通过）
- [x] 别名前缀命中规范命令、隐藏命令不出现（AC5；验证：`pytest tests/test_command_registry.py -q`，suggest 用例通过）

## 工程检查

- [x] 代码格式合规（验证：`ruff format --check .` 无差异）
- [x] lint 通过（验证：`ruff check .` 无告警）
- [x] 编译通过（验证：`compileall -q src examples` 无输出）
- [x] 全部测试通过（验证：`pytest -q` 无失败）

## 端到端

- [ ] 真实应用旅程：启动应用 → 输入 `/mo` 看到按前缀过滤的建议面板 → ↑↓ 移动高亮 → Enter 执行选中命令 → Esc 退出面板继续输入普通消息正常对话（验证：`F:\anaconda3\python.exe -m endless_code` 手工执行，行为与描述一致）
