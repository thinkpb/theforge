# ADR-0013: RAG injection as an additive chat extension

**Status:** Accepted (2026-06)

## Context

Phase 2's promise is that retrieval "plugs into the gateway": teams upload
documents, then get grounded answers from the same OpenAI-compatible endpoint
their SDKs already call. The wire-compatibility constraint (ADR-0004) rules
out changing existing fields; the compliance constraints (ADR-0005/0012) mean
retrieval must stay team-scoped and audited.

## Decision

- **Additive request field `rag`** (`{top_k, min_score}`) on
  `/v1/chat/completions`. Absent → plain completion, byte-for-byte OpenAI
  behavior. Present → the last user message is used as the retrieval query
  against the caller's **team collection** (never a parameter).
- **Context is prepended as a system message** built from retrieved chunks,
  with an instruction to admit ignorance rather than guess. Chunks are
  already-scrubbed text (ADR-0012), and the full message list still passes the
  outbound scrubber — re-scrubbing marker text is harmless by construction.
- **Sources are returned in an additive `forge_rag` response field**
  (doc_id, title, chunk_index, score) — grounding you can show an auditor.
- **The retrieval is audited as its own `search` event**, alongside the
  completion event. Retrieval happens in the handler setup phase, so RAG works
  with streaming too (streams don't carry the `forge_rag` extension yet).
- Empty retrieval (no documents, all below `min_score`) degrades gracefully:
  unmodified messages, empty sources — not an error.

## Consequences

- Two audit events per RAG chat with independent request ids; threading one
  correlation id through both is a small future improvement.
- Retrieval quality is now user-visible, which makes the eval milestone
  urgent. Live finding that proves the point: with llama3.2:1b the pipeline
  delivered the right chunk and the model **quoted the answer while claiming
  it didn't know** — pipeline correctness and answer quality are different
  layers (TESTING.md), measured by different tools.
- `min_score` is the only relevance control; reranking and hybrid search are
  the next retrieval-quality milestones and slot in behind `search_documents`.
- The injected context spends prompt tokens; rate limiting counts them via
  debit-after accounting automatically (ADR-0009).
