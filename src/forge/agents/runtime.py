"""Agent tool-calling runtime (ADR-0019).

The loop: the model is offered ONLY the agent's allow-listed tools; on a tool
call the runtime re-checks the allow-list (a hallucinated/injected call to an
ungranted tool is denied and audited), executes the granted tool, feeds the
result back, and repeats up to max_steps. Every model step and tool call lands
in the same append-only audit trail as gateway requests — the agent's actions
are fully replayable.

The PII boundary holds on the agent path too: messages are scrubbed before each
provider call via the same PIIScrubber as the gateway, so nothing leaves
unscrubbed even though agent calls don't go through gateway.complete().
"""

import json
import time
import uuid
from typing import Any

import litellm

from forge.agents.spec import AgentSpec
from forge.agents.tools import REGISTRY, ToolContext
from forge.audit import AuditBuffer, AuditRecord
from forge.config import Settings
from forge.gateway.router import resolve_model
from forge.pii import PIIScrubber

_DENIED = "Error: this tool is not permitted for this agent."
_REPEATED = (
    "You already called this tool with these exact arguments; its result is "
    "above. Do not call it again — write your final answer now."
)


async def _scrub_args(scrubber: PIIScrubber, args: dict[str, Any]) -> dict[str, Any]:
    """Scrub model-supplied string args for the client-facing trace."""
    out: dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, str):
            value, _ = await scrubber.scrub_text(value)
        out[key] = value
    return out


def _audit(
    audit: AuditBuffer,
    *,
    run_id: uuid.UUID,
    api_key_hash: str,
    agent: str,
    event: str,
    outcome: str,
    started: float,
    tool: str | None = None,
    error_type: str | None = None,
) -> None:
    audit.put(
        AuditRecord(
            request_id=run_id,
            api_key_hash=api_key_hash,
            model_alias=agent,
            upstream_model=None,
            outcome=outcome,
            status_code=200 if outcome == "success" else 500,
            error_type=error_type,
            latency_ms=int((time.perf_counter() - started) * 1000),
            event=event,
            agent=agent,
            tool=tool,
        )
    )


async def _provider_step(
    spec: AgentSpec,
    messages: list[dict[str, Any]],
    tool_schemas: list[dict[str, Any]],
    settings: Settings,
    scrubber: PIIScrubber,
) -> dict[str, Any]:
    upstream = resolve_model(spec.model, settings)
    # outbound PII boundary (ADR-0007) on the agent path too
    scrubbed, _ = await scrubber.scrub_messages(messages)
    params: dict[str, Any] = {}
    if upstream.startswith("ollama/"):
        params["api_base"] = settings.ollama_base_url
    if tool_schemas:
        params["tools"] = tool_schemas
        params["tool_choice"] = "auto"
    response = await litellm.acompletion(model=upstream, messages=scrubbed, **params)
    return response.model_dump()["choices"][0]["message"]


async def run_agent(
    *,
    spec: AgentSpec,
    user_input: str,
    settings: Settings,
    scrubber: PIIScrubber,
    audit: AuditBuffer,
    tool_ctx: ToolContext,
) -> dict[str, Any]:
    run_id = uuid.uuid4()
    started = time.perf_counter()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": spec.system_prompt},
        {"role": "user", "content": user_input},
    ]
    tool_schemas = [REGISTRY[t].schema() for t in spec.tools]
    trace: list[dict[str, Any]] = []
    seen_calls: set[tuple[str, str]] = set()  # break identical-call loops
    force_answer = False  # set after a repeated call: withhold tools next turn

    # One outer guard so NO exit path skips the agent_run audit (provider error,
    # scrubber fault, malformed response, audit hiccup) — audit completeness is a
    # compliance control, not best-effort.
    try:
        for _ in range(spec.max_steps):
            offered = [] if force_answer else tool_schemas
            message = await _provider_step(spec, messages, offered, settings, scrubber)
            _audit(audit, run_id=run_id, api_key_hash=tool_ctx.api_key_hash,
                   agent=spec.name, event="agent_step", outcome="success", started=started)

            tool_calls = message.get("tool_calls")
            if not tool_calls:
                output = message.get("content")
                trace.append({"type": "final", "content": output})
                _audit(audit, run_id=run_id, api_key_hash=tool_ctx.api_key_hash,
                       agent=spec.name, event="agent_run", outcome="success", started=started)
                return {"run_id": str(run_id), "output": output, "trace": trace}

            messages.append(message)
            for call in tool_calls:
                # defensive: a malformed tool_call (missing function/name/bad JSON)
                # must not crash the run — treat it as an error step, not a 500
                function = (call or {}).get("function") or {}
                name = function.get("name") or ""
                try:
                    args = json.loads(function.get("arguments") or "{}")
                    if not isinstance(args, dict):
                        args = {}
                except json.JSONDecodeError:
                    args = {}
                signature = (name, json.dumps(args, sort_keys=True, default=str))

                # authority enforcement — the spec's allow-list is the boundary
                if not name or name not in spec.tools or name not in REGISTRY:
                    result = _DENIED
                    outcome = "denied"
                elif signature in seen_calls:
                    # same tool, same args — don't re-run; nudge to answer, and
                    # withhold tools next turn so the model must produce content
                    result = _REPEATED
                    outcome = "repeated"
                    force_answer = True
                else:
                    seen_calls.add(signature)
                    try:
                        result = await REGISTRY[name].handler(tool_ctx, **args)
                        outcome = "success"
                    except Exception as exc:  # a tool fault is the agent's to handle
                        result = f"Error: {exc}"
                        outcome = "error"
                # the trace is returned to the client — scrub model-supplied args
                # so PII the model placed in arguments doesn't leak via the trace
                trace.append({
                    "type": "tool_call", "tool": name, "outcome": outcome,
                    "args": await _scrub_args(scrubber, args),
                })
                _audit(audit, run_id=run_id, api_key_hash=tool_ctx.api_key_hash,
                       agent=spec.name, event="tool_call", outcome=outcome, started=started,
                       tool=name or None, error_type=None if outcome == "success" else outcome)
                messages.append(
                    {"role": "tool", "tool_call_id": call.get("id"), "content": result}
                )

        # ran out of steps without a final answer (reliability bound)
        _audit(audit, run_id=run_id, api_key_hash=tool_ctx.api_key_hash, agent=spec.name,
               event="agent_run", outcome="error", started=started, error_type="step_limit")
        return {
            "run_id": str(run_id),
            "output": None,
            "error": f"step limit ({spec.max_steps}) reached without a final answer",
            "trace": trace,
        }
    except Exception as exc:
        _audit(audit, run_id=run_id, api_key_hash=tool_ctx.api_key_hash, agent=spec.name,
               event="agent_run", outcome="error", started=started, error_type=type(exc).__name__)
        raise
