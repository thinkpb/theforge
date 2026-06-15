"""Qdrant vector store. Collections are team-scoped (ADR-0012): a team key
can only ever read and write its own collection — isolation by construction,
not by query filter.

Hybrid layout (ADR-0016): every point carries a named dense vector and a named
BM25 sparse vector; hybrid queries fuse both with Reciprocal Rank Fusion
server-side. Dense-only remains available per request.
"""

import re
import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient, models

SEARCH_MODES = {"hybrid", "dense"}


class CollectionSchemaMismatch(Exception):
    """An existing collection predates the current vector schema (ADR-0016).
    Surfaces the re-index debt as an actionable error instead of an opaque 400."""


def collection_for_team(prefix: str, team: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", team)
    return f"{prefix}_{safe}"


class VectorStore:
    def __init__(self, url: str):
        self._client = AsyncQdrantClient(url=url)

    async def close(self) -> None:
        await self._client.close()

    async def ensure_collection(self, name: str, dim: int) -> None:
        if await self._client.collection_exists(name):
            info = await self._client.get_collection(name)
            vectors = info.config.params.vectors
            if not (isinstance(vectors, dict) and "dense" in vectors):
                raise CollectionSchemaMismatch(
                    f"Collection {name!r} predates the hybrid-search schema "
                    "(ADR-0016): it has no named 'dense' vector. Re-ingest its "
                    "documents to rebuild it (the re-index job is pending)."
                )
            return
        await self._client.create_collection(
            collection_name=name,
            vectors_config={
                "dense": models.VectorParams(size=dim, distance=models.Distance.COSINE)
            },
            sparse_vectors_config={
                # IDF lives server-side; the client sends term frequencies
                "bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)
            },
        )

    async def upsert_chunks(
        self,
        collection: str,
        doc_id: uuid.UUID,
        chunks: list[str],
        vectors: list[list[float]],
        sparse_vectors: list[models.SparseVector],
        metadata: dict[str, Any],
    ) -> None:
        points = [
            models.PointStruct(
                # deterministic from (doc_id, chunk_index): a re-run with the same
                # doc_id overwrites these points instead of duplicating them, which
                # is what makes async ingestion safe under arq retries (ADR-0017)
                id=str(uuid.uuid5(doc_id, str(index))),
                vector={"dense": dense, "bm25": sparse},
                payload={
                    "text": text,
                    "doc_id": str(doc_id),
                    "chunk_index": index,
                    **metadata,
                },
            )
            for index, (text, dense, sparse) in enumerate(
                zip(chunks, vectors, sparse_vectors, strict=True)
            )
        ]
        await self._client.upsert(collection_name=collection, points=points)

    async def search(
        self,
        collection: str,
        vector: list[float],
        sparse_vector: models.SparseVector,
        limit: int,
        mode: str = "hybrid",
    ) -> list[dict[str, Any]]:
        if mode not in SEARCH_MODES:
            raise ValueError(f"Unknown search mode {mode!r}. Available: {sorted(SEARCH_MODES)}")
        if not await self._client.collection_exists(collection):
            return []  # team hasn't ingested anything yet — not an error
        if mode == "dense":
            result = await self._client.query_points(
                collection_name=collection,
                query=vector,
                using="dense",
                limit=limit,
                with_payload=True,
            )
        else:
            result = await self._client.query_points(
                collection_name=collection,
                prefetch=[
                    models.Prefetch(query=vector, using="dense", limit=limit * 3),
                    models.Prefetch(query=sparse_vector, using="bm25", limit=limit * 3),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=limit,
                with_payload=True,
            )
        return [{"score": point.score, **(point.payload or {})} for point in result.points]
