# ADR-0020: Tool library — authority classes and safe evaluation

**Status:** Accepted (2026-06)

## Context

The agent runtime (ADR-0019) enforces a per-agent tool allow-list, but with a
single tool (`document_search`) the control had little to constrain. A useful
runtime needs a library — and each tool added is a new capability an agent, or
an injected instruction, could try to use. The library's design has to make
least privilege concrete and keep individual tools safe.

## Decision

- **Tools fall into authority classes; grant the narrowest that does the job:**
  - *pure* — no data or I/O (e.g. `calculator`). An agent doing arithmetic needs
    no document access, and shouldn't have it.
  - *team-read* — reads only the caller's team data (`document_search`,
    `list_documents`), never another team's.
  - *(future)* *egress* / *write* — tools that leave the operator's boundary or
    mutate state will carry stricter controls (allow-lists, confirmation) when
    they're added.
- **`calculator` uses no `eval`.** It parses the expression to an AST and walks
  only arithmetic nodes — names, calls, attribute access, comprehensions all
  raise. Exponents are bounded so `2 ** 10**9` can't DoS the worker. A pure tool
  must be pure *and* safe.
- **`list_documents`** is a distinct authority from search: an agent can be
  allowed to enumerate titles without being able to pull passage content, or
  vice-versa. Both are team-scoped by `ToolContext`.
- Every tool call remains audited (`tool_call`, ADR-0019) regardless of class.

## Consequences

- The allow-list now has teeth: the example `analyst` agent is granted all three;
  a narrower agent gets only what it needs. This is the demonstrable least-
  privilege story the injection→tool-abuse red-team milestone will exercise.
- Adding a capability (register a tool) and granting authority (list it in an
  agent's YAML) stay two separate steps — a capability nobody is granted is
  inert.
- Egress tools (HTTP fetch, etc.) are deliberately not in this milestone: they
  bring SSRF and exfiltration surface that deserves its own design pass rather
  than a quick add.
