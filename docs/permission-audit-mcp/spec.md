# Permission Audit and Lazy MCP Loading Spec

## Status

Implemented and verified.

## Revision Scope

This revision adopts the explicit ToolSearch interaction: the model is told that
some MCP tools are not loaded, each request receives a transient user message
listing all deferred tool names, and a client-owned activated-tool set controls
which complete schemas enter the next API request.

## Background

The project already protects tool execution with a permission engine and connects to MCP servers, but permission decisions are opaque, execution history is not auditable, and every discovered MCP schema is sent to the model on every request. This increases review friction, weakens operational visibility, and wastes context on large MCP installations.

## Goals

- Make every permission decision explainable and deterministic.
- Persist a redacted permission audit trail for the complete lifecycle of permissioned tool calls.
- Discover MCP tools locally while exposing complete schemas to the model only after an explicit search and activation.
- Keep existing built-in tools, remote MCP calls, permission checks, and public compatibility intact.

## Functional Requirements

- F1: Permission checks produce a stable explanation containing decision, category, risk level, tool, target, mode, rule source, reason, and approval requirement; the existing check result remains compatible.
- F2: Risk is low for read-only work, medium for file writes/edits, high for Bash, non-read-only MCP, or unknown side effects, and critical for blacklist or sandbox violations.
- F3: The approval view shows risk, target, rule source, and impact explanation.
- F4: Permissioned tool calls emit `permission_checked`, `approval_responded`, `execution_started`, and `execution_finished` audit events with session/call identity, timestamps, tool/target/mode, read-only flag, decision/approval, risk/source, duration, error flag, and redacted argument/result summaries.
- F5: Audit entries append to `.endless-code/sessions/<session_id>/permission-audit.jsonl` with locking and flush/fsync. Audit failures are logged and do not block the agent. `/audit` shows the current session's latest 50 redacted events.
- F6: MCP discovery handles `tools/list` pagination and builds an in-process catalog containing names, servers, descriptions, schemas, and read-only hints without exposing hidden schemas to the model.
- F7: The stable system prompt tells the model that some MCP tools are not loaded and must be queried through `ToolSearch` before invocation; this instruction is present on every normal Agent request.
- F8: Each normal Agent request receives one transient synthetic user message listing all currently deferred MCP tool names. The message is request-scoped, is not appended to conversation history, is not persisted to JSONL, and contains names only rather than complete schemas.
- F9: The read-only `mcp_search_tools` meta-tool accepts `query` and `limit` from 1 through 10, performs stable keyword matching, returns only names and short descriptions, and adds at most five matching tools by default to the client-owned activated-tool set. Activation is idempotent and process-local.
- F10: Built-in tools and `mcp_search_tools` are initially exposed. The tools array contains no deferred MCP schema; schemas for tools in the activated-tool set appear in the next request. Direct calls to hidden MCP tools return a readable guidance error.

## Non-functional Requirements

- Preserve existing MCP `call_tool` behavior and permission chain.
- Keep the activated-tool set separate from the conversation and do not persist MCP activation state.
- Keep the synthetic deferred-tool message out of conversation compaction, session recovery, audit records, and JSONL persistence.
- Do not expose secrets or complete command arguments/results in audit output.
- Keep changes within the local CLI/TUI, permission, MCP, Registry, Agent, tests, and README scope.

## Out of Scope

- Cloud agents, subagents, plugins, LSP, remote audit aggregation, and a generic MCP call proxy.

## Acceptance Criteria

- AC1: Tests can inspect deterministic explanations for read, write, Bash, blacklist, sandbox, rule, and mode cases while existing `check` callers still receive `(Decision, reason)`.
- AC2: A permissioned call produces all four lifecycle event types in valid JSONL, with redaction and non-blocking write failure behavior.
- AC3: `/audit` renders no more than 50 current-session events and contains no raw secrets or full payloads.
- AC4: Multi-page MCP fixtures are fully cataloged; initial Registry definitions omit hidden MCP schemas.
- AC5: Every normal request contains the fixed ToolSearch instruction and exactly one transient deferred-tool-name message; neither is persisted in conversation history.
- AC6: Searching activates stable matches within the limit; the next request's tools array includes their full schemas, repeated activation is harmless, and hidden direct execution returns guidance.
- AC7: An agent integration test covers the complete flow: deferred names in the request, ToolSearch in one iteration, and invocation of the activated tool in the next iteration.
- AC8: README documents the permission explanation, audit file/command, and the revised ToolSearch-based lazy MCP behavior.
- AC9: `pytest`, `ruff check`, `ruff format --check`, and `compileall` pass (or environment-only fixture restrictions are explicitly recorded).
