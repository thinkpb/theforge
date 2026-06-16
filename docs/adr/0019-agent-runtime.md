# ADR-0019: Agent runtime — config-defined agents, allow-listed tool authority

**Status:** Accepted (2026-06)

## Context

Phase 3 adds agents: an LLM that calls tools in a loop. For a compliance
platform the danger is not the loop, it's the *authority* — a tool call is an
action, and ADR-0018 showed a retrieved document can hijack the model. So the
agent runtime's defining question is: what can a (possibly injected) agent
actually *do*, and is every action recorded?

## Decision

- **Agents are config, not code** (`agents/*.yaml`, loaded at startup): name,
  model alias, system prompt, an allow-list of tools, and `max_steps`. Tool
  references are validated against the registry at load time (fail fast).
- **Tool authority is allow-listed and re-checked at call time.** The model is
  offered only the agent's tools; when it emits a call, the runtime re-checks
  `name in spec.tools` before executing. A call to an ungranted tool —
  hallucinated or injected — is **denied without running the handler** and
  audited as `outcome='denied'`. This is the concrete blast-radius control
  ADR-0018 named: even a fully hijacked agent cannot exceed its granted tools.
- **Tools are team-scoped.** Handlers receive a `ToolContext` carrying the
  caller's team; `document_search` can only ever read that team's collection —
  the same isolation as the rest of the platform.
- **The PII boundary holds on the agent path.** Agent calls don't go through
  `gateway.complete()`, so the runtime scrubs messages with the same
  `PIIScrubber` before every provider call, including the forced-answer turn.
  An adversarial review of this milestone found two real gaps, now closed:
  `scrub_messages` skipped the assistant `tool_calls` field, so PII the model
  placed in a tool *argument* re-reached the provider on the next turn — the
  scrubber now scrubs argument JSON too; and the client-facing trace stored raw
  args — those are scrubbed before they're returned.
- **Everything is audited, on every exit path.** Each model step (`agent_step`),
  tool call (`tool_call`, with the tool name and outcome), and run result
  (`agent_run`) lands in the same append-only trail (audit columns `agent`,
  `tool`; migration 0006). An outer guard ensures no exit — provider error,
  scrubber fault, malformed response — skips the `agent_run` record (the review
  found the original only audited `openai.OpenAIError`). Malformed tool calls
  (missing function, bad-JSON arguments) degrade to a denied/error step rather
  than crashing the run.
- **Bounded and loop-broken.** `max_steps` caps the loop; an identical repeated
  tool call is not re-executed and flips the next turn to withhold tools, so the
  model must produce a final answer. (Full reliability — timeouts, retries,
  fallback — is a later milestone.)

## Consequences

- **Local-model reality, measured:** `llama3.2:1b` emits tool calls as plain
  *text*, not structured `tool_calls` — too weak for the protocol. `llama3.1:8b`
  uses structured calls correctly but loops, re-calling the tool instead of
  answering; a direct litellm probe confirmed this is the model, not the
  tool-result plumbing. The loop-break + force-answer controls convert that into
  a correct grounded answer (verified live). Tool-calling needs a tool-capable
  model — the example agent uses the 8B alias.
- Agent runs are synchronous and return the full trace; durable run records and
  streaming are later Phase 3 milestones.
- New tools must be registered in code (the registry) and then granted per agent
  in YAML — two deliberate steps, so adding a capability and granting authority
  are separate decisions.
