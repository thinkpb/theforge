# ADR-0005: Compliance is the core design principle, not a final phase

**Status:** Accepted (2026-06) — supersedes the original phase ordering

## Context

The original roadmap treated compliance as Phase 5: build a generic AI platform
(gateway → RAG → agents → MLOps), then add PII scrubbing, audit logs, and HIPAA-ready
deployment at the end. A survey of the open-source landscape
([POSITIONING.md](../POSITIONING.md)) showed the generic-platform space is crowded —
Dify (143k stars), RAGFlow (81k), Flowise (42k) — while the regulated-industry angle
is genuinely unoccupied: no open-source project ships audit logging, PII scrubbing,
and compliance-ready deployment as defaults. Compliance bolted on late also tends to
leak: components built without audit hooks or PII boundaries need retrofitting, and
retrofits miss paths.

## Decision

Compliance-by-default is a foundation property, present from Phase 1:

- **Every request is audit-logged by default.** The audit trail is append-only and
  is built alongside the gateway, not after it.
- **PII scrubbing lives in the request pipeline**, on by default; opting out is a
  per-key configuration that is itself audit-logged.
- **Each later phase inherits the posture**: RAG scrubs at ingestion time, the agent
  runtime audits every tool call, MLOps runs training data through the same PII
  pipeline.
- **Phase 5 packages and proves** compliance (Helm chart, HIPAA/SOC2 control
  mapping, tenant isolation) — it does not introduce it.

Positioning follows the design: "the open-source AI infrastructure layer for
regulated industries," not "an AI platform that supports compliance."

## Consequences

- Presidio and the Postgres audit schema enter the dependency surface in Phase 1
  instead of Phase 5 — more upfront work before the first flashy demo.
- Inline PII detection adds latency to every request; performance budgets and a
  fast-path design are now Phase 1 concerns, not afterthoughts.
- Streaming (SSE) gets harder: audit records and scrubbing must handle incremental
  output, which constrains how streaming is implemented from the start.
- Every new component has two non-negotiable acceptance criteria: it emits audit
  events, and it respects the PII boundary. This belongs in code review checklists.
- The project stops competing with Dify-class platforms on breadth and competes
  instead in an empty niche — smaller audience, but one with budget and real
  requirements, and a defensible story.
