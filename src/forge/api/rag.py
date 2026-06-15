"""Document ingestion and retrieval endpoints (ADR-0012).

Collections are team-scoped via the caller's key: there is no collection
parameter to get wrong — or to attack.
"""

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field

from forge.audit import AuditBuffer, get_audit_buffer
from forge.auth import AuthContext, require_api_key
from forge.config import Settings, get_settings
from forge.jobs import create_job, get_job, mark_failed, public_job
from forge.pii import PIIScrubber, get_pii_scrubber
from forge.rag.chunking import STRATEGIES
from forge.rag.ingest import get_vector_store, ingest_document, search_documents
from forge.rag.parsing import DocumentParseError, UnsupportedDocumentType, parse_document
from forge.rag.store import SEARCH_MODES, VectorStore

router = APIRouter()

ADMIN_TEAM = "admin"


class IngestRequest(BaseModel):
    text: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=256)
    chunking: str | None = None  # default comes from settings (ADR-0015)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=50)
    mode: str | None = None  # "hybrid" (default from settings) or "dense"


def _effective(ctx: AuthContext, scrubber: PIIScrubber) -> tuple[str, PIIScrubber]:
    team = ctx.team or ADMIN_TEAM
    if ctx.pii_opt_out:
        scrubber = PIIScrubber(enabled=False)
    return team, scrubber


def _validate_chunking(chunking: str | None) -> None:
    if chunking is not None and chunking not in STRATEGIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown chunking strategy {chunking!r}. Available: {sorted(STRATEGIES)}",
        )


def _check_size(text: str, settings: Settings) -> None:
    # same cap as file upload — applied to every intake path, sync and async,
    # before any work is enqueued or done
    if len(text.encode("utf-8")) > settings.rag_max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Text exceeds {settings.rag_max_upload_bytes} bytes",
        )


@router.post("/v1/documents", status_code=201)
async def ingest(
    body: IngestRequest,
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_api_key),
    audit: AuditBuffer = Depends(get_audit_buffer),
    scrubber: PIIScrubber = Depends(get_pii_scrubber),
    store: VectorStore = Depends(get_vector_store),
) -> dict[str, Any]:
    _validate_chunking(body.chunking)
    _check_size(body.text, settings)
    team, scrubber = _effective(ctx, scrubber)
    return await ingest_document(
        text=body.text,
        title=body.title,
        team=team,
        settings=settings,
        scrubber=scrubber,
        store=store,
        audit=audit,
        api_key_hash=ctx.key_hash,
        chunking=body.chunking,
    )


@router.post("/v1/documents/upload", status_code=201)
async def upload(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    chunking: str | None = Form(default=None),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_api_key),
    audit: AuditBuffer = Depends(get_audit_buffer),
    scrubber: PIIScrubber = Depends(get_pii_scrubber),
    store: VectorStore = Depends(get_vector_store),
) -> dict[str, Any]:
    _validate_chunking(chunking)
    data = await file.read()
    if len(data) > settings.rag_max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {settings.rag_max_upload_bytes} bytes; "
            "async ingestion for large documents is a later milestone",
        )
    try:
        # parsers are CPU-bound — keep the event loop free
        text = await asyncio.to_thread(
            parse_document, data, file.filename or "", file.content_type
        )
    except UnsupportedDocumentType as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"{exc}. Supported: pdf, docx, html, txt, md",
        ) from exc
    except DocumentParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    if not text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No extractable text in document",
        )
    team, effective_scrubber = _effective(ctx, scrubber)
    return await ingest_document(
        text=text,
        title=title or file.filename,
        team=team,
        settings=settings,
        scrubber=effective_scrubber,
        store=store,
        audit=audit,
        api_key_hash=ctx.key_hash,
        chunking=chunking,
    )


@router.post("/v1/documents/async", status_code=202)
async def ingest_async(
    body: IngestRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_api_key),
) -> dict[str, Any]:
    """Enqueue ingestion for the same scrub→embed→store pipeline, off the
    request path (ADR-0017). Heavy documents no longer block the caller."""
    _validate_chunking(body.chunking)
    _check_size(body.text, settings)
    team = ctx.team or ADMIN_TEAM
    job_id = uuid.uuid4()
    session_factory = request.app.state.db_session_factory
    await create_job(
        session_factory, job_id=job_id, team=team, api_key_hash=ctx.key_hash, title=body.title
    )
    try:
        await request.app.state.arq.enqueue_job(
            "ingest_job",
            job_id=str(job_id),
            text=body.text,
            title=body.title,
            team=team,
            api_key_hash=ctx.key_hash,
            chunking=body.chunking,
            scrub=not ctx.pii_opt_out,
            _job_id=str(job_id),  # idempotent: re-enqueue of the same id is a no-op
        )
    except Exception as exc:
        # don't leave a QUEUED row with no work behind it — mark it failed so
        # the job row stays the honest source of truth, then surface 503
        await mark_failed(session_factory, job_id, f"enqueue failed: {type(exc).__name__}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not enqueue ingestion job; try again",
        ) from exc
    return {"job_id": str(job_id), "status": "queued"}


@router.get("/v1/documents/jobs/{job_id}")
async def job_status(
    job_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(require_api_key),
) -> dict[str, Any]:
    team = ctx.team or ADMIN_TEAM
    job = await get_job(request.app.state.db_session_factory, job_id, team)
    if job is None:  # also covers other teams' jobs — no cross-team visibility
        raise HTTPException(status_code=404, detail="Unknown job id")
    return public_job(job)


@router.post("/v1/search")
async def search(
    body: SearchRequest,
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_api_key),
    audit: AuditBuffer = Depends(get_audit_buffer),
    scrubber: PIIScrubber = Depends(get_pii_scrubber),
    store: VectorStore = Depends(get_vector_store),
) -> dict[str, Any]:
    if body.mode is not None and body.mode not in SEARCH_MODES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown search mode {body.mode!r}. Available: {sorted(SEARCH_MODES)}",
        )
    team, scrubber = _effective(ctx, scrubber)
    results = await search_documents(
        query=body.query,
        limit=body.limit,
        team=team,
        settings=settings,
        scrubber=scrubber,
        store=store,
        audit=audit,
        api_key_hash=ctx.key_hash,
        mode=body.mode,
    )
    return {"object": "list", "data": results}
