"""Audit trail read endpoint — operational visibility into the append-only log."""

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from forge.audit import AuditLog
from forge.auth import require_api_key

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/v1/audit")
async def list_audit(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        rows = (
            await session.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))
        ).all()
    return {
        "object": "list",
        "data": [
            {
                "id": row.id,
                "request_id": str(row.request_id),
                "ts": row.ts.isoformat(),
                "api_key_hash": row.api_key_hash,
                "model_alias": row.model_alias,
                "upstream_model": row.upstream_model,
                "outcome": row.outcome,
                "status_code": row.status_code,
                "error_type": row.error_type,
                "prompt_tokens": row.prompt_tokens,
                "completion_tokens": row.completion_tokens,
                "total_tokens": row.total_tokens,
                "cost_usd": float(row.cost_usd) if row.cost_usd is not None else None,
                "latency_ms": row.latency_ms,
            }
            for row in rows
        ],
    }
