# ADR-0009: Token-aware rate limiting with debit-after accounting

**Status:** Accepted (2026-06)

## Context

LLM APIs aren't REST APIs: the expensive unit is the token, not the request. A
request-count limit alone lets one key burn the provider budget with huge
prompts. But a request's token cost is only known *after* the provider answers
— pre-flight enforcement would require estimating tokens from raw text, which
is provider/tokenizer-specific and wrong at the margins.

## Decision

- **Two fixed-window counters per key per minute in Redis**: requests
  (incremented and checked pre-flight) and consumed tokens (debited
  post-completion from actual `usage`, checked pre-flight on the next request).
- **Debit-after accounting**: a key can overshoot its token budget by exactly
  one request, then receives 429 until the window resets. Honest and simple
  beats estimated and wrong.
- **Fail open when Redis is down** (loudly logged). Rate limiting protects cost
  and capacity, not compliance — the opposite call from the audit buffer
  (ADR-0006), and the contrast is deliberate: each control fails toward what it
  protects.
- **The master (admin) key is exempt**; per-key custom limits arrive when a
  deployment needs them — settings-level defaults (`FORGE_RATE_LIMIT_RPM/TPM`)
  cover Phase 1.
- **Rate-limited requests are audited** (`outcome='rate_limited'`) — "every
  request is audit-logged" includes the rejected ones.
- 429 responses carry `Retry-After` with seconds to window reset.

## Consequences

- Fixed windows allow up to 2× burst at window boundaries (end of one window +
  start of the next). A sliding-window or token-bucket upgrade is mechanical if
  it ever matters; the Redis key shape doesn't change.
- Streaming responses currently debit only the request counter — token usage
  for streams lands with better usage propagation (noted in ADR-0011).
- Redis enters the request hot path (one pipelined round trip pre-flight, one
  post-completion); both fail open.
