"""Chunking strategies (ADR-0015).

Three strategies behind one signature; the eval harness picks the default
(`evals/compare_chunking.py`), not intuition:

- fixed      — word windows with word overlap; cuts mid-sentence by design
- sentence   — packs whole sentences up to the budget; overlap is trailing
               sentences, so a fact is never split mid-thought
- paragraph  — packs whole paragraphs (the author's own semantic units);
               oversized paragraphs fall back to sentence packing. The first
               rung of hierarchical chunking — parent-child retrieval needs
               store support and is deferred.
"""

import re

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


def _words(text: str) -> int:
    return len(text.split())


def _fixed(text: str, max_words: int, overlap: int) -> list[str]:
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


def _pack(units: list[str], max_words: int, overlap: int) -> list[str]:
    """Pack whole units (sentences) into chunks; overlap = trailing units."""
    chunks: list[str] = []
    current: list[str] = []
    count = 0
    for unit in units:
        unit_words = _words(unit)
        if unit_words > max_words:
            if current:
                chunks.append(" ".join(current))
                current, count = [], 0
            chunks.extend(_fixed(unit, max_words, overlap))  # degenerate giant unit
            continue
        if count + unit_words > max_words and current:
            chunks.append(" ".join(current))
            kept: list[str] = []
            kept_words = 0
            for trailing in reversed(current):
                trailing_words = _words(trailing)
                if kept_words + trailing_words > overlap:
                    break
                kept.insert(0, trailing)
                kept_words += trailing_words
            current, count = kept, kept_words
        current.append(unit)
        count += unit_words
    if current:
        chunks.append(" ".join(current))
    return chunks


def _sentence(text: str, max_words: int, overlap: int) -> list[str]:
    sentences = [s for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    return _pack(sentences, max_words, overlap)


def _paragraph(text: str, max_words: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    count = 0
    for paragraph in (p.strip() for p in _PARAGRAPH_SPLIT.split(text) if p.strip()):
        paragraph_words = _words(paragraph)
        if paragraph_words > max_words:
            if current:
                chunks.append("\n\n".join(current))
                current, count = [], 0
            chunks.extend(_sentence(paragraph, max_words, overlap))
            continue
        if count + paragraph_words > max_words and current:
            # no overlap across paragraph boundaries — they're semantic seams
            chunks.append("\n\n".join(current))
            current, count = [], 0
        current.append(paragraph)
        count += paragraph_words
    if current:
        chunks.append("\n\n".join(current))
    return chunks


STRATEGIES = {
    "fixed": _fixed,
    "sentence": _sentence,
    "paragraph": _paragraph,
}


def chunk_text(text: str, max_words: int, overlap: int, strategy: str = "fixed") -> list[str]:
    if max_words <= 0:
        raise ValueError("max_words must be positive")
    if not 0 <= overlap < max_words:
        raise ValueError("overlap must be >= 0 and < max_words")
    try:
        chunker = STRATEGIES[strategy]
    except KeyError:
        raise ValueError(
            f"Unknown chunking strategy {strategy!r}. Available: {sorted(STRATEGIES)}"
        ) from None
    return chunker(text, max_words, overlap)
