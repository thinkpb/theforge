# ADR-0012: PII-safe RAG — scrub before embed, team-scoped collections

**Status:** Accepted (2026-06)

## Context

Phase 2 adds document ingestion and retrieval. For regulated content the
critical question is *where in the pipeline scrubbing happens*. A vector
computed from raw text **encodes the PII**: once it's in vector space no
scrubber can reach it, and nearest-neighbor queries can partially reconstruct
what went in. Scrubbing at query time — the common pattern — protects nothing
at rest.

## Decision

- **Pipeline order is the design: chunk → scrub → embed → store.** The vector
  store only ever holds scrubbed text and vectors *of* scrubbed text. The
  leakage test reads Qdrant payloads directly — the claim is about what's at
  rest, so the test asserts on what's at rest.
- **Search queries go through the same scrubber.** Against a scrubbed corpus,
  identifiers in a query can't match anything anyway (the stored side says
  `<PERSON>`), so this costs no recall — and keeps the outbound boundary
  uniform if the embedding model is ever remote.
- **Collections are team-scoped by the caller's key** (`forge_<team>`): there
  is no collection parameter to get wrong, or to attack. Isolation by
  construction, not by query filter.
- **Local embeddings by default** (`ollama/nomic-embed-text` via LiteLLM, same
  adapter as completions): documents never leave the operator's infrastructure
  on the ingestion path.
- **Curated PII entity list** (`FORGE_PII_ENTITIES`), platform-wide: scrub
  identifiers (PERSON, SSN, EMAIL, PHONE, CREDIT_CARD, LOCATION, …), not every
  date-like string. Found the hard way: Presidio's DATE_TIME recognizer tagged
  "47 days" and "quarterly" — destroying the facts retrieval exists to find.
  HIPAA deployments that must suppress personal dates add DATE_TIME back.
- **Ingestion and search are audited** as first-class event types (`event`
  column, migration 0004) with redaction counts — the per-key PII opt-out
  stores raw content but always leaves its NULL-redactions trace (ADR-0008).
- Chunking starts as fixed-size word windows with overlap behind a strategy
  signature; sentence-aware and hierarchical chunkers are later milestones.

## Consequences

- Scrubbing at ingestion means the corpus is scrubbed with the entity config
  *at ingestion time* — changing `FORGE_PII_ENTITIES` later requires
  re-ingestion to apply retroactively. Worth a re-index job in a later
  milestone.
- Over-scrubbing trades retrieval quality for safety (e.g. the small NER model
  tags drug names as PERSON, ADR-0007); the allow-list is the operator's
  recall lever. The RAGAS eval milestone will quantify this trade-off.
- Synchronous ingestion limits document size; the async job-queue milestone
  lifts that.
- Embedding dimension is pinned in config (768 for nomic-embed-text); changing
  embedding models requires new collections — another reason for the re-index
  job.
