# Permission Audit and Lazy MCP Loading Tasks

## File List

| Operation | File | Responsibility |
|---|---|---|
| Add | `src/endless_code/permission/audit.py` | Session audit writer and redaction |
| Modify | `src/endless_code/permission/engine.py`, `src/endless_code/permission/__init__.py` | Permission explanations |
| Add | `src/endless_code/mcp/catalog.py` | MCP catalog/search/activation |
| Modify | `src/endless_code/mcp/tool.py`, `src/endless_code/mcp/manager.py` | Search tool and paginated discovery |
| Modify | `src/endless_code/tool/__init__.py` | Exposure-aware registry |
| Modify | `src/endless_code/agent/__init__.py` | Audit lifecycle and dynamic definitions |
| Modify | `src/endless_code/tui/app.py`, `src/endless_code/cli.py` | Approval explanation and `/audit` |
| Add/modify | `tests/test_permission_audit.py`, `tests/test_mcp_catalog.py`, existing tests | Regression and integration coverage |
| Modify | `README.md` | User documentation |

## T1: Permission Explanation

**Dependencies:** None

Implement `PermissionExplanation`, risk/source classification, and `Engine.explain`; delegate `check` while retaining its tuple API. Add focused tests and run the permission suite.

**Verification:** `python -m pytest -q tests/test_permission.py`

## T2: Audit Writer and Agent Lifecycle

**Dependencies:** T1

Add redacted JSONL writer and connect permission checks, approval responses, execution start, and finish. Preserve best-effort semantics. Add writer tests and run agent/permission tests.

**Verification:** `python -m pytest -q tests/test_permission_audit.py tests/test_agent.py`

## T3: MCP Catalog and Pagination

**Dependencies:** None

Add catalog/search meta-tool, paginate `list_tools`, and make Registry exposure-aware. Verify hidden schemas, stable search, activation, and guidance errors.

**Verification:** `python -m pytest -q tests/test_mcp_catalog.py tests/test_mcp_manager.py tests/test_mcp_tool.py tests/test_tool.py`

## T4: Agent/TUI/CLI Integration

**Dependencies:** T2, T3

Expose definitions per iteration, display explanations, add `/audit`, wire active session audit, and cover search-then-call integration.

**Verification:** `python -m pytest -q tests/test_agent.py tests/test_tui.py`

## T5: Documentation and Full Verification

**Dependencies:** T1, T2, T3, T4

Update README and run all project checks.

**Verification:** `python -m pytest -q`; `python -m ruff check .`; `python -m ruff format --check .`; `python -m compileall -q src examples`

## Execution Order

```text
T1 -> T2 -> T3 -> T4 -> T5
```
