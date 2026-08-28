# Permission Audit and ToolSearch-based Lazy MCP Loading Checklist

## Status

Implemented and verified.

## Functional and Integration

- [x] Permission explanations cover read, write, Bash, blacklist, sandbox, rules, and modes; legacy tuple checks remain unchanged (verify `python -m pytest -q tests/test_permission.py`).
- [x] A permissioned tool call writes `permission_checked`, `approval_responded`, `execution_started`, and `execution_finished` with complete fields and redacted payloads (verify `python -m pytest -q tests/test_permission_audit.py`).
- [x] Audit write failures are logged without failing execution (verify the failure-injection test in `tests/test_permission_audit.py`).
- [x] `/audit` renders at most 50 current-session events without raw secrets (verify the TUI audit test).
- [x] MCP discovery consumes multiple `nextCursor` pages and keeps complete local schemas (verify `python -m pytest -q tests/test_mcp_catalog.py tests/test_mcp_manager.py`).
- [x] Initial `tools` definitions contain built-ins and ToolSearch but no deferred MCP schemas (verify the Registry request test).
- [x] The stable system prompt tells the model that some MCP tools are not loaded and must be queried with ToolSearch (verify `python -m pytest -q tests/test_prompt.py`).
- [x] Each normal provider request contains exactly one transient synthetic user message listing all deferred MCP names when deferred names exist, and no such message when the list is empty (verify Agent request assembly tests).
- [x] The synthetic deferred-name message is absent from `Conversation`, session JSONL, compaction input, recovery attachments, and permission audit events (verify persistence and compaction regression tests).
- [x] ToolSearch returns stable name/description-only matches within the limit and updates the client-owned activated set idempotently (verify `python -m pytest -q tests/test_mcp_catalog.py`).
- [x] Activated tools appear with complete schemas only in the next request, while hidden direct execution returns ToolSearch guidance (verify Registry and Agent integration tests).
- [x] Search-then-call works across two Agent iterations and the activated MCP call still passes through permission checks (verify the end-to-end Agent test).

## Engineering

- [x] `python -m pytest -q` passes.
- [x] `python -m ruff check .` passes.
- [x] `python -m ruff format --check .` passes.
- [x] `python -m compileall -q src examples` passes.
- [x] `git diff --check` reports no whitespace errors and the final diff contains no synthetic-message persistence.

## End-to-End

- [x] Start with an MCP server containing deferred tools, inspect the first provider request to confirm only built-ins and ToolSearch schemas plus the transient deferred-name message, invoke ToolSearch, inspect the next request for the activated complete schema, then call the tool and observe its result and permission audit lifecycle entries.
- [x] Resume or compact a session after ToolSearch activation and confirm the synthetic deferred-name message is not restored as conversation history while the process-local activated set retains the intended behavior.
