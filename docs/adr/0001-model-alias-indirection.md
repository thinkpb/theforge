# ADR-0001: Clients see model aliases, never upstream model strings

**Status:** Accepted (2026-06)

## Context

A gateway client has to name a model somehow. The obvious choice is to pass through
upstream model strings (`openai/gpt-4o`, `anthropic/claude-fable-5`) — zero mapping
code, and clients can use any model the provider offers. But then every client
hard-codes provider routing into its own codebase, and the gateway can't change
where a request goes without breaking callers. For Forge's target users (regulated
industries), re-routing is a core requirement: migrating off a provider for data
residency reasons, failing over during an outage, or canary-testing a fine-tuned
replacement must not require client deployments.

## Decision

The gateway's API accepts only **Forge aliases**, defined in `Settings.model_map`
(`src/forge/config.py`). The alias→provider mapping is the gateway's private
concern. Responses report the alias, not the upstream model string — `complete()`
in `src/forge/gateway/router.py` rewrites the `model` field before returning.
Unknown aliases are rejected with `400` and the list of valid aliases.

## Consequences

- Operators re-route, A/B test, fail over, or migrate providers by editing config;
  no client changes.
- The deferred routing-strategy and canary-deployment milestones (Phases 1 and 4)
  have a natural attachment point: an alias can map to a policy, not just a string.
- Cost: new upstream models must be registered before use — deliberate friction
  that doubles as an allowlist, which compliance deployments want anyway.
- Leak risk: response payloads from providers may embed model names in other fields;
  the audit/scrubbing milestones must keep this boundary tight.
