# ADR-0015: Chunking strategies — measured tie, sentence by structure

**Status:** Accepted (2026-06)

## Context

"Naive splitting kills retrieval quality" is received wisdom. Forge now has
three chunking strategies (fixed word windows, sentence packing, paragraph
packing with sentence fallback) selectable per ingestion request
(`chunking` field) with a settings default, and a comparison harness
(`evals/compare_chunking.py`) that runs all three against the gold corpus —
grown for this milestone with long multi-fact documents and chunk-sensitive
metrics (`topic_in_top1`, `topic_recall`: does the retrieved *chunk* contain
the answer, which document-rank metrics can't see).

## Decision — and what the measurement actually said

- **The benchmark saturated.** All three strategies scored identically
  (every metric 1.0) at 250-, 100-, and 60-word budgets. At this corpus scale
  (~18 documents), dense embeddings of any reasonable chunk of the right
  document retrieve fine at top-4. The received wisdom is not *wrong* — it is
  **unmeasurable here**, and claiming a winner would be fiction.
- **Default = `sentence`, chosen on structure, not score:** the unit suite
  proves `fixed` cuts sentences mid-thought ("The liability cap is two ⏐
  million dollars…") — a latent failure mode the saturated benchmark can't
  punish yet — and `sentence` costs nothing measured. `paragraph` (the first
  rung of hierarchical; parent-child retrieval needs store support and is
  deferred) is available per-request.
- **The harness is the standing arbiter.** When the corpus grows or top-k
  shrinks, re-run `compare_chunking.py`; the default changes when a number
  says so.

## Consequences

- Two of the first "retrieval misses" turned out to be **dataset bugs**: the
  expected-topic phrasing didn't match the document wording ("required by
  law" vs "required to be *disclosed* by law"). Lesson for every eval built
  after this one: misses must be triaged against the ruler before the system
  — gold datasets have bugs too.
- A saturated benchmark is a finding with an expiry date: every future RAG
  milestone (hybrid search especially) must grow the corpus and question
  difficulty until metrics regain discriminating power, or its claims are
  unverifiable.
- Per-request strategy choice means a team's collection can mix strategies;
  re-chunking an existing corpus still requires re-ingestion (same re-index
  gap as ADR-0012).
