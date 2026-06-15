# Testing Strategy

AI systems need two testing strategies running in parallel. The platform's
deterministic parts (routing, auth, rate limits, chunking, retrieval) get
traditional tests. The probabilistic parts (LLM outputs) can't be tested with
`assert response == expected` — they get evals, red teaming, and drift monitoring.

The mental model, carried through every phase:

```
Traditional tests   → Did the system DO the right thing?
AI evals            → Was the OUTPUT any good?
Red teaming         → Can someone make it do the WRONG thing?
Production monitors → Is it STILL good three months later?
```

For a platform aimed at medical records and legal documents, all four layers are
table stakes — red teaming and PII leakage testing especially
([ADR-0005](adr/0005-compliance-first-design.md)).

## Layer 1 — Traditional testing (deterministic components)

**Status: active.** This is the layer that exists today.

- **Unit + integration tests** (pytest) — the current suite in [`tests/`](../tests)
  drives the real ASGI app via httpx `ASGITransport`: auth rejection/acceptance,
  alias validation, health. Everything deterministic (routing logic, key
  management, rate-limit thresholds, future chunking) is tested this way.
- **API contract tests** — Forge is a platform other devs build on; the
  OpenAI-compatible response *shape* is guaranteed even though content varies
  ([ADR-0004](adr/0004-openai-compatible-surface.md)). Contract tests assert shape,
  never content:

  ```python
  def test_completion_response_contract(response):
      for field in ("id", "model", "choices", "usage"):
          assert field in response
      assert response["usage"]["prompt_tokens"] > 0
  ```

- **Load tests** (Locust or k6) — watch p95 latency, error rate, and cost per
  request. Especially important once inline PII scrubbing adds per-request latency:
  the performance budget from ADR-0005 needs numbers, not vibes.

## Layer 2 — AI evals (probabilistic outputs)

**Status: active** — gold dataset + harness in [`evals/`](../evals), ADR-0014.
Retrieval metrics (hit@k, MRR, PII leaks) are deterministic; RAGAS
faithfulness/relevancy run with a local judge on demand. `evals/baseline.json`
is the regression line.

- **Gold-standard dataset** — domain-specific Q&A pairs for the healthcare and
  legal verticals (`expected_topics`, `should_not_contain`, faithfulness flags).
  The most reusable testing asset the project will own; versioned in-repo.
- **RAGAS** for the RAG pipeline — faithfulness, answer relevancy, context
  precision, context recall. These scores are the quality regression line: a
  chunking change that drops faithfulness 0.87 → 0.71 is a broken build even
  though no individual test "failed."
- **DeepEval** for per-PR LLM assertions — threshold-based metrics
  (hallucination < 0.2, faithfulness > 0.85) that run like pytest.
- **LLM-as-judge** for open-ended outputs — a stronger model scores accuracy,
  groundedness, helpfulness. Not for absolute scores; for catching drops between
  versions.

Note the split: *retrieval* is deterministic and belongs in Layer 1
("did the chunk containing the planted fact come back, with score > threshold?");
only the *generated answer* needs evals.

## Layer 3 — Red teaming (adversarial)

**Status: PII leakage suite active (`tests/test_pii.py`); injection and jailbreak
suites land with Phase 2/3.**

- **PII leakage tests** — the highest-stakes suite for Forge. Fixture documents
  containing realistic PII (names, SSNs, DOBs) go through the pipeline; responses
  must keep clinical/legal content and lose identifiers:

  ```python
  assert "Metformin" in response.content       # clinical info preserved
  assert "John Smith" not in response.content   # name scrubbed
  assert "123-45-6789" not in response.content  # SSN scrubbed
  ```

  Runs on every pipeline change, not on a schedule.
- **Prompt injection tests** — *active* for the RAG pipeline (ADR-0018):
  `evals/injection/corpus.jsonl` holds synthetic poisoned documents (instruction
  overrides, forged system roles, hidden HTML comments, fence-break, smuggled-as-
  data), each with a canary the injection tries to emit;
  `evals/redteam_injection.py` measures resistance (defended vs undefended).
  CI asserts the defense is structurally applied (`tests/test_injection.py`); the
  resistance rate is on-demand and model-dependent. Agent-side stakes (tool use
  amplifies blast radius) land in Phase 3.
- **Jailbreak scanning** — [Garak](https://github.com/NVIDIA/garak) against the
  gateway's REST surface, monthly and before each release.

## Layer 4 — Production monitoring (tests that run forever)

**Status: designed in Phase 1 (audit log captures the raw signals), dashboards and
alerting mature through Phases 4–5.**

Four signals, all derivable from the audit trail the gateway already plans to
capture (tokens, cost, latency, outcome per request):

1. **Quality drift** — eval scores over time; alert on >10% faithfulness drop
2. **Cost anomalies** — cost per request and token usage trends
3. **Latency degradation** — p95 per model/endpoint; alert at 2× baseline
4. **User rejection rate** — edits/dismissals of AI output; the quality proxy
   humans can feel

**Shadow deployments** before any model swap: candidate model answers a traffic
sample in parallel, only production's response reaches users, evals compare
offline against promotion thresholds. This is the Phase 4 canary-deployment
milestone's foundation.

## The stack, mapped to phases

| Layer | Tool | When it runs | Lands in |
|---|---|---|---|
| Unit/integration | pytest | every commit (CI) | ✅ now |
| API contracts | pytest snapshot/shape tests | every commit (CI) | Phase 1 |
| PII leakage | custom pytest suite | every pipeline change | ✅ now |
| Load testing | Locust or k6 | weekly + pre-release | Phase 1 (with rate limiting) |
| RAG evals | RAGAS + gold dataset (`evals/`) | every model/chunking change | ✅ now |
| LLM test suite | DeepEval | every PR to main | Phase 2 |
| Prompt injection | custom corpus + red-team eval (`evals/injection/`) | every pipeline change | ✅ now (RAG) |
| Security/red team | Garak + custom | monthly + before release | Phase 3 |
| Production drift | LangFuse dashboards + audit log | continuous | Phase 4–5 |
| Shadow deployments | custom | before any model swap | Phase 4 |

## Conventions

- Deterministic logic gets standard pytest asserts. LLM output quality gets evals —
  never string-equality assertions against generated text.
- Eval scores are regression lines, not absolute targets: track deltas between
  versions, fail CI on significant drops.
- Every fixture containing PII is synthetic. No real personal data in this repo,
  ever — including in test fixtures and eval datasets.
