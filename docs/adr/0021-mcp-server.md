# ADR-0021: MCP server — Forge tools over the Model Context Protocol

**Status:** Accepted (2026-06)

## Context

The Model Context Protocol is becoming the standard way clients (Claude Desktop,
IDEs, other agents) discover and call tools. Forge already has a team-scoped,
audited tool registry (ADR-0019/0020); exposing it over MCP lets any MCP client
use Forge tools without bespoke integration. The question is how to do it without
opening a hole in the compliance model.

## Decision

- **Implement the JSON-RPC 2.0 core of the spec directly** (`forge/mcp.py`):
  `initialize`, `tools/list`, `tools/call`, plus notification handling and
  batches. This is the request/response subset of the Streamable HTTP transport —
  the client POSTs a message to `/mcp` and gets a JSON-RPC response; there's no
  server-initiated SSE stream (not needed for tool exposure). Building the
  protocol rather than pulling a framework keeps the surface small and auditable
  and makes the spec legible.
- **MCP is another surface onto the same tools, not a bypass.** `/mcp` uses the
  same Forge bearer auth; the team comes from the key. Tool calls run through the
  identical `ToolContext` + `REGISTRY` as the agent runtime, are team-scoped, and
  are audited (`tool_call`, `agent="mcp"` so MCP-origin calls are attributable).
- **Spec-correct error semantics:** protocol errors are JSON-RPC errors (unknown
  method → -32601, unknown tool → -32602); *tool* faults are `isError: true`
  results, per MCP convention — the client sees the failure without the call
  being a transport error.

## Consequences

- Any MCP client authenticates with a Forge team key and gets exactly that team's
  tools, with every call landing in the audit trail — the compliance boundary
  holds across the new surface.
- The whole registry is exposed; per-tool MCP gating (an operator allow-list of
  which tools are MCP-visible) is a future refinement. Today every registered
  tool is safe and team-scoped, so exposing all is acceptable and documented.
- No server→client streaming (SSE), resources, or prompts yet — only tools.
  Those are additive MCP capabilities if a use case needs them.
- Tool results returned to the MCP client carry whatever the tool produced;
  `document_search` returns already-scrubbed text (ADR-0012), so the PII boundary
  extends to MCP for free.
