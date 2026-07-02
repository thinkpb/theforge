"""Durable agent run records (ADR-0022).

A queryable, team-scoped record of every agent run — its status, the sequence of
steps and tool outcomes, step count, error type, and timing. Like the audit log
(ADR-0006) it is METADATA-ONLY: it records what the agent *did* (which tools, in
what order, with what outcome), never what it *said* (no output text, no prompts,
no tool arguments). Content archival, if a deployment wants it, is a separate
opt-in — the run record stays a compliance artifact, not a conversation store.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from forge.db import Base

RUNNING = "running"
SUCCESS = "success"
ERROR = "error"


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    team: Mapped[str] = mapped_column(index=True)
    api_key_hash: Mapped[str]
    agent: Mapped[str]
    status: Mapped[str] = mapped_column(default=RUNNING)
    error_type: Mapped[str | None]
    num_steps: Mapped[int] = mapped_column(default=0)
    # metadata-only step summary: [{"type","tool","outcome"}] — no content/args
    steps: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def summarize_trace(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project the runtime trace down to metadata only — drop content and args."""
    return [
        {"type": s.get("type"), "tool": s.get("tool"), "outcome": s.get("outcome")}
        for s in trace
    ]


def public_run(run: AgentRun) -> dict[str, Any]:
    return {
        "run_id": str(run.id),
        "team": run.team,
        "agent": run.agent,
        "status": run.status,
        "error_type": run.error_type,
        "num_steps": run.num_steps,
        "steps": run.steps,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
    }


async def create_run(
    session_factory: async_sessionmaker,
    *,
    run_id: uuid.UUID,
    team: str,
    api_key_hash: str,
    agent: str,
) -> None:
    async with session_factory() as session:
        session.add(
            AgentRun(
                id=run_id, team=team, api_key_hash=api_key_hash, agent=agent, status=RUNNING
            )
        )
        await session.commit()


async def finish_run(
    session_factory: async_sessionmaker,
    run_id: uuid.UUID,
    *,
    status: str,
    steps: list[dict[str, Any]],
    error_type: str | None = None,
) -> None:
    async with session_factory() as session:
        run = await session.get(AgentRun, run_id)
        if run is None:
            return
        run.status = status
        run.steps = steps
        run.num_steps = len(steps)
        run.error_type = error_type
        await session.commit()


async def get_run(session_factory, run_id: uuid.UUID, team: str) -> AgentRun | None:
    async with session_factory() as session:
        return await session.scalar(
            select(AgentRun).where(AgentRun.id == run_id, AgentRun.team == team)
        )


async def list_runs(session_factory, team: str, limit: int) -> list[AgentRun]:
    async with session_factory() as session:
        rows = await session.scalars(
            select(AgentRun)
            .where(AgentRun.team == team)
            .order_by(AgentRun.created_at.desc())
            .limit(limit)
        )
        return list(rows)
