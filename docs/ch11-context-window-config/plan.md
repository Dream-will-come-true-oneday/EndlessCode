# 可配置上下文窗口与动态压缩阈值 Plan

## 架构概览

新增一个集中式上下文限制模块，将 `context_window` 转换为不可变的限制配置。Provider 配置层只负责确定有效窗口；Agent、TUI 和压缩子模块统一消费限制配置，不再直接引用固定的 1M 阈值。

```text
ProviderConfig.context_window
        │
        ▼
effective_context_window（未配置 -> 200K）
        │
        ▼
build_context_limits(context_window)
        │
        ├─ Agent 自动/紧急阈值
        ├─ TUI 恢复阈值
        ├─ layer1 工具结果落盘线
        ├─ layer2 摘要后保留量
        └─ recovery 恢复附件上限
```

## 核心数据结构与接口

### `ContextLimits`

在 `endless_code.compact.limits` 新增不可变数据类：

```python
@dataclass(frozen=True)
class ContextLimits:
    context_window: int
    single_result_bytes: int
    message_aggregate_bytes: int
    summary_reserve_tokens: int
    auto_safety_margin_tokens: int
    manual_safety_margin_tokens: int
    recent_keep_tokens: int
    recovery_tokens_per_file: int

    @property
    def auto_compact_threshold(self) -> int: ...

    @property
    def emergency_retry_threshold(self) -> int: ...

    @property
    def supports_auto_compaction(self) -> bool: ...
```

- `auto_compact_threshold = context_window - summary_reserve_tokens - auto_safety_margin_tokens`。
- `emergency_retry_threshold = context_window - summary_reserve_tokens - manual_safety_margin_tokens`。
- `supports_auto_compaction` 保留现有小窗口守卫语义：窗口必须大于摘要预留与自动安全余量之和。

### `build_context_limits`

```python
def build_context_limits(context_window: int) -> ContextLimits: ...
```

- 只接受正整数；配置中的 `0` 在调用前已由 `effective_context_window` 转换为默认 200K。
- 使用整数除法实现向上取整，避免浮点误差：`ceil(base * context_window / 200_000)`。
- token 阈值不封顶；工具字节阈值的缩放窗口按 `min(context_window, 400_000)` 计算，因此最多为基线 2 倍。
- 200K 和 1M 的结果严格匹配 `spec.md`；任意中间窗口使用同一公式。

### 兼容调用接口

- `ManageInput.context_window` 保持不变，并提供由该值生成限制配置的只读属性，避免调用方重复传递字段。
- `offload_and_snip` 增加可选限制参数；未传时使用 200K 默认配置，保持现有直接调用行为。
- `pick_recent_tail`、`build_recovery_attachment` 和 `render_file_block` 增加带 200K 默认值的内部限制参数，现有调用方式继续有效。
- `ContextLimits` 与 `build_context_limits` 从 `endless_code.compact` 导出，供 Agent、TUI 和测试使用。

## 模块设计

### 配置与运行时

- `config.py` 将 Anthropic、OpenAI/DeepSeek 的协议默认窗口统一改为 200,000；显式正整数优先和 `0` 的现有语义不变。
- `agent/__init__.py` 将 `SessionRuntime` 与 `new_session_runtime` 的默认窗口改为 200,000。
- Agent 每轮从运行时窗口取得同一份限制配置：自动提示事件使用 `auto_compact_threshold`，紧急压缩后使用 `emergency_retry_threshold`。
- TUI 恢复会话时使用 `auto_compact_threshold`，确保恢复和正常运行的触发点一致。

### 动态限制计算

- `compact/limits.py` 保存 200K 基线值、2 倍工具上限、缩放函数及 `ContextLimits`。
- `compact/const.py` 只保留不会随窗口变化的值：恢复文件数、最近消息数、熔断次数、PTL 重试参数、token 字符估算和预览大小。
- 不使用进程级可变全局阈值，多个 Provider 或多个 App 实例可安全使用不同窗口。

### 压缩与恢复链路

