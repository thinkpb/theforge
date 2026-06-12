"""Document ingestion and retrieval endpoints (ADR-0012).

Collections are team-scoped via the caller's key: there is no collection
parameter to get wrong — or to attack.
"""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from forge.audit import AuditBuffer, get_audit_buffer
from forge.auth import AuthContext, require_api_key
from forge.config import Settings, get_settings
from forge.pii import PIIScrubber, get_pii_scrubber
from forge.rag.ingest import get_vector_store, ingest_document, search_documents
from forge.rag.parsing import DocumentParseError, UnsupportedDocumentType, parse_document
from forge.rag.store import VectorStore

router = APIRouter()

ADMIN_TEAM = "admin"


class IngestRequest(BaseModel):
    text: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=256)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=50)


def _effective(ctx: AuthContext, scrubber: PIIScrubber) -> tuple[str, PIIScrubber]:
    team = ctx.team or ADMIN_TEAM
    if ctx.pii_opt_out:
        scrubber = PIIScrubber(enabled=False)
    return team, scrubber


@router.post("/v1/documents", status_code=201)
async def ingest(
    body: IngestRequest,
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_api_key),
    audit: AuditBuffer = Depends(get_audit_buffer),
    scrubber: PIIScrubber = Depends(get_pii_scrubber),
    store: VectorStore = Depends(get_vector_store),
) -> dict[str, Any]:
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
    )


@router.post("/v1/documents/upload", status_code=201)
async def upload(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_api_key),
    audit: AuditBuffer = Depends(get_audit_buffer),
    scrubber: PIIScrubber = Depends(get_pii_scrubber),
    store: VectorStore = Depends(get_vector_store),
) -> dict[str, Any]:
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
    )


@router.post("/v1/search")
async def search(
    body: SearchRequest,
    settings: Settings = Depends(get_settings),
    ctx: AuthContext = Depends(require_api_key),
    audit: AuditBuffer = Depends(get_audit_buffer),
    scrubber: PIIScrubber = Depends(get_pii_scrubber),
    store: VectorStore = Depends(get_vector_store),
) -> dict[str, Any]:
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
    )
    return {"object": "list", "data": results}
