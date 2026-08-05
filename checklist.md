# 权限系统 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为；括号内为验证方式与对应需求。函数/类型名仅作定位提示，核验断言本身不依赖其命名（重命名实现而行为不变时本清单仍适用）。

## 实现完整性

- [ ] 黑名单硬拦截：对 `rm -rf /`、`rm -fr ~`、fork bomb、写块设备等命令做权限判定，结果为 Deny 且不执行（验证：单测对这些命令调用判 Deny；端到端观察被拒回灌）。（AC1/F1）
- [ ] 黑名单不可绕过：在 bypassPermissions 模式下，同样的黑名单命令仍判 Deny（验证：单测在 bypass 模式下对 `rm -rf /` 仍得 Deny）。（AC1/N1）
- [ ] 沙箱围栏：对项目根之外的路径（如 `/etc/passwd`、`../outside`）做文件操作判 Deny；项目内路径放行（验证：单测用 `tmp_path` 造内外路径断言裁决）。（AC2/F2）
- [ ] 沙箱防逃逸顺序：构造一个位于项目内、但指向项目外目录的软链接，对其做文件操作判 Deny（验证：单测用 `Path.symlink_to` 建软链接断言「先解析再比对」生效）。（AC2/N2）
- [ ] 沙箱新建文件祖先回退：对项目内、但中间多级目录尚未创建的新建文件路径，判 Allow（验证：单测专测目标不存在时回退到最近已存在祖先的分支）。（AC2/N2）
- [ ] 规则精确与 glob 匹配：`Bash(git status)` 放行 `git status` 而不放行 `git push`；`Bash(git *)` 放行所有 git；`Write(src/**)` 放行 `src/a/b.py` 而不放行 `docs/x`（验证：规则单测断言匹配结果）。（AC3/F3）
- [ ] deny 规则正向拦截：单独一条 deny 规则（如 `Bash(git push)` deny）命中时判定为 Deny（验证：引擎单测构造该 deny 规则对 `git push` 断言 Deny）。（AC3/F3）
- [ ] 同层 deny 优先：同一层 allow 与 deny 都命中时判 Deny（验证：规则/引擎单测）。（AC5/F4）
- [ ] 友好名路由：规则里的 Bash/Read/Write/Edit/Glob/Grep 正确作用到对应的 6 个内置工具（验证：单测用友好名规则对相应工具调用断言命中）。（AC4/F3）
- [ ] 模式矩阵：default(只读放行/写·命令执行需确认)、acceptEdits(写放行/命令执行需确认)、bypass(全放行)、plan(仅只读可见且其写/命令执行兜底仍为需确认)，逐档逐类裁决正确（验证：引擎单测对每档每类断言最终裁决值，含 plan 行 Write/Exec=Ask）。（AC7/F5）
- [ ] 流水线短路与跳层：黑名单命中不再走沙箱/规则；deny 规则命中不再走模式；allow 规则命中直接放行；非命令执行工具不被黑名单误拦、命令执行工具不被沙箱误拦而是继续后续层（验证：引擎单测按层构造样例断言短路与跳层放行）。（AC8/F6）
- [ ] 安全默认（分三路）：(a) 未注册工具名 → 归命令执行类、判需确认/拒绝而非放行；(b) 参数 JSON 无法解析的文件类调用 → 判拒绝（不静默放行）；(c) 只读标志缺失/类别不明 → 按有副作用处理（验证：引擎单测对三类畸形调用分别断言不被直接放行）。（AC15/N7）

## 集成

