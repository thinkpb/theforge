# Forge

Self-hostable AI infrastructure platform for regulated industries (healthcare, legal,
fintech). **Compliance-by-default is the core design principle** (docs/adr/0005):
every request audit-logged, PII scrubbing in the pipeline, from Phase 1 — not bolted
on later. Built in five phases — see docs/ROADMAP.md for the plan and current
milestone checklist. Currently in **Phase 1: the Compliance-Core Gateway**.
Positioning and competitive landscape: docs/POSITIONING.md.

## Commands

```bash
uv sync                                  # install deps (Python pinned via .python-version)
uv run pytest                            # run tests
uv run ruff check src tests             # lint
uv run uvicorn forge.main:app --reload  # run the gateway on :8000
docker compose up -d                     # Postgres + Redis
```

## Layout

- `src/forge/main.py` — FastAPI app factory
- `src/forge/config.py` — pydantic-settings, env prefix `FORGE_`, model alias map
- `src/forge/auth.py` — bearer auth (master key for now; per-team keys planned)
- `src/forge/api/` — HTTP routes (OpenAI-compatible: `/v1/chat/completions`, `/v1/models`)
- `src/forge/gateway/` — provider routing via LiteLLM
- `tests/` — pytest, async mode auto, httpx ASGITransport (no live server needed)

## Conventions

- Async Python throughout; FastAPI dependencies for settings/auth injection.
- Clients see Forge model **aliases** (from `Settings.model_map`), never upstream
  provider model strings — routing is the gateway's concern.
- Settings are cached (`get_settings` is `lru_cache`d); tests must call
  `get_settings.cache_clear()` when changing env (conftest handles this).
- Each roadmap milestone should land with tests and update the checklist in
  docs/ROADMAP.md.
- Every new component has two non-negotiable acceptance criteria: it emits audit
  events, and it respects the PII boundary (docs/adr/0005).
- Testing follows the four-layer model in docs/TESTING.md: deterministic logic gets
  standard pytest asserts; LLM output quality gets evals (RAGAS/DeepEval) — never
  string-equality assertions against generated text.
- Test fixtures and eval datasets use synthetic PII only — no real personal data in
  the repo, ever.
- Significant design decisions get an ADR in docs/adr/ (next sequential number,
  Status/Context/Decision/Consequences format); update docs/ARCHITECTURE.md when
  patterns change.
- This project doubles as a blog-driven portfolio: when a design decision is
  non-obvious (chunking strategy, rate-limit algorithm, etc.), note the rationale in
  docs/ — it becomes blog material.
