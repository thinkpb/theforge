# ADR-0022: Durable agent runs — metadata-only, team-scoped

**Status:** Accepted (2026-06)

## Context

The agent runtime (ADR-0019) audits each step as it happens, but the audit trail
is an append-only event stream, not a queryable per-run view. Operators (and
regulators) need to ask run-level questions: did this run succeed? what did the
agent *do*? which runs failed, and how? That needs a durable, queryable record —
without turning it into a store of conversation content.

## Decision

- **`agent_runs` table** (migration 0007): id, team, api_key_hash, agent, status
  (`running`/`success`/`error`), error_type, num_steps, and a `steps` JSON
  summary. Created `running` at the start of a run, finished at the end — the
  same lifecycle shape as `ingestion_jobs` (ADR-0017).
- **Metadata-only, exactly like the audit log (ADR-0006).** The `steps` summary
  is `[{type, tool, outcome}]` — the sequence of tool calls and their outcomes.
  It never stores the model's output text, the user's prompt, or tool arguments.
  The record proves *what the agent did*, not *what it said*. Content archival,
  if a deployment wants it, is a separate explicit opt-in — the default run
  record is a compliance artifact, not a transcript.
- **Team-scoped.** `GET /v1/agents/runs/{id}` returns 404 for another team's run
  (existence doesn't leak); `GET /v1/agents/runs` lists only the caller's team.
- **One id across records.** The endpoint generates the run id and passes it into
  `run_agent`, so the durable record and the audit events (`agent_run`,
  `agent_step`, `tool_call`) share it and correlate.
- **Finished on every exit path.** Success, step-limit error, provider error, and
  *any other* exception (unknown-model HTTPException, audit backpressure, a
  scrubber fault) all update the row — no run is left stuck `running`. An
  adversarial review of this milestone caught the original endpoint catching only
  `openai.OpenAIError`, which would have orphaned runs in `running` on any other
  failure; the fix is a catch-all that finishes the run and re-raises.
- **DB write failures fail loud, not silent.** If `create_run`/`finish_run` can't
  write, the request errors rather than swallowing it — for a system-of-record,
  an unrecordable run should surface, not disappear. (Deliberately *not* wrapped
  in best-effort try/except, contra one review suggestion.)

## Consequences

- Two records per run by design: the append-only audit events (ADR-0019) and the
  consolidated run row. The run row is the read-model; the audit stream is the
  immutable ledger. They agree because they share the run id and the same
  metadata-only discipline.
- "Audit trails for agents — proving what your AI actually did" is now literal:
  the step summary is a replayable action log a reviewer can read without seeing
  regulated content.
- No output/answer is persisted, so a run record alone can't tell you the final
  answer — that's the deliberate trade-off. Retrieval of the answer is the
  client's responsibility (it's in the run response); durable answer storage is
  future opt-in work.
- Runs are synchronous today; long-running or resumable agents would extend this
  table with checkpoint state.
