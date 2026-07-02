"""Agent endpoints (ADR-0019).

Agents are defined by config (YAML, loaded at startup) and run in the caller's
team — tools are scoped to that team, so an agent can only ever touch its team's
data. Listing and running require a valid key; the run executes the audited
tool-calling loop.
"""

import uuid
from typing import Any

import openai
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from forge.agents.runs import (
    ERROR,
    SUCCESS,
    create_run,
    finish_run,
    get_run,
    list_runs,
    public_run,
    summarize_trace,
)
from forge.agents.runtime import run_agent
from forge.agents.tools import ToolContext
from forge.audit import AuditBuffer, get_audit_buffer
from forge.auth import AuthContext, require_api_key
from forge.config import Settings, get_settings
from forge.pii import PIIScrubber, get_pii_scrubber
from forge.rag.ingest import get_vector_store
from forge.rag.store import VectorStore

router = APIRouter(dependencies=[Depends(require_api_key)])

ADMIN_TEAM = "admin"


class AgentRunRequest(BaseModel):
    input: str = Field(min_length=1)


@router.get("/v1/agents")
async def list_agents(request: Request) -> dict[str, Any]:
    agents = request.app.state.agents
    return {
        "object": "list",
        "data": [
            {"name": s.name, "model": s.model, "tools": s.tools, "max_steps": s.max_steps}
            for s in agents.values()
        ],
    }


@router.post("/v1/agents/{name}/run")
async def run(
    name: str,
    body: AgentRunRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_api_key),
    audit: AuditBuffer = Depends(get_audit_buffer),
    scrubber: PIIScrubber = Depends(get_pii_scrubber),
    store: VectorStore = Depends(get_vector_store),
) -> dict[str, Any]:
    spec = request.app.state.agents.get(name)
    if spec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown agent {name!r}")

    team = ctx.team or ADMIN_TEAM
    if ctx.pii_opt_out:
        scrubber = PIIScrubber(enabled=False)
    tool_ctx = ToolContext(
        team=team,
        settings=settings,
        scrubber=scrubber,
        store=store,
        audit=audit,
        api_key_hash=ctx.key_hash,
    )

    # durable run record (ADR-0022): created running, finished with the
    # metadata-only step summary — shares its id with the audit events
    run_id = uuid.uuid4()
    session_factory = request.app.state.db_session_factory
    await create_run(
        session_factory, run_id=run_id, team=team, api_key_hash=ctx.key_hash, agent=name
    )
    try:
        result = await run_agent(
            spec=spec,
            user_input=body.input,
            settings=settings,
            scrubber=scrubber,
            audit=audit,
            tool_ctx=tool_ctx,
            run_id=run_id,
        )
    except openai.OpenAIError as exc:
        # the run is already audited as agent_run error inside the runtime;
        # record the durable run as errored, then surface 502
        await finish_run(
            session_factory, run_id, status=ERROR, steps=[], error_type=type(exc).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Agent provider error ({type(exc).__name__})",
        ) from exc
    except Exception as exc:
        # invariant #3 (ADR-0022): every run finishes on every exit path. Any
        # other failure — HTTPException (e.g. unknown model), an audit backpressure
        # 503, a scrubber fault — must not leave the row stuck 'running'.
        await finish_run(
            session_factory, run_id, status=ERROR, steps=[], error_type=type(exc).__name__
        )
        raise

    errored = result.get("error") is not None
    await finish_run(
        session_factory,
        run_id,
        status=ERROR if errored else SUCCESS,
        steps=summarize_trace(result["trace"]),
        error_type="step_limit" if errored else None,
    )
    return result


@router.get("/v1/agents/runs")
async def list_agent_runs(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    ctx: AuthContext = Depends(require_api_key),
) -> dict[str, Any]:
    runs = await list_runs(request.app.state.db_session_factory, ctx.team or ADMIN_TEAM, limit)
    return {"object": "list", "data": [public_run(r) for r in runs]}


@router.get("/v1/agents/runs/{run_id}")
async def get_agent_run(
    run_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(require_api_key),
) -> dict[str, Any]:
    run = await get_run(request.app.state.db_session_factory, run_id, ctx.team or ADMIN_TEAM)
    if run is None:  # also covers other teams' runs — 404, no cross-team leak
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown run id")
    return public_run(run)
