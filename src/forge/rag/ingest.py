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

import time
import uuid
from typing import Any

from fastapi import Request

from forge.audit import AuditBuffer, AuditRecord
from forge.config import Settings
from forge.pii import PIIScrubber
from forge.rag.chunking import chunk_text
from forge.rag.embeddings import embed_texts
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
) -> AuditRecord:
    return AuditRecord(
        request_id=uuid.uuid4(),
        api_key_hash=api_key_hash,
        model_alias=settings.embedding_model,
        upstream_model=settings.embedding_model,
        outcome=outcome,
        status_code=200,
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
) -> dict[str, Any]:
    started = time.perf_counter()
    doc_id = uuid.uuid4()
    chunks = chunk_text(text, settings.rag_chunk_words, settings.rag_chunk_overlap)

    total_redactions: int | None = None
    scrubbed_chunks: list[str] = []
    for chunk in chunks:
        scrubbed, count = await scrubber.scrub_text(chunk)
        scrubbed_chunks.append(scrubbed)
        if count is not None:
            total_redactions = (total_redactions or 0) + count

    vectors = await embed_texts(scrubbed_chunks, settings)
    collection = collection_for_team(settings.qdrant_collection_prefix, team)
    await store.ensure_collection(collection, settings.embedding_dim)
    await store.upsert_chunks(
        collection,
        doc_id,
        scrubbed_chunks,
        vectors,
        metadata={"title": title} if title else {},
    )
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
) -> list[dict[str, Any]]:
    started = time.perf_counter()
    scrubbed_query, redactions = await scrubber.scrub_text(query)
    (vector,) = await embed_texts([scrubbed_query], settings)
    collection = collection_for_team(settings.qdrant_collection_prefix, team)
    results = await store.search(collection, vector, limit)
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
