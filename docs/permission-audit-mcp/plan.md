# Permission Audit and ToolSearch-based Lazy MCP Loading Plan

## Status

Implemented and verified.

## Architecture Overview

The permission engine owns deterministic explanation generation. A session-scoped audit writer records engine checks, approval responses, and execution boundaries without becoming a dependency of successful execution. The MCP manager keeps the existing live sessions but builds an `McpCatalog`; Registry exposes built-ins, the `ToolSearch` meta-tool, and only tools in a client-owned activated set. The Agent constructs each provider request from persisted conversation messages plus one transient synthetic user message containing deferred MCP names. That message is never handed to `Conversation`, compaction, recovery, audit, or JSONL persistence. TUI consumes explanation data on approval and renders the current session audit.

## Core Data and Interfaces

- `PermissionExplanation`: decision, category, risk level, tool, target, mode, rule source, reason, requires approval, and read-only flag.
- `AuditWriter(session_dir, session_id)`: append event dictionaries with lock, flush, fsync, and best-effort logging; `recent(limit=50)` reads current JSONL safely.
- `McpCatalog`: register paginated MCP tools and search by stable token score; it owns deferred metadata and schema lookup but not the request lifecycle.
- `Registry.register(tool, exposed=True)`, `activate(name)`, `activated_tools`, `definitions()`, `deferred_mcp_names()`, and hidden-tool execution guidance. Built-ins and `ToolSearch` are always exposed; deferred MCP definitions are filtered by `activated_tools`.
- `McpSearchTool`: read-only ToolSearch-compatible meta-tool that searches the catalog, returns name/description summaries, and calls the Registry activation callback for matching names.
- `build_request_messages(messages, deferred_names)`: create a deep-copied provider message list and append one `Message(role="user", content=...)` when deferred names exist. The helper must not mutate or persist the source conversation.
- `deferred_tools_instruction()`: stable prompt module explaining that deferred MCP tools require `ToolSearch` before invocation.

## Module Design

### Permission

`Engine.explain()` computes category, target, sandbox/blacklist/rule/mode decision, risk, and a human-readable reason. `Engine.check()` delegates to it and keeps its tuple return type. Optional `AuditWriter` receives check and lifecycle events through Agent execution.

### Audit

`permission/audit.py` serializes only bounded, redacted summaries. Keys and secret-like values are replaced; long values are truncated. Writes catch filesystem/serialization errors and log warnings. The writer is session-specific and is replaced when the active session changes.

### MCP

`_serve_session()` repeatedly calls `list_tools(cursor=...)` until `nextCursor` is empty, adapting each page. The manager constructs a catalog and registers a `McpSearchTool`; all adapted MCP tools are registered deferred. Catalog activation resolves matching metadata, while Registry records canonical names in `activated_tools` and exposes their existing complete schemas without changing the remote caller. Hidden calls are rejected before permission/execution with guidance.

### Request Assembly

At the start of every normal Agent iteration, Registry supplies visible definitions and deferred MCP names. The Agent builds the provider request as follows:

1. Copy the persisted conversation messages.
2. Append one synthetic user message listing deferred names, with explicit request-scoped wording.
3. Send built-in and `ToolSearch` definitions plus complete definitions for `activated_tools`.
4. On a later iteration, rebuild both messages and definitions so a successful ToolSearch activation is visible immediately in the next request.

The synthetic message is excluded from `Conversation`, session writers, compact input, recovery attachments, and audit events. Context estimation for compaction must use persisted conversation history plus actual visible request definitions, without permanently adding the synthetic message.

### Registry and Agent

Registry tracks registration separately from exposure. `activated_tools: set[str]` is the sole activation source of truth for deferred MCP definitions; activation is idempotent and process-local. `definitions` and `read_only_definitions` filter deferred tools through that set; `get/execute` identify hidden tools and return the guidance result. Agent rebuilds definitions and request messages every loop, and ToolSearch activation therefore affects only the next provider request and later requests.

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
| MCP lazy boundary | LLM request assembly | MCP protocol still requires local schema discovery; only prompt payload is deferred |
| ToolSearch discovery hint | Stable system instruction plus transient user catalog | The model knows how to discover tools and which deferred names exist without persisting directory data in conversation history |
| Search activation | Up to five stable matches in `activated_tools` | Predictable prompt growth and idempotent local state |
| Synthetic message lifetime | Request-scoped, never persisted | Prevents repeated tool-directory messages from polluting history or compaction |
| Hidden execution | Guidance error | Makes the required discovery step explicit without changing remote calls |
