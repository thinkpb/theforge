# ADR-0003: Single master key now, per-team API keys later

**Status:** Superseded by [ADR-0008](0008-api-key-management.md) (2026-06) — the
master key remains as the admin/bootstrap credential

## Context

The roadmap includes full API key management (create, revoke, scope, per-team cost
attribution) backed by Postgres. Building that first would block every other
milestone behind schema design and key lifecycle UX. The gateway still needs auth
from day one — an unauthenticated LLM proxy is a wallet drainer.

## Decision

Ship bearer auth against a single master key from settings
(`src/forge/auth.py`, constant-time comparison via `secrets.compare_digest`).
All authenticated routes depend on `require_api_key`; nothing else in the codebase
knows how a key is validated.

## Consequences

- Safe to defer: because auth is a FastAPI dependency, upgrading to Postgres-backed
  per-team keys changes the internals of `require_api_key` (and its return type —
  likely a key/team record instead of the raw string) without touching any handler.
- Until then, the deployment model is "one key per trusted environment" — fine for
  a single team self-hosting, not for multi-tenant use. The README quickstart
  reflects this.
- Cost attribution per team is impossible until real keys exist; the cost-tracking
  milestone therefore lands alongside or after key management.
