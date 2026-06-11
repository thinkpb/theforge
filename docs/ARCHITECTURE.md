# Forge Architecture

Forge is a self-hostable LLM gateway (Phase 1 of a five-phase platform — see
[ROADMAP.md](ROADMAP.md)). Clients speak an OpenAI-compatible API to the gateway;
the gateway authenticates them, resolves a Forge **model alias** to an upstream
provider, and proxies the call via LiteLLM. Everything regulated industries care
about — auth, cost tracking, rate limits, audit, PII handling — attaches at this
choke point.

Compliance-by-default is the core design principle
([ADR-0005](adr/0005-compliance-first-design.md)): audit logging and PII scrubbing
are Phase 1 pipeline concerns that every later component inherits, not features
added at the end.

```
client (any OpenAI SDK)
   │  Authorization: Bearer <forge key>
   ▼
api/          transport layer: routes, pydantic DTOs, auth dependency
   ▼
gateway/      routing layer: alias resolution, provider calls, error translation
   ▼
LiteLLM       provider adapter: OpenAI · Anthropic · Ollama
```

Significant decisions are recorded as ADRs in [adr/](adr/).

## Design patterns in use

### Application factory
[`create_app()`](../src/forge/main.py) builds the FastAPI app instead of exposing a
module-level singleton. **Why:** tests construct fresh app instances after changing
environment, and later milestones (worker processes, embedded test harnesses) can
build differently-configured instances without import-order tricks.

### Dependency injection
Settings and auth enter handlers through FastAPI `Depends`
([`auth.py`](../src/forge/auth.py), [`api/chat.py`](../src/forge/api/chat.py)) —
never as module-global imports inside handler bodies. **Why:** the composition
happens at the edge, so swapping an implementation (master-key auth → Postgres-backed
per-team keys, [ADR-0003](adr/0003-master-key-auth-first.md)) touches one dependency
function and zero handlers. It is also what makes the test fixtures in
[`tests/conftest.py`](../tests/conftest.py) possible.

### Cached settings singleton (12-factor config)
[`config.py`](../src/forge/config.py) defines a pydantic-settings model behind an
`lru_cache`'d accessor: one validated, typed config object sourced from environment
variables (prefix `FORGE_`). **Why:** config errors surface at startup as validation
errors, not at request time as `KeyError`s; tests clear the cache to re-read env.

### Facade over an adapter, with an anti-corruption boundary
[`gateway/router.py`](../src/forge/gateway/router.py) is the only module that talks
to LiteLLM (the provider adapter — [ADR-0002](adr/0002-litellm-as-provider-adapter.md)).
Upstream exceptions are translated into gateway-level HTTP errors at this boundary,
and the upstream model string is replaced with the Forge alias before the response
leaves the gateway. **Why:** provider quirks stop here; nothing above this layer
knows or cares which provider served a request.

### Model-alias registry (indirection)
Clients only ever see Forge aliases from `Settings.model_map`; the alias→provider
mapping is the gateway's private concern ([ADR-0001](adr/0001-model-alias-indirection.md)).
**Why:** this is the load-bearing contract decision — operators can re-route, A/B,
fail over, or migrate providers without any client changing code.

### Layered architecture
`api/` (transport + pydantic DTOs) → `gateway/` (routing logic) → providers, with
`auth` and `config` as cross-cutting concerns injected where needed. **Why:** each
roadmap milestone attaches at a known layer — rate limiting and cost tracking wrap
the gateway layer; new transports (SDK, dashboard) sit beside `api/` without
duplicating routing logic.

### In-process integration tests
[`tests/conftest.py`](../tests/conftest.py) drives the real ASGI app through httpx's
`ASGITransport` — full middleware/auth/validation stack, no live server, no network.
**Why:** tests exercise the same code paths as production requests but run in
milliseconds and need no infrastructure.

## Patterns deliberately deferred

These are roadmap milestones, not oversights:

- **Strategy** — routing and fallback policies (retry on provider error, failover
  chains) will be pluggable strategies on the gateway layer.
- **Middleware / decorator** — audit logging, PII scrubbing (Presidio), rate
  limiting (Redis), and cost tracking (Postgres) will wrap the request path as
  cross-cutting concerns, not be inlined into handlers. Audit + PII are the next
  milestones, per [ADR-0005](adr/0005-compliance-first-design.md).
- **Repository** — per-team API keys, audit records, and cost records get a
  persistence layer behind an interface; until then auth is a single master key
  ([ADR-0003](adr/0003-master-key-auth-first.md)).

## How to read this codebase

Start at [`src/forge/main.py`](../src/forge/main.py) — the app factory wires two
routers. Then trace one request, `POST /v1/chat/completions`:

1. [`api/chat.py`](../src/forge/api/chat.py) — `require_api_key` dependency runs
   first (router-level), then the pydantic DTO validates the body.
2. The handler forwards to [`gateway/router.py`](../src/forge/gateway/router.py)
   `complete()`, which resolves the alias via `resolve_model()` and calls
   `litellm.acompletion`.
3. The response is normalized (upstream model string → Forge alias) and returned;
   upstream failures become `502`, unknown aliases `400`.

Configuration lives in [`config.py`](../src/forge/config.py); copy
[`.env.example`](../.env.example) to `.env` to run locally.
