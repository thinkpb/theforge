# ADR-0011: SSE streaming with audit-at-stream-end

**Status:** Accepted (2026-06)

## Context

Streaming is table stakes for chat UX, but it complicates every compliance
property Forge guarantees: the audit record needs token counts that only exist
when the stream ends, errors can happen after the 200 status line is already
sent, and the OpenAI wire format (ADR-0004) prescribes SSE framing.

## Decision

- **Setup before streaming**: alias resolution, PII scrubbing (request side —
  unchanged from ADR-0007), and the provider call all happen before the HTTP
  response starts, so setup failures still return real status codes (400/429/
  502/504), not a broken stream.
- **Audit at stream end**: one record per stream, written when it finishes —
  total latency, the usage the provider reported in its final chunk, and the
  serving upstream. A mid-stream failure is audited as `upstream_error` and
  surfaced to the client as a final SSE `error` event (the 200 is already on
  the wire — the event is the only honest channel left).
- **ADR-0001 holds on every chunk**: each SSE event reports the alias, never
  the upstream model string. Streams terminate with `data: [DONE]` per the
  OpenAI format.

## Consequences

- Token counts on streams depend on the provider including usage in its final
  chunk (Ollama does via LiteLLM; verified live). Absent usage audits as NULL
  tokens — recorded, not guessed.
- Streams debit only the request-count rate limit, not tokens (ADR-0009) — the
  handler returns before usage exists. Fixable by threading a debit callback
  into the generator if it ever matters.
- Fallback chains (ADR-0010) don't apply to streams: failover after first
  token would mean replaying partial output. Pre-first-token failures could
  fall back in principle — deferred until someone needs it.
- Response-side PII scrubbing on streams remains the open Phase 1 question it
  was in ADR-0007 — scrubbing incremental output requires buffering that
  defeats streaming's purpose; a windowed scanner is the likely Phase 5 shape.
