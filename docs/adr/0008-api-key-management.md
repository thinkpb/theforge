# ADR-0008: Per-team API keys — hashed storage, revoke-not-delete

**Status:** Accepted (2026-06) — supersedes ADR-0003's single-key model

## Context

The master-key-only model (ADR-0003) was a deliberate placeholder: no per-team
attribution, no revocation story, no per-key policy. Cost tracking needs to know
*who* spent; the audit trail needs requests to resolve to an accountable
identity; ADR-0007 deferred per-key PII opt-out until keys existed.

## Decision

- **Key format:** `fsk_` + 32 bytes of URL-safe randomness. The distinctive
  prefix makes keys recognizable to secret scanners and humans in logs.
- **Hash-only storage:** the database stores the SHA-256 hash and a 12-char
  display prefix. The full key exists exactly once — in the creation response.
  A database breach yields no usable credentials.
- **Revoke, never delete:** revocation sets `revoked_at`; key rows are permanent
  so every audit record forever resolves to the key (and team) that made it.
  Deleting a key would orphan its audit history — the opposite of compliance.
- **Master key becomes the admin/bootstrap credential:** it manages keys
  (`POST/GET/DELETE /v1/keys`) and reads the audit trail; team keys get 403
  there. Completions accept either. You need one credential before the first
  key exists — that's the master key's remaining job.
- **Per-key PII opt-out** (`pii_opt_out`) implements the exception ADR-0007
  promised: requests made with such a key skip scrubbing, and the audit row
  records `pii_redactions = NULL` — the opt-out always leaves a trace.
- Auth resolves keys with one indexed Postgres lookup per request.

## Consequences

- Cost attribution per team is now possible — the cost-tracking milestone joins
  `audit_log.api_key_hash` to `api_keys`.
- A per-request DB lookup enters the hot path (~1 ms locally). If it ever
  matters, a short-TTL cache is the fix — but caching revocation is a
  compliance trade-off (a revoked key living until TTL expiry), so it waits
  until measurements justify it.
- Scopes/roles are deliberately absent: two privilege levels (master, team key)
  cover Phase 1. Real RBAC arrives with multi-tenancy in Phase 5.
- Key rotation is manual (create new + revoke old); automated rotation is
  future work.
