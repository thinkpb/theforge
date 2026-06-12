"""RAG injection into chat completions (ADR-0013).

Retrieval happens in the handler's setup phase (works for streaming too),
against the caller's team collection, and is audited as a search event. The
retrieved chunks are already-scrubbed text from the store (ADR-0012); they're
prepended as a system message, and the whole message list still passes through
the outbound scrubber in the gateway — re-scrubbing marker text is harmless.
"""

from typing import Any

from forge.audit import AuditBuffer
from forge.config import Settings
from forge.pii import PIIScrubber
from forge.rag.ingest import search_documents
from forge.rag.store import VectorStore

_CONTEXT_PREAMBLE = (
    "Use the following context documents to answer. If the answer is not in "
    "the context, say you don't know rather than guessing.\n\n"
)


def _query_from(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = [p.get("text", "") for p in content if p.get("type") == "text"]
            if texts:
                return " ".join(texts)
    return ""


async def build_rag_context(
    *,
    messages: list[dict[str, Any]],
    top_k: int,
    min_score: float,
    team: str,
    settings: Settings,
    scrubber: PIIScrubber,
    store: VectorStore,
    audit: AuditBuffer,
    api_key_hash: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (possibly augmented messages, sources used)."""
    query = _query_from(messages)
    if not query:
        return messages, []
    results = await search_documents(
        query=query,
        limit=top_k,
        team=team,
        settings=settings,
        scrubber=scrubber,
        store=store,
        audit=audit,
        api_key_hash=api_key_hash,
    )
    results = [r for r in results if r["score"] >= min_score]
    if not results:
        return messages, []

    context = "\n\n".join(
        f"[{index + 1}] {result.get('title') or 'document'}: {result['text']}"
        for index, result in enumerate(results)
    )
    system = {"role": "system", "content": _CONTEXT_PREAMBLE + context}
    sources = [
        {
            "doc_id": result["doc_id"],
            "title": result.get("title"),
            "chunk_index": result["chunk_index"],
            "score": result["score"],
        }
        for result in results
    ]
    return [system, *messages], sources
