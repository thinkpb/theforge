# ADR-0016: Hybrid search — dense + BM25 with server-side RRF fusion

**Status:** Accepted (2026-06)

## Context

Dense embeddings are bad at exact tokens: alert codes, policy numbers, section
references. The keyword dataset added for this milestone (near-duplicate
incident runbooks differing only by codes like ERR-4471 / ERR-4472) made the
failure measurable: dense-only retrieval put the wrong runbook first for both
code queries. ADR-0015's saturation warning held — the benchmark had to get
harder before this milestone could prove anything.

## Decision

- **Named vectors per point in Qdrant**: a dense vector (`nomic-embed-text`)
  and a BM25 sparse vector (fastembed `Qdrant/bm25`, term frequencies
  client-side, IDF applied server-side via `Modifier.IDF`).
- **Hybrid queries fuse both legs server-side** with Reciprocal Rank Fusion
  (Qdrant Query API prefetch + `Fusion.RRF`). `mode: "dense"` remains available
  per request; the settings default is hybrid.
- **The sparse leg makes scrub-before-embed non-negotiable** (ADR-0012): a BM25
  index is *readable* — indices map to tokens, values to weights. Dense vectors
  leak PII statistically; a sparse index of raw text would store it almost
  verbatim. Sparse vectors are computed from scrubbed text only, and the test
  suite proves an identifier query cannot match (the index never saw the
  identifier).

## Measured result (27-item corpus)

- top-1: dense 0.926 hit@1; hybrid 0.963 — the gain is exactly the exact-token
  queries dense cannot disambiguate.
- top-4 (what RAG injection uses): both modes recover everything (hit@4 1.0).
- **RRF ties are real**: a doc ranked 1st by one leg and unranked by the other
  scores 0.5 from either side, so dense-wrong vs BM25-right can tie at rank-1
  and break either way. Cross-encoder reranking — the principled tie-breaker —
  is the deferred next rung, noted rather than rushed.

## Consequences

- `min_score` is score-space dependent: cosine for dense, RRF rank scores
  (rank-1 ≈ 0.5, rank-2 ≈ 0.33) for hybrid. Documented at the API; clients
  using thresholds must know their mode.
- Collection schema changed (named + sparse vectors): existing collections
  need re-ingestion — the third entry in the re-index-job ledger
  (ADR-0012, ADR-0015). That job is now clearly owed.
- fastembed/onnxruntime joins the platform deps. Exposed during install: the
  dev venv had been Intel-under-Rosetta since the repo's first day (built by
  the old x86 uv); onnxruntime's missing Intel-macOS wheel forced the rebuild
  to native arm64 that should have happened in the Homebrew migration.
- Baseline updated to the 27-item corpus: hit@1 0.926, hit@4 1.0, MRR 0.957,
  zero PII leaks. The keyword misses (RRF ties) are visible in the baseline —
  the reranking milestone has its target number.
