"""Audit trail: metadata-only, append-only, write-behind (ADR-0006).

Records that a request happened and how — never the prompt or response text;
storing content would make the audit log itself a PII liability. Append-only is
enforced by a Postgres trigger, not app discipline. Writes go through a bounded
in-memory queue flushed by a background worker: the request path never blocks on
Postgres, and a full queue surfaces as backpressure (503) rather than silent
audit gaps.
"""

import asyncio
import hashlib
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime

from fastapi import Request
from sqlalchemy import BigInteger, DateTime, Index, Numeric, Uuid, func
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logger = logging.getLogger(__name__)

_FLUSH_RETRY_SECONDS = 1.0
_DRAIN_TIMEOUT_SECONDS = 5.0


class Base(DeclarativeBase):
    pass


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_ts", "ts"),
        Index("ix_audit_log_api_key_hash", "api_key_hash"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    api_key_hash: Mapped[str]
    model_alias: Mapped[str]
    upstream_model: Mapped[str | None]
    outcome: Mapped[str]  # 'success' | 'upstream_error' | 'rejected'
    status_code: Mapped[int]
    error_type: Mapped[str | None]
    prompt_tokens: Mapped[int | None]
    completion_tokens: Mapped[int | None]
    total_tokens: Mapped[int | None]
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int]
    # None = scrubbing disabled; 0 = ran, nothing found (ADR-0007)
    pii_redactions: Mapped[int | None]


# Shared by the Alembic migration and the test fixtures so the enforced SQL is
# identical everywhere. asyncpg can't run multi-statement strings, hence a list.
APPEND_ONLY_DDL = [
    """
    CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'audit_log is append-only';
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER audit_log_no_update_delete
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_append_only()
    """,
]


def key_fingerprint(api_key: str) -> str:
    """Identify which key made a request without the log storing the credential."""
    return hashlib.sha256(api_key.encode()).hexdigest()


@dataclass(frozen=True)
class AuditRecord:
    request_id: uuid.UUID
    api_key_hash: str
    model_alias: str
    upstream_model: str | None
    outcome: str
    status_code: int
    latency_ms: int
    error_type: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    pii_redactions: int | None = None


class AuditBufferFull(Exception):
    """Queue is at capacity — the caller must reject the request (backpressure)."""


class AuditBuffer:
    """Bounded write-behind queue with a batching flush worker.

    A failed flush keeps its batch and retries forever — events are only dropped
    if the process dies (the crash-loss window ADR-0006 documents).
    """

    def __init__(self, session_factory: async_sessionmaker, maxsize: int, flush_batch: int):
        self._session_factory = session_factory
        self._queue: asyncio.Queue[AuditRecord] = asyncio.Queue(maxsize=maxsize)
        self._flush_batch = flush_batch
        self._task: asyncio.Task | None = None

    def put(self, record: AuditRecord) -> None:
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            raise AuditBufferFull from None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="audit-flush-worker")

    async def stop(self) -> None:
        try:
            await self.drain()
        except TimeoutError:
            logger.warning(
                "audit buffer did not drain within %.1fs; %d events may be lost",
                _DRAIN_TIMEOUT_SECONDS,
                self._queue.qsize(),
            )
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def drain(self) -> None:
        """Wait until every enqueued event has been flushed to Postgres."""
        async with asyncio.timeout(_DRAIN_TIMEOUT_SECONDS):
            await self._queue.join()

    def cancel(self) -> None:
        """Kill the flush worker without draining — simulates a stuck/down sink."""
        if self._task is not None:
            self._task.cancel()

    async def _run(self) -> None:
        while True:
            batch = [await self._queue.get()]
            while len(batch) < self._flush_batch:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            await self._flush(batch)
            for _ in batch:
                self._queue.task_done()

    async def _flush(self, batch: list[AuditRecord]) -> None:
        while True:
            try:
                async with self._session_factory() as session:
                    session.add_all(AuditLog(**asdict(r)) for r in batch)
                    await session.commit()
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("audit flush failed; retrying %d events", len(batch))
                await asyncio.sleep(_FLUSH_RETRY_SECONDS)


def get_audit_buffer(request: Request) -> AuditBuffer:
    return request.app.state.audit_buffer
