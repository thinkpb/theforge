# ADR-0014: RAG eval pipeline — black-box, two metric families, baseline-gated

**Status:** Accepted (2026-06)

## Context

ADR-0013's live finding made the gap concrete: the pipeline delivered the right
chunk and the model still answered wrongly. Pipeline correctness is assertable
with pytest; answer quality needs evals (TESTING.md Layer 2). The chunking and
hybrid-search milestones also need a ruler before they can claim improvement.

## Decision

- **Black-box over HTTP.** The harness (`evals/`) drives a running gateway as a
  user: fresh eval team key per run, real ingestion, real retrieval, real RAG
  chat. It evaluates the system, not a unit under glass — auth, scrubbing,
  chunking, and injection are all inside the measurement.
- **Gold dataset in-repo** (`evals/datasets/*.jsonl`): synthetic healthcare and
  legal Q&A, each item carrying its source doc, ground truth, expected topics,
  and a `should_not_contain` PII list. Unit tests validate the dataset itself
  (schema, answerability, leak-checks-that-can-fire, realistic-format SSNs).
- **Two metric families:**
  - *Retrieval* — deterministic, no judge: hit@1, hit@k, MRR, PII-leak count.
    Exact and comparable across runs.
  - *Generation* — RAGAS faithfulness + answer relevancy, LLM-judged via a
    local Ollama judge (default llama3.1:8b), run sequentially because Ollama
    serializes requests. Scores are regression lines, not absolute truths.
- **Committed baseline** (`evals/baseline.json`): `--baseline` compares and
  exits non-zero on drops (>0.05 on any metric; any increase in PII leaks).
  Runs on demand per TESTING.md — every model/chunking change — not per-commit
  CI (needs live Ollama + minutes of judge time).

## Consequences

- **The harness paid for itself on its first run**: it caught the small NER
  model missing the name "Ana Perez" — one leak in 16 documents that no unit
  test had a fixture for. Measured fix: `en_core_web_lg` catches it (0.85);
  the platform default flipped to lg (revising ADR-0007's footprint choice
  with evidence), and `FORGE_PII_SPACY_MODEL=en_core_web_sm` remains the
  lightweight opt-down. Leaks went 1 → 0 in the same session.
- The weak-judge caveat is real: an 8B local judge produces noisy absolute
  scores. Deltas against the committed baseline are the signal; absolute
  numbers are not marketing material.
- The eval stack (ragas + pre-1.0 langchain pin) lives in a separate `evals`
  dependency group — the platform itself never imports it.
- Current corpus (16 docs) makes retrieval trivially perfect (MRR 1.0); the
  metrics become meaningful as the dataset grows and chunking strategies
  compete. Growing the dataset is part of every future RAG milestone.