- `compact/compact.py` 通过 `ManageInput` 的限制配置执行小窗口守卫、层 1 落盘判断和自动压缩触发判断。
- `compact/layer1.py` 使用动态的单条与同消息聚合字节线；现有按大小降序落盘、决策冻结和预览格式不变。
- `compact/layer2.py` 使用动态 `recent_keep_tokens` 选择摘要后保留的近期消息；至少保留 5 条且不拆工具调用配对的规则不变。
- `compact/recovery.py` 使用动态 `recovery_tokens_per_file` 限制每个恢复附件；最多恢复 5 个文件的规则不变。
- 手动压缩不依赖自动触发线，但生成摘要后的保留量和恢复附件仍使用当前窗口对应的限制。

## 模块交互

```text
启动/选择 Provider
  -> load 配置
  -> effective_context_window
  -> new_session_runtime(context_window)

每轮 Agent
  -> build_context_limits(runtime.context_window)
  -> estimate_tokens
  -> 达到 auto_compact_threshold 时发出压缩事件
  -> manage_context
       -> layer1 按动态字节线落盘
       -> 仍达到动态阈值时 layer2 摘要
       -> 按动态 recent/recovery 上限重建历史

Provider 返回上下文超限
  -> 紧急摘要
  -> 估算结果达到 emergency_retry_threshold 时停止重试
  -> 否则只重试原请求一次

恢复历史会话
  -> build_context_limits(runtime.context_window)
  -> 达到 auto_compact_threshold 时先压缩
  -> 再进入空闲态
```

## 文件组织

```text
src/endless_code/config.py                  # 默认窗口改为 200K
src/endless_code/agent/__init__.py          # 运行时默认值及动态触发线
src/endless_code/tui/app.py                 # 恢复路径使用动态触发线
src/endless_code/compact/limits.py          # 新增限制配置与缩放公式
src/endless_code/compact/const.py           # 仅保留固定常量
src/endless_code/compact/compact.py         # 统一取得并传递限制配置
src/endless_code/compact/layer1.py          # 动态工具输出保护线
src/endless_code/compact/layer2.py          # 动态近期 token 保留量
src/endless_code/compact/recovery.py        # 动态恢复附件上限
tests/test_limits.py                        # 新增公式、取整与封顶测试
tests/test_config.py                        # 默认/显式窗口测试
tests/test_compact.py                       # 落盘、摘要、恢复与触发行为
tests/test_agent.py                         # 自动/紧急阈值集成测试
tests/test_tui.py                           # 恢复路径阈值测试
README.md                                   # 默认 200K 与显式 1M 示例
.endless-code/config.yaml.example           # 200K 默认及 1M 配置注释
docs/ch10-1m-context/{spec.md,plan.md}       # 标注固定 1M 方案已被本方案取代
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 动态值承载方式 | 不可变 `ContextLimits` | 所有消费方使用同一计算结果，避免散落公式与全局状态 |
| token 阈值缩放 | 200K 基线连续线性缩放、向上取整 | 支持任意窗口并保留安全余量，不因取整向下而提前耗尽预算 |
| 工具结果缩放 | 连续线性缩放，最多 2 倍 | 允许大窗口读取更大结果，同时避免 1M 工具输出无限膨胀 |
| 默认窗口 | 三协议统一 200K | 用户要求且避免协议间隐式差异 |
| 非正窗口处理 | 配置层保留 `0`=默认；动态计算函数拒绝非正值 | 区分配置占位值和有效运行时窗口，尽早暴露内部错误 |
| helper 兼容性 | 新限制参数带 200K 默认值 | 降低内部调用和既有测试的迁移风险 |
| 旧 1M 文档 | 保留历史内容并标注已取代 | 维持变更历史，同时避免读者将其误认为当前默认 |

## 测试设计

- 表驱动验证 128K、200K、300K、512K、1M、2M 的 token 比例、向上取整和工具 2 倍封顶。
- 验证 200K 默认与 1M/512K 显式配置在三种协议上的有效窗口。
- 分别在 200K 与 1M 限制下验证单条工具结果和聚合结果的边界值、超限落盘及稳定预览。
- 验证摘要近期保留量、恢复附件截断量随窗口变化，固定的消息数和文件数不变。
- 验证 Agent 自动压缩、紧急重试及 TUI 恢复路径使用同一动态阈值。
- 运行全量 `pytest`、Ruff 检查、格式检查和 `compileall`；不运行会调用真实 Provider 的 smoke 请求。
