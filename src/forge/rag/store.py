"""Qdrant vector store. Collections are team-scoped (ADR-0012): a team key
can only ever read and write its own collection — isolation by construction,
not by query filter.
"""

import re
import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient, models


def collection_for_team(prefix: str, team: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", team)
    return f"{prefix}_{safe}"


class VectorStore:
    def __init__(self, url: str):
        self._client = AsyncQdrantClient(url=url)

    async def close(self) -> None:
        await self._client.close()

    async def ensure_collection(self, name: str, dim: int) -> None:
        if not await self._client.collection_exists(name):
            await self._client.create_collection(
                collection_name=name,
                vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
            )

    async def upsert_chunks(
        self,
        collection: str,
        doc_id: uuid.UUID,
        chunks: list[str],
        vectors: list[list[float]],
        metadata: dict[str, Any],
    ) -> None:
        points = [
            models.PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "text": text,
                    "doc_id": str(doc_id),
                    "chunk_index": index,
                    **metadata,
                },
            )
            for index, (text, vector) in enumerate(zip(chunks, vectors, strict=True))
        ]
        await self._client.upsert(collection_name=collection, points=points)

    async def search(
        self, collection: str, vector: list[float], limit: int
    ) -> list[dict[str, Any]]:
        if not await self._client.collection_exists(collection):
            return []  # team hasn't ingested anything yet — not an error
        result = await self._client.query_points(
            collection_name=collection, query=vector, limit=limit, with_payload=True
        )
        return [
            {"score": point.score, **(point.payload or {})} for point in result.points
        ]
