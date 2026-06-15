"""arq worker for async document ingestion (ADR-0017).

The worker is a separate process, so it builds its own resources — engine,
vector store, scrubber, and its OWN audit buffer (the gateway's in-process
buffer from ADR-0006 is unreachable across processes). The ingestion path
itself is the exact same ingest_document() the synchronous endpoint calls, so
async and sync ingestion are byte-for-byte identical in scrubbing and auditing.

Run it with:  uv run arq forge.worker.WorkerSettings
"""

import logging
import uuid

from arq.connections import RedisSettings

from forge.audit import AuditBuffer
from forge.config import get_settings
from forge.db import create_engine_and_factory
from forge.jobs import COMPLETE, get_job, mark_complete, mark_failed, mark_running
from forge.pii import PIIScrubber
from forge.rag.ingest import ingest_document
from forge.rag.store import VectorStore

logger = logging.getLogger(__name__)


async def ingest_job(
    ctx,
    *,
    job_id: str,
    text: str,
    title: str | None,
    team: str,
    api_key_hash: str,
    chunking: str | None,
    scrub: bool,
) -> dict:
    jid = uuid.UUID(job_id)
    session_factory = ctx["session_factory"]

    # Idempotency under arq's at-least-once retries (ADR-0017): if a prior run
    # already finished (e.g. the ack was lost), don't re-embed — return the
    # recorded result. The crash-mid-run case is covered separately by the
    # deterministic point IDs derived from this job id, which overwrite rather
    # than duplicate.
    existing = await get_job(session_factory, jid, team)
    if existing is not None and existing.status == COMPLETE:
        return {
            "doc_id": str(existing.doc_id),
            "chunks": existing.chunks,
            "pii_redactions": existing.pii_redactions,
        }

    await mark_running(session_factory, jid)
    try:
        scrubber = ctx["scrubber"] if scrub else PIIScrubber(enabled=False)
        result = await ingest_document(
            text=text,
            title=title,
            team=team,
            settings=ctx["settings"],
            scrubber=scrubber,
            store=ctx["vector_store"],
            audit=ctx["audit_buffer"],
            api_key_hash=api_key_hash,
            chunking=chunking,
            doc_id=jid,  # stable identity → retry overwrites, never duplicates
        )
        await mark_complete(
            session_factory,
            jid,
            doc_id=uuid.UUID(result["doc_id"]),
            chunks=result["chunks"],
            pii_redactions=result["pii_redactions"],
        )
        return result
    except Exception as exc:
        logger.exception("ingestion job %s failed", job_id)
        await mark_failed(session_factory, jid, f"{type(exc).__name__}: {exc}")
        raise


async def on_startup(ctx) -> None:
    settings = get_settings()
    engine, session_factory = create_engine_and_factory(settings.database_url)
    buffer = AuditBuffer(
        session_factory,
        maxsize=settings.audit_queue_size,
        flush_batch=settings.audit_flush_batch,
    )
    buffer.start()
    ctx["settings"] = settings
    ctx["engine"] = engine
    ctx["session_factory"] = session_factory
    ctx["audit_buffer"] = buffer
    ctx["vector_store"] = VectorStore(settings.qdrant_url)
    ctx["scrubber"] = PIIScrubber(
        enabled=settings.pii_scrubbing_enabled,
        allow_list=settings.pii_allow_list,
        entities=settings.pii_entities,
        spacy_model=settings.pii_spacy_model,
    )


async def on_shutdown(ctx) -> None:
    await ctx["audit_buffer"].stop()
    await ctx["vector_store"].close()
    await ctx["engine"].dispose()


class WorkerSettings:
    functions = [ingest_job]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 4  # embedding is the bottleneck; keep concurrency modest
