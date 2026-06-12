"""BM25 sparse embeddings for hybrid search (ADR-0016).

fastembed's Qdrant/bm25 produces term-frequency sparse vectors; Qdrant applies
the IDF half server-side (Modifier.IDF on the collection). Encoding is local,
CPU-bound, and deterministic — it runs in a worker thread.

Compliance note: unlike dense vectors, sparse vectors are *readable* — the
indices map to tokens and the values to weights. Scrub-before-embed
(ADR-0012) is what makes a BM25 index safe to hold at all.
"""

from functools import lru_cache

from fastembed import SparseTextEmbedding
from qdrant_client import models


@lru_cache
def _model() -> SparseTextEmbedding:
    return SparseTextEmbedding("Qdrant/bm25")


def sparse_embed(texts: list[str]) -> list[models.SparseVector]:
    return [
        models.SparseVector(indices=e.indices.tolist(), values=e.values.tolist())
        for e in _model().embed(texts)
    ]
