# Forge Roadmap

Five build phases. Each phase ≈ 4–6 weeks at 10–20 hrs/week, ends in a deployable
milestone, and produces 2–3 blog posts.

**Design principle:** compliance is not a phase — it's the foundation
([ADR-0005](adr/0005-compliance-first-design.md)). Every component ships with audit
logging and PII scrubbing from day one. Phase 5 packages and proves compliance; it
doesn't introduce it.

## Phase 1 — The Compliance-Core Gateway (Weeks 1–6) ✅

A multi-provider LLM gateway where every request is authenticated, audit-logged,
and PII-scrubbed **by default** — plus routing, cost tracking, and rate limiting.

**Ships:** a running gateway that proxies requests to OpenAI, Anthropic, and a local
Ollama instance, with an immutable audit trail and PII scrubbing in the request
pipeline. Any team can drop it in front of their AI calls and inherit the compliance
posture.

**Skills:** FastAPI (async Python) · LiteLLM · PostgreSQL (audit + cost storage) ·
Microsoft Presidio (PII detection) · Redis (rate limiting) · Docker Compose →
Kubernetes · JWT auth + API key management

**Blog posts:**
- "I surveyed the open-source AI platform landscape — here's the gap nobody is building for" *(launch post)*
- "Why every AI team needs a gateway before they need a model"
- "PII scrubbing in LLM pipelines — where data leaks hide"

### Milestones
- [x] Repo scaffold, FastAPI app, health endpoint, bearer auth
- [x] GitHub Actions CI — pytest + ruff on every commit
- [x] LiteLLM proxy: `/v1/chat/completions` routing to OpenAI / Anthropic / Ollama
      — verified end-to-end against local Ollama (llama3.2:1b)
- [x] **Audit logging on every request** — who, which model, token counts, cost,
      latency, outcome — append-only in Postgres (write-behind buffer, ADR-0006;
      verified live incl. trigger-rejected tampering)
- [x] **PII scrubbing in the request pipeline** (Presidio) — on by default at the
      outbound boundary, opt-out visible in the audit trail (ADR-0007)
- [x] PII leakage test suite — synthetic-PII fixtures asserting on the outbound
      boundary; verified live (zero fixture-PII occurrences in provider logs)
- [x] API key management — per-team keys, hash-only storage, revoke-not-delete,
      per-key PII opt-out (ADR-0008); verified live
- [x] Cost tracking per key/team — /v1/costs aggregates the audit trail (no separate cost store)
- [x] Redis-backed rate limiting — token-aware via debit-after accounting (ADR-0009); rate-limited requests are audited
- [x] Provider fallbacks — per-alias chains on transient errors; audit records the serving provider (ADR-0010)
- [x] Streaming support (SSE) — setup-before-stream, audit-at-stream-end with real usage (ADR-0011); verified live
- [x] Docker Compose deploy → first K8s manifests — non-root image + kustomize
      manifests, verified on a live minikube cluster (migrations, e2e, audit)

## Phase 2 — The RAG Engine, PII-Safe (Weeks 7–12) 🚧

Document ingestion and retrieval pipeline. PDFs, Word docs, web pages in;
semantically searchable chunks out — with PII detected and handled **at ingestion
time**, not query time. Plugs into the gateway.

**Ships:** teams upload documents and query them via API; RAG results get injected
into LLM calls automatically; the vector store never holds unscrubbed PII unless a
tenant explicitly configures it to (and that choice is audited).

**Skills:** Qdrant · nomic-embed-text (local embeddings via Ollama) · document parsing
(PyMuPDF, python-docx, BeautifulSoup) · chunking strategies (fixed, semantic,
hierarchical) · retrieval patterns (similarity, hybrid, reranking) · Celery + Redis

**Blog posts:**
- "Chunking strategies — why naive splitting kills retrieval quality"
- "Hybrid search: combining BM25 and vector search for better RAG"
- "PII-safe document ingestion — the pipeline no one talks about building"

### Milestones
- [x] Qdrant + local embeddings foundation — PII-safe ingestion (scrub-before-embed),
      team-scoped collections, `/v1/documents` + `/v1/search`, all audited
      (ADR-0012); verified live with nomic-embed-text
- [x] Document parsers: PDF (PyMuPDF), DOCX (python-docx), HTML (BeautifulSoup)
      + `/v1/documents/upload` — same scrub-before-embed pipeline; verified live
      with a real PDF
- [ ] Chunking strategies: sentence-aware + hierarchical, with a comparison harness
- [x] RAG injection into `/v1/chat/completions` — additive `rag` request field,
      `forge_rag` sources in the response, retrieval audited (ADR-0013)
- [ ] Hybrid search: BM25 + vector with reranking
- [ ] Async ingestion jobs for large documents (queue + workers)
- [ ] Gold eval dataset (synthetic healthcare/legal Q&A) + RAGAS regression pipeline
- [ ] Prompt-injection fixture corpus for document pipelines (TESTING.md Layer 3)

## Phase 3 — The Agent Runtime, Fully Audited (Weeks 13–20)

A runtime where teams define, deploy, and run agents with tool use — self-hostable
and extensible. Every agent step, tool call, and decision lands in the same audit
trail as gateway requests.

**Ships:** a YAML-defined agent that can use tools (search, code execution, document
retrieval, API calls). Developers deploy agents via config, not code, and get a
complete replayable trace of everything the agent did.

**Skills:** LangGraph · MCP server implementation · tool/function calling patterns ·
agent state management + persistence · streaming (SSE/WebSockets) · A2A patterns

**Blog posts:**
- "Building an MCP server from scratch — what the spec actually means"
- "Audit trails for agents — proving what your AI actually did"
- "Agent reliability at scale — retries, fallbacks, and observability"

## Phase 4 — MLOps Pipeline (Weeks 21–28)

Fine-tuning workflow and model registry. Submit fine-tuning jobs, track experiments,
version models, deploy them behind the gateway. Training data goes through the same
PII pipeline; model promotions are audited like everything else.

**Ships:** a UI where teams submit fine-tuning jobs, track runs, promote models to
production, and A/B test them — wired into the gateway for seamless routing.

**Skills:** MLflow · Airflow/Prefect · LoRA/QLoRA fine-tuning (Unsloth) · custom eval
harness · GPU job scheduling (K8s + GPU operator) · canary deployments for models

**Blog posts:**
- "CI/CD for models is not CI/CD for code — here's what's different"
- "Fine-tuning vs RAG — a decision framework with real numbers"
- "Building a model registry that ops teams actually trust"

## Phase 5 — Compliance Packaging + Vertical Layer (Weeks 29–36)

Audit logging and PII scrubbing already exist everywhere — this phase **packages and
proves** the compliance posture: policy as code, tenant isolation, data residency
controls, HIPAA/SOC2 mapping docs, and the one-command deploy.

**Ships:** a one-command Helm deploy that gives a law firm or health clinic a fully
compliant, isolated AI platform with zero data leaving their infrastructure — plus
the documentation that maps Forge controls to HIPAA/SOC2 requirements.

**Skills:** OpenTelemetry · policy as code (OPA) · HIPAA/SOC2 deployment patterns ·
tenant isolation at the infrastructure level · Helm charts

**Blog posts:**
- "What HIPAA actually requires from an AI system — a technical breakdown"
- "Shipping a SOC2-ready AI platform on Kubernetes"
- "Tenant isolation for AI workloads — beyond namespaces"
