# Permission Audit and Lazy MCP Loading Plan

## Architecture Overview

The permission engine owns deterministic explanation generation. A session-scoped audit writer records engine checks, approval responses, and execution boundaries without becoming a dependency of successful execution. The MCP manager keeps the existing live sessions but builds an `McpCatalog`; Registry exposes built-ins, the search meta-tool, and only activated MCP tools. Agent requests continue to ask Registry for definitions each iteration, so activation becomes visible naturally on the following request. TUI consumes explanation data on approval and renders the current session audit.

## Core Data and Interfaces

- `PermissionExplanation`: decision, category, risk level, tool, target, mode, rule source, reason, requires approval, and read-only flag.
- `AuditWriter(session_dir, session_id)`: append event dictionaries with lock, flush, fsync, and best-effort logging; `recent(limit=50)` reads current JSONL safely.
- `McpCatalog`: register paginated MCP tools, search by stable token score, activate names, and report activation.
- `Registry.register(tool, exposed=True)`, `activate(name)`, `definitions()`, and hidden-tool execution guidance.
- `McpSearchTool`: read-only Tool that searches the catalog and activates matches, returning name/description summaries only.

## Module Design

### Permission

`Engine.explain()` computes category, target, sandbox/blacklist/rule/mode decision, risk, and a human-readable reason. `Engine.check()` delegates to it and keeps its tuple return type. Optional `AuditWriter` receives check and lifecycle events through Agent execution.

### Audit

`permission/audit.py` serializes only bounded, redacted summaries. Keys and secret-like values are replaced; long values are truncated. Writes catch filesystem/serialization errors and log warnings. The writer is session-specific and is replaced when the active session changes.

### MCP

`_serve_session()` repeatedly calls `list_tools(cursor=...)` until `nextCursor` is empty, adapting each page. The manager constructs a catalog and registers an `McpSearchTool`; all adapted MCP tools are registered hidden. Catalog activation marks existing tools exposed without changing the remote caller. Hidden calls are rejected before permission/execution with guidance.

### Registry and Agent

Registry tracks exposure separately from registration. `definitions` and `read_only_definitions` filter hidden tools; `get/execute` can identify hidden tools and return the guidance result. Agent keeps requesting definitions per loop, and search execution activates tools before the next provider request.

### TUI and CLI

Approval text includes explanation fields. `/audit` is handled only while idle and displays up to 50 audit entries for the current session. CLI creates the audit writer for the active session and passes it through app/agent construction; session resume swaps the writer path.

## Interaction Flow

1. MCP sessions initialize and paginate discovery into the local catalog.
2. Registry exposes built-ins plus `mcp_search_tools`; hidden MCP schemas remain local.
3. Agent checks each tool call, emits `permission_checked`, optionally awaits approval, then emits execution start/finish events.
4. A model search call activates matching catalog entries.
5. The next Agent iteration obtains Registry definitions containing activated schemas.
6. TUI `/audit` reads and redacts the active session's JSONL.

## File Organization

```text
src/endless_code/permission/audit.py   # best-effort session audit writer
src/endless_code/permission/engine.py  # explanation generation
src/endless_code/permission/__init__.py
src/endless_code/mcp/catalog.py        # local catalog and search
src/endless_code/mcp/tool.py           # MCP tool and search meta-tool
src/endless_code/mcp/manager.py        # paginated discovery and catalog wiring
src/endless_code/tool/__init__.py      # exposure-aware Registry
src/endless_code/agent/__init__.py     # lifecycle events and dynamic definitions
src/endless_code/tui/app.py            # approval explanation and /audit view
src/endless_code/cli.py                # session audit construction
tests/test_permission.py               # explanation coverage
tests/test_permission_audit.py         # lifecycle/redaction/failure coverage
tests/test_mcp_catalog.py              # pagination/search/exposure coverage
tests/test_agent.py                    # two-iteration activation flow
README.md                              # user-facing behavior
```

## Technical Decisions

| Decision | Choice | Reason |
|---|---|---|
| Explanation | Deterministic rules | No extra model latency and reproducible audit records |
| Audit storage | Per-session JSONL | Matches existing session persistence and append safety |
| MCP lazy boundary | LLM Registry exposure | MCP protocol still requires local schema discovery; only prompt payload is deferred |
| Search activation | Up to five stable matches | Predictable prompt growth and idempotent local state |
| Hidden execution | Guidance error | Makes the required discovery step explicit without changing remote calls |
