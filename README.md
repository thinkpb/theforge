# Forge

[![CI](https://github.com/thinkpb/theforge/actions/workflows/ci.yml/badge.svg)](https://github.com/thinkpb/theforge/actions/workflows/ci.yml)

**The open-source AI infrastructure layer for regulated industries.**

> HIPAA-ready, SOC2-aligned, self-hostable AI platform with built-in audit trails,
> PII scrubbing, and MLOps — deploy in one Helm chart.

Forge is for teams building AI products in healthcare, legal, and fintech — where
compliance, data isolation, and auditability aren't optional. Compliance isn't a
feature Forge added; it's the design principle everything else is built on
([why](docs/adr/0005-compliance-first-design.md)).

## Why Forge

Plenty of open-source projects do one layer well — gateways, RAG, agents, chat UIs
([where Forge fits](docs/POSITIONING.md)). None combine all five layers, and none
are designed for regulated industries. Forge is one self-hostable platform where:

- **Every request is audit-logged by default** — append-only trail of who called
  which model, tokens, cost, and outcome
- **PII scrubbing is in the request pipeline** — on by default, not an optional add-on
- **LLM Gateway** — multi-provider routing (OpenAI, Anthropic, local Ollama) with
  auth, cost tracking, rate limiting, and fallbacks
- **RAG Engine** — document ingestion with PII-safe ingestion and hybrid retrieval
- **Agent Runtime** — YAML-defined agents with tool use, every step audited
- **MLOps Pipeline** — fine-tuning jobs, model registry, canary deploys behind the gateway
- **Compliance packaging** — HIPAA/SOC2 control mapping, tenant isolation,
  data residency, one-command Helm deploy

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    FORGE PLATFORM                    │
│                                                      │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────┐  │
│  │  Dashboard  │  │   Dev SDK    │  │  REST API  │  │
│  │  (React)    │  │  (Python/TS) │  │  (FastAPI) │  │
│  └──────┬──────┘  └──────┬───────┘  └─────┬──────┘  │
│         └────────────────┼────────────────┘         │
│                          ▼                          │
│         ┌────────────────────────────────┐          │
│         │       LLM Gateway Layer        │          │
│         │  routing · auth · cost limits  │          │
│         │  fallbacks · rate limiting     │          │
│         └────────────────┬───────────────┘          │
│                          │                          │
│    ┌─────────────────────┼──────────────────┐       │
│    ▼                     ▼                  ▼       │
│  ┌──────────┐    ┌──────────────┐    ┌───────────┐  │
│  │  RAG     │    │    Agent     │    │  MLOps    │  │
│  │  Engine  │    │   Runtime    │    │ Pipeline  │  │
│  └──────────┘    └──────────────┘    └───────────┘  │
│                          │                          │
│         ┌────────────────┼────────────────┐         │
│         ▼                ▼                ▼         │
│  ┌─────────────────────────────────────────────┐    │
│  │           Observability + Compliance        │    │
│  │    traces · costs · evals · audit logs      │    │
│  │    PII scrubbing · data residency           │    │
│  └─────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────┘
```

## Status

🚧 **Phase 1 — The Compliance-Core Gateway** (in progress)

See [docs/ROADMAP.md](docs/ROADMAP.md) for the full five-phase build plan,
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design patterns and decision
records ([docs/adr/](docs/adr/)), and [docs/TESTING.md](docs/TESTING.md) for the
four-layer testing strategy.

## Quickstart

Zero API keys needed — the local Ollama model is the fastest way to try Forge:

```bash
# Start infrastructure (Postgres, Redis, Ollama)
docker compose up -d
docker compose exec ollama ollama pull llama3.2:1b   # one-time, ~1.3 GB

# Install dependencies and run the gateway
uv sync
uv run uvicorn forge.main:app --reload

# Chat through the gateway — fully local, no data leaves your machine
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer change-me" \
  -H "Content-Type: application/json" \
  -d '{"model": "llama3.2", "messages": [{"role": "user", "content": "Say hello from Forge"}]}'
```

To route to hosted providers, copy `.env.example` to `.env`, add your
`OPENAI_API_KEY` / `ANTHROPIC_API_KEY`, and set a real `FORGE_MASTER_KEY`.
Already running Ollama natively (recommended on macOS — Docker can't use the
GPU there)? Skip the compose Ollama service; the gateway talks to
`localhost:11434` either way.

## Tech Stack

| Component       | Choice              |
|-----------------|---------------------|
| Gateway         | FastAPI + LiteLLM   |
| Storage         | PostgreSQL          |
| Rate limiting   | Redis               |
| Vector DB       | Qdrant (Phase 2)    |
| Agent framework | LangGraph (Phase 3) |
| MLOps           | MLflow + Airflow (Phase 4) |
| PII scrubbing   | Presidio (Phase 1)  |
| Policy as code  | OPA (Phase 5)       |
| Deploy          | Docker Compose → Kubernetes + Helm |

## License

Apache-2.0
