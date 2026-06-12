"""Cost attribution endpoint — aggregates the audit trail per key and team.

No separate cost store: the audit log (ADR-0006) already records cost_usd and
key attribution for every request, so cost tracking is a read model over it.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from forge.audit import AuditLog
from forge.auth import require_master_key
from forge.keys import ApiKey

router = APIRouter(dependencies=[Depends(require_master_key)])


@router.get("/v1/costs")
async def costs(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    stmt = (
        select(
            ApiKey.team,
            ApiKey.name,
            AuditLog.api_key_hash,
            func.count().label("requests"),
            func.coalesce(func.sum(AuditLog.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(AuditLog.cost_usd), 0).label("cost_usd"),
        )
        .select_from(AuditLog)
        .join(ApiKey, ApiKey.key_hash == AuditLog.api_key_hash, isouter=True)
        .where(AuditLog.ts >= since)
        .group_by(ApiKey.team, ApiKey.name, AuditLog.api_key_hash)
    )
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        rows = (await session.execute(stmt)).all()

    by_key = [
        {
            # requests made with the master key have no api_keys row
            "team": row.team or "admin",
            "key_name": row.name or "master",
            "api_key_hash": row.api_key_hash,
            "requests": row.requests,
            "total_tokens": int(row.total_tokens),
            "cost_usd": float(row.cost_usd),
        }
        for row in rows
    ]
    by_team: dict[str, dict[str, Any]] = {}
    for entry in by_key:
        team = by_team.setdefault(
            entry["team"], {"requests": 0, "total_tokens": 0, "cost_usd": 0.0}
        )
        team["requests"] += entry["requests"]
        team["total_tokens"] += entry["total_tokens"]
        team["cost_usd"] += entry["cost_usd"]

    return {
        "since": since.isoformat(),
        "by_team": by_team,
        "by_key": by_key,
        "total_cost_usd": round(sum(e["cost_usd"] for e in by_key), 6),
    }
