"""Ingestion and search pipelines (ADR-0012).

Order is the design: chunk → SCRUB → embed → store. Scrubbing must precede
embedding because a vector computed from raw text encodes the PII — once it's
in vector space no scrubber can reach it, and nearest-neighbor queries can
partially reconstruct it. The vector store only ever sees scrubbed text and
vectors of scrubbed text.

Search queries go through the same scrubber: against a scrubbed corpus,
identifiers in the query can't match anything anyway, so scrubbing costs no
recall — and keeps the outbound boundary uniform if the embedding model is
ever remote.
"""

import asyncio
import time
import uuid
from typing import Any

from fastapi import Request

from forge.audit import AuditBuffer, AuditRecord
from forge.config import Settings
from forge.pii import PIIScrubber
from forge.rag.chunking import chunk_text
from forge.rag.embeddings import embed_texts
from forge.rag.sparse import sparse_embed
from forge.rag.store import VectorStore, collection_for_team


def get_vector_store(request: Request) -> VectorStore:
    return request.app.state.vector_store


def _record(
    *,
    event: str,
    api_key_hash: str,
    settings: Settings,
    outcome: str,
    started: float,
    pii_redactions: int | None,
    status_code: int = 200,
    error_type: str | None = None,
) -> AuditRecord:
    return AuditRecord(
        request_id=uuid.uuid4(),
        api_key_hash=api_key_hash,
        model_alias=settings.embedding_model,
        upstream_model=settings.embedding_model,
        outcome=outcome,
        status_code=status_code,
        error_type=error_type,
        latency_ms=int((time.perf_counter() - started) * 1000),
        pii_redactions=pii_redactions,
        event=event,
    )


async def ingest_document(
    *,
    text: str,
    title: str | None,
    team: str,
    settings: Settings,
    scrubber: PIIScrubber,
    store: VectorStore,
    audit: AuditBuffer,
    api_key_hash: str,
    chunking: str | None = None,
    doc_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    # async callers pass a stable doc_id (= job id) so an arq retry overwrites
    # the same points instead of duplicating them (ADR-0017)
    doc_id = doc_id or uuid.uuid4()
    try:
        chunks = chunk_text(
            text,
            settings.rag_chunk_words,
            settings.rag_chunk_overlap,
            strategy=chunking or settings.rag_chunk_strategy,
        )

        total_redactions: int | None = None
        scrubbed_chunks: list[str] = []
        for chunk in chunks:
            scrubbed, count = await scrubber.scrub_text(chunk)
            scrubbed_chunks.append(scrubbed)
            if count is not None:
                total_redactions = (total_redactions or 0) + count

        vectors = await embed_texts(scrubbed_chunks, settings)
        # sparse vectors of SCRUBBED text only — a BM25 index is readable (ADR-0016)
        sparse_vectors = await asyncio.to_thread(sparse_embed, scrubbed_chunks)
        collection = collection_for_team(settings.qdrant_collection_prefix, team)
        await store.ensure_collection(collection, settings.embedding_dim)
        await store.upsert_chunks(
            collection,
            doc_id,
            scrubbed_chunks,
            vectors,
            sparse_vectors,
            metadata={"title": title} if title else {},
        )
    except Exception as exc:
        # every ingestion is audited — failures too (parity with the chat path)
        audit.put(
            _record(
                event="ingestion",
                api_key_hash=api_key_hash,
                settings=settings,
                outcome="error",
                started=started,
                pii_redactions=None,
                status_code=500,
                error_type=type(exc).__name__,
            )
        )
        raise
    audit.put(
        _record(
            event="ingestion",
            api_key_hash=api_key_hash,
            settings=settings,
            outcome="success",
            started=started,
            pii_redactions=total_redactions,
        )
    )
    return {
        "doc_id": str(doc_id),
        "collection": collection,
        "chunks": len(scrubbed_chunks),
        "pii_redactions": total_redactions,
    }


async def search_documents(
    *,
    query: str,
    limit: int,
    team: str,
    settings: Settings,
    scrubber: PIIScrubber,
    store: VectorStore,
    audit: AuditBuffer,
    api_key_hash: str,
    mode: str | None = None,
) -> list[dict[str, Any]]:
    started = time.perf_counter()
    scrubbed_query, redactions = await scrubber.scrub_text(query)
    (vector,) = await embed_texts([scrubbed_query], settings)
    (sparse_vector,) = await asyncio.to_thread(sparse_embed, [scrubbed_query])
    collection = collection_for_team(settings.qdrant_collection_prefix, team)
    results = await store.search(
        collection, vector, sparse_vector, limit, mode=mode or settings.rag_search_mode
    )
    audit.put(
        _record(
            event="search",
            api_key_hash=api_key_hash,
            settings=settings,
            outcome="success",
            started=started,
            pii_redactions=redactions,
        )
    )
    return results