- [ ] 拒绝回灌不中断：被拒工具调用回灌错误结果，Agent Loop 继续下一轮（验证：脚本化 fake 首轮请求被拒工具，断言结果为错误且进入次轮）。（AC11/F9）
- [ ] 保序配对回灌：单批同时含「被拒调用 + 放行调用」时，两者结果按原调用下标顺序、各自调用 ID 正确配对回灌（被拒为错误、放行为正常），互不串位（验证：agent 单测构造混合批断言结果数组顺序与 ID 配对）。（AC11/F9/N3）
- [ ] 人在回路三选一：default 下请求写文件触发多行待批准块；选「允许本次」→执行；「拒绝本次」→回灌错误；「永久」→执行且写本地配置（验证：agent 单测对 `respond` Future 调 `set_result` 各选项断言行为）。（AC10/F8）
- [ ] 永久放行持久化：选「永久」后，本地层配置文件新增对应精确 allow 条目；以新引擎重新加载配置后，同一调用判放行（验证：单测断言文件内容 + 重载后裁决）。（AC10/F8）
- [ ] 层级就近优先：本地层 deny 盖过项目层 allow、本地盖项目、项目盖用户（验证：引擎单测构造三层冲突规则断言裁决顺序）。（AC5/F4）
- [ ] 只读并发不退化：一批连续只读调用不产生任何待批准请求、仍并发执行（`asyncio.gather`）；其中被沙箱拦的只读得错误结果而其余照常并发完成（验证：agent 单测断言无 ApprovalRequest 事件、并发批结果齐备且含被拒项）。（AC13/N3）
- [ ] 取消安全：人在回路等待中取消本轮 → Loop 干净收尾、对话历史角色合法、不退出程序、无挂起 asyncio task（验证：agent 单测在待批准等待中 `task.cancel()`，超时保护 + `asyncio.all_tasks()` 断言通过）。（AC12/N4）
- [ ] 运行时切换模式（Shift+Tab）：连续按 Shift+Tab 循环 default→acceptEdits→plan→bypassPermissions→default，当前模式依次正确改变、状态栏左侧常驻显示当前模式（不显示 provider 名）、切换不改已加载规则（验证：tui 单测模拟 shift+tab 断言模式序列与状态栏文本）。（AC9/F7）
- [ ] 模式跨轮保持：切换模式后发起新一轮对话，模式维持上次切换值、不被本轮启动重置（验证：tui 单测切到 acceptEdits 后 begin_turn 断言模式不变）。（AC9/F7）
- [ ] 启动默认模式：本地/项目/用户三层配置的默认模式按 本地>项目>用户 生效、皆无则 default（验证：单测三层各设不同默认模式断言生效层；含 default_mode=plan 启动即应用只读工具集+计划提醒）。（AC18/F4）
- [ ] 配置降级：三层文件缺失时引擎按空规则运行；某文件格式非法时跳过该文件、其余正常、不致引擎构造失败、不抛未捕获异常（验证：单测传非法配置内容断言降级不抛致命错误）。（AC6/N5）
- [ ] 跨协议一致：provider 适配层无 permission 相关改动（验证：按实际 provider 模块路径核对 diff 无改动）；anthropic 与 openai 各跑同一拦截场景行为一致。（AC14/N6）
- [ ] 可扩展性：新增一档模式只改模式兜底表、新增一层防御只在流水线插一层，改动不触及 provider 适配层（验证：核对正常改动的 diff 范围局限在 permission 模块）。（AC19/N9）
- [ ] 不破坏既有 Agent Loop / Plan Mode：多轮连环、用户取消、流出错恢复、历史一致、缓存命中、规划按轮次注入仍成立（验证：跑既有端到端关键场景；`pytest` 通过）。（AC16/N3）

## 编译与测试

- [ ] `python -m endless_code` 能正常启动（在合法配置下进入 TUI）。
- [ ] `python examples/smoke.py` 在 `Mode.BYPASS` 下不阻塞、跑完。
- [ ] `python -m ruff check .` 无告警（含 permission 子包）。
- [ ] `python -m ruff format --check .` 通过（或本地 `ruff format .` 已统一格式）。
- [ ] `python -m pytest` 通过（config/conversation/tool/agent/prompt/permission/tui 单测）。
- [ ] `pytest --timeout=30 tests/test_agent.py tests/test_permission_*.py tests/test_tui.py` 无超时（重点守护人在回路阻塞/取消）。（N4）
- [ ] （可选）`python -m mypy src/endless_code` 通过（含 permission 子包）。
- [ ] 含密钥的本地配置层已被 gitignore（验证：`git check-ignore .endless-code/settings.local.yaml` 命中）；对话区与任何输出均不出现 api_key（验证：检索输出）。（AC17）

## 端到端场景（Textual TUI 自动化/手动实跑）

- [ ] 场景 1（default 写需确认）：default 模式下让模型写一个新文件 → 弹出多行人在回路待批准块（工具名 + 参数 + 触发原因 + 三选菜单）；选「允许本次」→ 文件被写，Loop 继续。（AC10/F8）
- [ ] 场景 2（拒绝→改路径→完成闭环）：让模型写项目外路径 → 被拒（含「路径在项目目录之外」原因）→ 模型在后续轮改写到项目内合法路径并成功完成任务，体现「拒绝回灌让模型调整而非终止」。（AC11/F9）
- [ ] 场景 3（菜单交互）：待批准块用 ↑↓ 移动高亮 + 回车确认；数字键 1/2/3 亦可直选；默认高亮「允许本次」。（AC10/F8）
- [ ] 场景 4（永久放行 + 文件产物）：对某调用选「永久」→ (a) 用 cat/grep 确认本地层配置文件出现该精确 allow 条目；(b) 重启 endless-code 后同调用不再弹窗直接执行。（AC10/F8）
- [ ] 场景 5（acceptEdits）：Shift+Tab 切到 acceptEdits（状态栏左侧显示 `ACCEPT EDITS`）后再改文件不弹窗直接执行，但命令执行仍弹窗。（AC7/F5）
- [ ] 场景 6（bypass + 黑名单兜底）：Shift+Tab 循环到 bypassPermissions（状态栏左侧显示 `BYPASS`）后普通命令不弹窗；但让模型跑 `rm -rf /` 仍被黑名单拦下、回灌被拒。（AC1/AC7/N1）
- [ ] 场景 7（沙箱拦截）：让模型读 `/etc/passwd` 或写项目外路径 → 被沙箱拦、回灌「路径在项目目录之外」，模型据此停手或改项目内路径。（AC2/F2）
- [ ] 场景 8（plan 不变）：`/plan` 仅放只读工具产出计划、`/do` 执行——沿用现有 Plan Mode 行为不退步。（AC9/F7）
- [ ] 场景 9（取消）：人在回路弹窗时按 Esc → 干净回到空闲、不退出程序；再发一条消息可继续、不报 400。（AC12/N4）