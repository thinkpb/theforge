"""Ingestion jobs (ADR-0017).

Durable, queryable, team-scoped job records for async document ingestion.
The job row is the source of truth for status; arq/Redis only carries the
work. The row outlives Redis result TTLs and is auditable like everything else.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Uuid, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from forge.db import Base

QUEUED = "queued"
RUNNING = "running"
COMPLETE = "complete"
FAILED = "failed"


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    team: Mapped[str] = mapped_column(index=True)
    api_key_hash: Mapped[str]
    title: Mapped[str | None]
    status: Mapped[str] = mapped_column(default=QUEUED)
    doc_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    chunks: Mapped[int | None]
    pii_redactions: Mapped[int | None]
    error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def public_job(job: IngestionJob) -> dict:
    return {
        "job_id": str(job.id),
        "team": job.team,
        "title": job.title,
        "status": job.status,
        "doc_id": str(job.doc_id) if job.doc_id else None,
        "chunks": job.chunks,
        "pii_redactions": job.pii_redactions,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


async def create_job(
    session_factory: async_sessionmaker,
    *,
    job_id: uuid.UUID,
    team: str,
    api_key_hash: str,
    title: str | None,
) -> None:
    async with session_factory() as session:
        session.add(
            IngestionJob(
                id=job_id, team=team, api_key_hash=api_key_hash, title=title, status=QUEUED
            )
        )
        await session.commit()


async def _update(session_factory: async_sessionmaker, job_id: uuid.UUID, **fields) -> None:
    async with session_factory() as session:
        job = await session.get(IngestionJob, job_id)
        if job is None:  # row should always exist; worker is defensive
            return
        for key, value in fields.items():
            setattr(job, key, value)
        await session.commit()


async def mark_running(session_factory, job_id: uuid.UUID) -> None:
    await _update(session_factory, job_id, status=RUNNING)


async def mark_complete(
    session_factory, job_id: uuid.UUID, *, doc_id, chunks, pii_redactions
) -> None:
    await _update(
        session_factory,
        job_id,
        status=COMPLETE,
        doc_id=doc_id,
        chunks=chunks,
        pii_redactions=pii_redactions,
        error=None,
    )


async def mark_failed(session_factory, job_id: uuid.UUID, error: str) -> None:
    await _update(session_factory, job_id, status=FAILED, error=error[:2000])


async def get_job(session_factory, job_id: uuid.UUID, team: str) -> IngestionJob | None:
    async with session_factory() as session:
        job = await session.scalar(
            select(IngestionJob).where(IngestionJob.id == job_id, IngestionJob.team == team)
        )
        return job
