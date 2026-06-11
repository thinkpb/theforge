# ADR-0002: LiteLLM as the provider adapter

**Status:** Accepted (2026-06)

## Context

The gateway must call OpenAI, Anthropic, and local Ollama (with more providers
likely). Options: write per-provider adapters by hand, or adopt LiteLLM, which
normalizes ~all providers behind one OpenAI-shaped `acompletion()` call. Forge's
differentiating value is the layer **on top** — auth, cost tracking, rate limiting,
audit, compliance — not re-implementing provider SDK plumbing.

## Decision

Use LiteLLM as the provider adapter, but confine it to one module:
`src/forge/gateway/router.py` is the only place that imports or calls LiteLLM.
Upstream exceptions are translated to gateway HTTP errors at that boundary, so
LiteLLM types never cross into the API layer.

## Consequences

- Provider breadth for free, including streaming and token usage accounting we'll
  need for the cost-tracking milestone.
- LiteLLM is a heavy dependency with a fast release cadence; pinning and upgrade
  testing are part of maintenance.
- Its abstraction leaks on provider-specific parameters; because all calls go
  through one module, those leaks are contained and handled in one place.
- If LiteLLM ever becomes the wrong choice, the facade means replacing it is a
  one-module rewrite, not a platform rewrite.
