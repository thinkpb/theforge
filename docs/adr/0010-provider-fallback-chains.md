# ADR-0010: Provider fallback chains on transient failures

**Status:** Accepted (2026-06)

## Context

Providers have outages, rate limits, and timeouts. A gateway whose job is "drop
this in front of your AI calls" should absorb transient provider failures
instead of forwarding them, when the operator has somewhere else to route.

## Decision

- Operators configure ordered fallback chains per alias
  (`FORGE_FALLBACK_MAP='{"gpt-4o": ["claude-fable-5", "llama3.2"]}'`); each
  entry in the chain is tried once, in order.
- **Only transient errors fall through**: timeouts, connection failures,
  provider rate limits, 5xx. BadRequest/Auth errors fail immediately — the
  request or the operator config is wrong, and another provider can't fix it.
- **The client contract holds**: the response reports the requested alias even
  when a fallback served it (ADR-0001). The **audit record carries the truth**
  (`upstream_model` = the provider that actually answered) — routing is
  invisible to clients, never to compliance.
- An exhausted chain returns the *last* error, honestly mapped (a final 429
  stays 429, not a generic 502).
- Unresolvable fallback aliases are skipped with a warning, not fatal — one
  config typo shouldn't break the primary route.
- Messages are scrubbed once (ADR-0007), before the first attempt; audited
  latency is total across attempts.

## Consequences

- Cross-provider fallback means the fallback provider sees the (scrubbed)
  prompt — for regulated deployments, fallback chains should respect data
  residency, which is exactly why chains are explicit operator configuration
  and not automatic.
- Same-provider retry with backoff is deliberately absent: chains-of-one-try
  keep tail latency bounded and the logic predictable. A retry policy can
  slot into the same loop later (the strategy-pattern attachment point from
  ARCHITECTURE.md).
- Streaming requests do not fall back yet — fallback applies before the first
  token; mid-stream failover is a much harder problem (ADR-0011).
