# ADR-0006: Metadata-only audit log with buffered write-behind

**Status:** Accepted (2026-06)

## Context

ADR-0005 commits Forge to auditing every request from Phase 1. Three design
questions follow. *What is recorded?* Storing prompts/responses would make the
audit log itself a PII store — the opposite of its purpose. *How is immutability
guaranteed?* App-level discipline ("we never call UPDATE") convinces no compliance
reviewer. *What happens when the audit sink is down?* Fail-open creates silent
audit gaps; fail-closed (synchronous insert per request) couples availability and
adds per-request latency to a hot path.

## Decision

- **Metadata only.** `audit_log` records who (SHA-256 fingerprint of the API key —
  never the credential), what (model alias and upstream model), and how (outcome,
  status, error type, token counts, cost, latency). Never message content.
- **Append-only enforced by Postgres.** A trigger raises on any UPDATE or DELETE.
  The app's own credentials cannot rewrite history.
- **Buffered write-behind.** Requests enqueue audit events on a **bounded**
  in-process `asyncio.Queue` (non-blocking); a background worker flushes batches
  and retries failed batches indefinitely. If the queue fills (sustained Postgres
  outage), new requests are rejected with 503 — backpressure is the backstop that
  keeps "every request is audited" true.
- The audit hook lives in the gateway layer (`gateway/router.py`), the choke point
  where alias, upstream, tokens, and cost are all known — every future surface
  (SDK, agents, RAG) inherits auditing by flowing through it.

## Consequences

- Request latency is unaffected by Postgres in normal operation; short DB blips
  are absorbed by the queue.
- **Known, bounded gap:** events still in the queue when the process crashes are
  lost. This window is documented rather than hidden; the durable upgrade (e.g. a
  Redis-backed queue) is future work if the deployment posture requires it.
- Sustained sink outage degrades availability (503s) by design — operators see it
  immediately instead of discovering missing audit rows later.
- Full immutability against a malicious DB owner (DROP TABLE) is out of scope at
  the app layer; infrastructure-level controls (WORM storage, replication) are
  Phase 5 territory.
- The schema is migration 0001 (Alembic); the trigger DDL is shared between the
  migration and test fixtures so the enforced SQL is identical everywhere.
