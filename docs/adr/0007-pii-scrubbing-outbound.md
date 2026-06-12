# ADR-0007: PII scrubbing at the outbound boundary

**Status:** Accepted (2026-06)

## Context

ADR-0005 commits Forge to PII scrubbing in the request pipeline from Phase 1. The
design questions: where in the pipeline, what detection stack, what happens to
detected entities, and how operators control it. The driving threat model: PII in
prompts leaving the operator's infrastructure for upstream providers (OpenAI,
Anthropic) whose retention and training policies the operator doesn't control.

## Decision

- **Scrub at the outbound boundary, in `gateway.complete()`** — after alias
  resolution, before the provider call. Same choke-point argument as audit
  (ADR-0006): every surface inherits it. Local providers (Ollama) are scrubbed
  too — uniform behavior beats clever exceptions.
- **Microsoft Presidio** (analyzer + anonymizer) with the **small spaCy model**
  (`en_core_web_sm`): regex/checksum recognizers for structured PII (SSN, email,
  phone, cards) plus NER for names. Detected entities are replaced with type
  markers (`<PERSON>`, `<US_SSN>`) — the model still sees document structure.
- **On by default; opting out is visible.** `FORGE_PII_SCRUBBING_ENABLED=false`
  passes content through, and the audit trail records `pii_redactions = NULL`
  (off) vs `0` (ran, found nothing) — different compliance statements. Per-key
  opt-out arrives with API key management.
- **Operator allow-list** (`FORGE_PII_ALLOW_LIST`) for domain vocabulary the NER
  model false-positives on.
- Scrubbing is CPU-bound and synchronous, so it runs in a worker thread
  (`asyncio.to_thread`); engines are built once per process.

## Consequences

- The PII boundary claim is now executable: the leakage test suite
  (`tests/test_pii.py`) asserts on exactly what the mocked provider received.
  Live verification: provider logs contain zero occurrences of fixture PII.
- **Small-model precision is a real cost.** Two findings from building the test
  suite: Presidio deliberately invalidates the classic fake SSN 123-45-6789
  (test fixtures must use realistic-format synthetic values), and
  `en_core_web_sm` tags drug names like "Metformin" as PERSON — over-scrubbing
  that destroys clinical utility. The allow-list is the operator-facing
  mitigation; swapping to `en_core_web_lg` (~560 MB) is a config change when a
  deployment values recall/precision over footprint.
- Inline scrubbing adds CPU latency to every request — acceptable at current
  scale, but it makes the load-testing milestone's latency budget non-optional.
- **Responses are not scrubbed in Phase 1.** The threat model is data leaving
  the operator's infrastructure; provider responses arriving back are a lower
  risk and a streaming-aware design problem — deferred to the streaming
  milestone, where scrubbing must work on incremental output anyway.
- English-only for now; multilingual support is a Presidio configuration
  surface, not a redesign.
