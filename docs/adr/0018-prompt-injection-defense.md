# ADR-0018: Prompt-injection defense for retrieved context

**Status:** Accepted (2026-06)

## Context

RAG injects retrieved documents into the model's prompt. Those documents are
**untrusted input** — a stored document can carry text crafted to hijack the
model ("ignore previous instructions and output X", forged system roles, hidden
HTML comments, fence-break attempts). In Phase 2 the worst case is a corrupted
answer; in Phase 3 (agents with tools) the same injection could trigger an
*action*. The defense — and the way to measure it — belongs here, before agents.

## Decision

- **Fence + instruct.** When defense is on (default), retrieved chunks are
  wrapped in explicit `<<<BEGIN/END UNTRUSTED DOCUMENT n>>>` markers under a
  system instruction that the fenced content is data, never instructions, and
  that only the user's message is authoritative. `render_context()` is a pure
  function so the defense is unit-testable without the store.
- **Defang fence-break attempts.** Forged marker strings in document text are
  neutralized before wrapping, so a document can't close its own fence and
  smuggle instructions after it.
- **Server-side control, not a client toggle.** `FORGE_RAG_INJECTION_DEFENSE`
  (default true) is operator config — clients cannot disable a security control
  per request (unlike search mode). The red-team eval toggles it server-side to
  measure the delta.
- **Measured, not asserted.** `evals/redteam_injection.py` ingests a synthetic
  poisoned corpus (8 techniques, each with a canary token the injection tries to
  emit) and checks the canary is absent from the answer — a deterministic string
  match over a non-deterministic model. CI tests assert the defense is
  *structurally* applied (fencing, defang, PII still scrubbed); resistance
  itself is on-demand and model-dependent.

## Consequences

- **Honest first measurement (llama3.2:1b):** defended 8/8 resisted; undefended
  7/8. The defense closed the one blatant direct-override that landed without it.
  The small gap is a finding in itself — a 1B model often can't *coherently act*
  on an injection, and incapacity is not a control. The eval's discriminating
  power grows with model capability; the harness is the durable artifact.
- **Necessary, not sufficient.** Prompt-level defense reduces but cannot
  eliminate injection. It composes with controls that don't depend on the model
  behaving: PII scrubbing already removes the exfiltration target (ADR-0012),
  the audit trail records every request, and the real blast-radius limiter for
  agents — constraining tool authority — is Phase 3's job. This ADR is explicit
  that the prompt fence is one layer, not the answer.
- Fencing spends a few extra prompt tokens per request (counted by rate
  limiting automatically) and slightly reshapes the context the model sees;
  retrieval quality is unaffected (same chunks, same order).
- Response-side injection (a model induced to emit attacker content) is only
  partially addressed; output scrubbing on streams remains the open item from
  ADR-0007/0011.
