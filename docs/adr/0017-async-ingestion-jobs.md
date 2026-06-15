# ADR-0017: Async ingestion via arq, durable job records

**Status:** Accepted (2026-06)

## Context

Synchronous ingestion blocks the request for the whole chunk → scrub → embed →
store pipeline; a large document means a long-held HTTP connection, and the
upload path capped at 10 MB rather than wait. Heavy ingestion belongs off the
request path. The roadmap named "Celery + Redis"; the platform is async
end-to-end, so that fit was reconsidered.

## Decision

- **arq, not Celery** (user decision). arq is an async-native Redis queue: the
  worker `await`s the *same* `ingest_document()` the sync endpoint calls — no
  sync/async bridge. Celery is recorded as the considered alternative
  (ubiquitous, but sync-first; bridging it to an async pipeline means a wrapper
  or a second event loop per task). A custom Redis-list worker was also
  considered and rejected — it reimplements retries/visibility for free in arq.
- **Separate worker process** (`forge/worker.py`, `arq forge.worker.WorkerSettings`)
  that builds its own engine, vector store, scrubber, and **its own audit
  buffer** — the gateway's in-process buffer (ADR-0006) cannot cross processes.
  Async and sync ingestion are therefore identical in scrubbing and auditing by
  construction: same function, same buffer type, same Postgres.
- **Durable job records in Postgres** (`ingestion_jobs`, migration 0005), not
  just arq's Redis result TTL: status is queryable, team-scoped, and auditable
  like the rest of the platform. `POST /v1/documents/async` → 202 + job_id;
  `GET /v1/documents/jobs/{id}` → status, 404 across teams (existence doesn't
  leak).
- **The worker is where the re-index job will live.** Building it surfaced the
  re-index debt concretely: a pre-hybrid collection (ADR-0016) made the worker
  fail with an opaque Qdrant 400. `ensure_collection` now detects the schema
  mismatch and raises `CollectionSchemaMismatch` → 409 with an actionable
  message, instead of a confusing 500/400.

## Idempotency under at-least-once delivery

arq is at-least-once: a worker crash or retry can re-run a job. An adversarial
review of this milestone confirmed the first cut was *not* idempotent (it drew a
fresh doc_id and random point IDs each run, so a retry duplicated chunks). The
shipped design closes both windows:

- **Stable identity.** The worker passes the job id as the document's `doc_id`,
  and point IDs are `uuid5(doc_id, chunk_index)` — deterministic. A re-run of
  the same job *overwrites* the same points instead of inserting duplicates.
- **Status guard.** `ingest_job` returns early if the job is already COMPLETE
  (the post-ack-lost retry), avoiding redundant embedding work.
- Together: post-success retry → early return; crash-mid-run retry → overwrite.
  A regression test asserts the Qdrant point count is unchanged across re-runs.

## Consequences

- **Every ingestion is audited, failures included.** `ingest_document` now
  audits an `outcome='error'` ingestion event before re-raising — parity with
  the chat path, and the worker still records the failure on the job row.
- **Intake is bounded on every path.** The async and sync text endpoints apply
  the same size cap as upload (413 before any work/enqueue); an enqueue failure
  marks the job FAILED and returns 503 rather than leaving an orphaned QUEUED
  row behind a fake 202.
- Large *file* uploads still parse synchronously then could enqueue the embed
  work; truly large files need object storage rather than a Redis payload —
  the next rung, noted not built.
- A new deployment unit: the `forge-worker` Deployment (k8s) / `arq` process
  must run alongside the gateway, sharing Redis/Postgres/Qdrant config.
- The synchronous `/v1/documents` and `/v1/documents/upload` endpoints are
  unchanged — async is additive.
