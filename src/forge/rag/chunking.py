"""Chunking strategies. Phase 2 starts with fixed-size word windows with
overlap; sentence-aware and hierarchical strategies are later milestones and
will slot in behind the same signature (strategy pattern per ARCHITECTURE.md).
"""


def chunk_text(text: str, max_words: int, overlap: int) -> list[str]:
    """Fixed-size word windows. Overlap keeps facts that straddle a boundary
    retrievable from at least one chunk."""
    if max_words <= 0:
        raise ValueError("max_words must be positive")
    if not 0 <= overlap < max_words:
        raise ValueError("overlap must be >= 0 and < max_words")
    words = text.split()
    if not words:
        return []
    step = max_words - overlap
    chunks = []
    for start in range(0, len(words), step):
        chunks.append(" ".join(words[start : start + max_words]))
        if start + max_words >= len(words):
            break
    return chunks
