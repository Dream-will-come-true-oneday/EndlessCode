# Permission Audit and Lazy MCP Loading Checklist

## Functional and Integration

- [ ] Permission explanations cover read, write, Bash, blacklist, sandbox, rules, and modes; legacy tuple checks remain unchanged (verify `tests/test_permission.py`).
- [ ] A permissioned tool call writes all four lifecycle events with complete fields and redacted payloads (verify `tests/test_permission_audit.py`).
- [ ] Audit write failures are logged without failing execution (verify failure-injection test).
- [ ] `/audit` renders at most 50 current-session events without raw secrets (verify TUI test).
- [ ] MCP discovery consumes multiple `nextCursor` pages and keeps complete local schemas (verify catalog/manager tests).
- [ ] Initial definitions contain built-ins and `mcp_search_tools`, but no hidden MCP schemas (verify Registry test).
- [ ] Search results are stable, bounded, name/description-only, and activate matching tools idempotently (verify catalog test).
- [ ] Activated schemas appear on the next definitions call and hidden direct calls return guidance (verify catalog/agent tests).
- [ ] Search-then-call works across two Agent iterations and still uses permission checks (verify integration test).

## Engineering

- [ ] `python -m pytest -q` passes.
- [ ] `python -m ruff check .` passes.
- [ ] `python -m ruff format --check .` passes.
- [ ] `python -m compileall -q src examples` passes.

## End-to-End

- [ ] Start with an MCP server containing hidden tools, search from the model, observe only names/descriptions, then call an activated tool on the following iteration; observe the tool result and corresponding audit lifecycle entries.
