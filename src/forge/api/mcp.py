"""MCP endpoint (ADR-0021) — JSON-RPC 2.0 over HTTP POST at /mcp.

Authenticated with the same Forge bearer key as everything else; the team comes
from the key, so MCP tool calls are team-scoped and audited like agent calls.
Supports single messages and JSON-RPC batches.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from forge.agents.tools import ToolContext
from forge.audit import AuditBuffer, get_audit_buffer
from forge.auth import AuthContext, require_api_key
from forge.config import Settings, get_settings
from forge.mcp import PARSE_ERROR, dispatch, error
from forge.pii import PIIScrubber, get_pii_scrubber
from forge.rag.ingest import get_vector_store
from forge.rag.store import VectorStore

router = APIRouter()

ADMIN_TEAM = "admin"


@router.post("/mcp")
async def mcp_endpoint(
    request: Request,
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_api_key),
    audit: AuditBuffer = Depends(get_audit_buffer),
    scrubber: PIIScrubber = Depends(get_pii_scrubber),
    store: VectorStore = Depends(get_vector_store),
) -> Response:
    try:
        payload: Any = await request.json()
    except Exception:
        return JSONResponse(error(None, PARSE_ERROR, "parse error"))

    if ctx.pii_opt_out:
        scrubber = PIIScrubber(enabled=False)
    tool_ctx = ToolContext(
        team=ctx.team or ADMIN_TEAM,
        settings=settings,
        scrubber=scrubber,
        store=store,
        audit=audit,
        api_key_hash=ctx.key_hash,
    )

    if isinstance(payload, list):  # JSON-RPC batch
        responses = [r for r in (await _batch(payload, tool_ctx, audit)) if r is not None]
        return JSONResponse(responses) if responses else Response(status_code=202)

    response = await dispatch(payload, tool_ctx, audit)
    if response is None:  # a notification — no body
        return Response(status_code=202)
    return JSONResponse(response)


async def _batch(items: list, tool_ctx: ToolContext, audit: AuditBuffer) -> list:
    return [await dispatch(item, tool_ctx, audit) for item in items]
