# ADR-0004: OpenAI-compatible API surface

**Status:** Accepted (2026-06)

## Context

Teams adopting a gateway already have application code written against some LLM
API — overwhelmingly the OpenAI chat-completions shape, which every major SDK,
framework, and tool speaks. A bespoke Forge API would force every adopter to
rewrite call sites and forgo existing tooling; adoption friction would kill the
project's usefulness regardless of what the platform layer offers.

## Decision

The gateway exposes the OpenAI wire format: `POST /v1/chat/completions` and
`GET /v1/models` (`src/forge/api/chat.py`). Any OpenAI SDK works by pointing its
base URL at the gateway and using a Forge key as the API key. Forge-specific
capabilities (model aliases per ADR-0001, later: cost headers, audit metadata)
extend the format without breaking it.

## Consequences

- Drop-in adoption: changing two client config values (base URL, key) onboards an
  existing app.
- Wire compatibility is now a constraint: request/response shapes must track the
  OpenAI spec, including SSE framing when the streaming milestone lands.
- Forge extensions must be additive (extra fields, headers, or separate endpoints) —
  never repurposing or removing OpenAI-spec fields.
- The DTOs in `api/chat.py` deliberately accept a subset of parameters today;
  growing toward fuller spec coverage is incremental and non-breaking.
