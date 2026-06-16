"""Agent endpoints (ADR-0019).

Agents are defined by config (YAML, loaded at startup) and run in the caller's
team — tools are scoped to that team, so an agent can only ever touch its team's
data. Listing and running require a valid key; the run executes the audited
tool-calling loop.
"""

from typing import Any

import openai
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

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
    try:
        return await run_agent(
            spec=spec,
            user_input=body.input,
            settings=settings,
            scrubber=scrubber,
            audit=audit,
            tool_ctx=tool_ctx,
        )
    except openai.OpenAIError as exc:
        # the run is already audited as agent_run error inside the runtime;
        # surface the upstream failure honestly rather than as a 500
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Agent provider error ({type(exc).__name__})",
        ) from exc
