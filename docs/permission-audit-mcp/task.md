# Permission Audit and ToolSearch-based Lazy MCP Loading Tasks

## Status

Implemented and verified.

## File List

| Operation | File | Responsibility |
|---|---|---|
| Add | `src/endless_code/permission/audit.py` | Session audit writer and redaction |
| Modify | `src/endless_code/permission/engine.py`, `src/endless_code/permission/__init__.py` | Permission explanations |
| Add | `src/endless_code/mcp/catalog.py` | MCP catalog/search/activation |
| Modify | `src/endless_code/mcp/tool.py`, `src/endless_code/mcp/manager.py` | ToolSearch and paginated discovery |
| Modify | `src/endless_code/tool/__init__.py` | Client-owned activated tool set |
| Modify | `src/endless_code/agent/__init__.py` | Request-scoped message assembly and audit lifecycle |
| Modify | `src/endless_code/prompt/modules.py`, `src/endless_code/prompt/__init__.py` | Stable ToolSearch instruction |
| Modify | `src/endless_code/tui/app.py`, `src/endless_code/cli.py` | Approval explanation and `/audit` |
| Add/modify | `tests/test_permission_audit.py`, `tests/test_mcp_catalog.py`, `tests/test_agent.py`, `tests/test_prompt.py` | Regression and integration coverage |
| Modify | `README.md` | User documentation |

## T1: Permission Explanation

**Dependencies:** None

Implement `PermissionExplanation`, risk/source classification, and `Engine.explain`; delegate `check` while retaining its tuple API. Add focused tests and run the permission suite.

**Verification:** `python -m pytest -q tests/test_permission.py`

## T2: Audit Writer and Agent Lifecycle

**Dependencies:** T1

Add redacted JSONL writer and connect permission checks, approval responses, execution start, and finish. Preserve best-effort semantics. Add writer tests and run agent/permission tests.

**Verification:** `python -m pytest -q tests/test_permission_audit.py tests/test_agent.py`

## T3: Registry Activation Set

**Files:** `src/endless_code/tool/__init__.py`, `src/endless_code/mcp/catalog.py`, `src/endless_code/mcp/tool.py`

**Dependencies:** T2

Replace implicit exposure flags as the lazy-loading source of truth with a client-owned `activated_tools: set[str]`. Keep built-ins and ToolSearch visible, keep deferred MCP schemas hidden, make activation idempotent, and preserve hidden-call guidance. Add tests for initial visibility, activation, repeated activation, and complete schema export.

**Verification:** `python -m pytest -q tests/test_mcp_catalog.py tests/test_tool.py tests/test_mcp_tool.py`

## T4: ToolSearch Prompt and Request Assembly

**Files:** `src/endless_code/prompt/modules.py`, `src/endless_code/prompt/__init__.py`, `src/endless_code/agent/__init__.py`, `tests/test_prompt.py`, `tests/test_agent.py`

**Dependencies:** T3

Add a stable system-prompt instruction that names ToolSearch and requires discovery before deferred-tool invocation. Add request-scoped assembly that copies persisted messages and appends exactly one synthetic user message containing all deferred MCP names when present. Ensure the synthetic message is absent from the source Conversation and is not passed into compact, recovery, audit, or JSONL persistence paths.

**Verification:** `python -m pytest -q tests/test_prompt.py tests/test_agent.py`

## T5: MCP Discovery and Agent Integration

**Files:** `src/endless_code/mcp/manager.py`, `src/endless_code/cli.py`, `tests/test_mcp_catalog.py`, `tests/test_agent.py`, existing MCP manager tests

**Dependencies:** T4

Keep paginated MCP discovery and local schema caching. Register ToolSearch and deferred tools through the activation-set API. Cover the full flow: the request includes deferred names, the model invokes ToolSearch, activation updates the client set, and the next request includes the activated schema. Verify MCP calls still pass through the existing permission chain.

**Verification:** `python -m pytest -q tests/test_mcp_catalog.py tests/test_mcp_manager.py tests/test_mcp_tool.py tests/test_tool.py tests/test_agent.py`

## T6: Agent/TUI/CLI Integration

**Dependencies:** T2, T5

Expose definitions per iteration, display explanations, add `/audit`, wire active session audit, and cover search-then-call integration.

**Verification:** `python -m pytest -q tests/test_agent.py tests/test_tui.py tests/test_permission_audit.py`

## T7: Documentation and Full Verification

**Files:** `README.md`, `docs/permission-audit-mcp/spec.md`, `docs/permission-audit-mcp/plan.md`, `docs/permission-audit-mcp/checklist.md`

**Dependencies:** T1, T2, T5, T6

Document the revised ToolSearch flow, request-scoped deferred-name message, activated set, and five-layer permission audit. Run all project checks and inspect the final diff for accidental persistence of synthetic messages.

**Verification:** `python -m pytest -q`; `python -m ruff check .`; `python -m ruff format --check .`; `python -m compileall -q src examples`

## Execution Order

```text
T1 -> T2 -> T3 -> T4 -> T5 -> T6 -> T7
```
