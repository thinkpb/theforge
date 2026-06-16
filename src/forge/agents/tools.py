"""Agent tool registry (ADR-0019).

A tool is a named, JSON-schema'd async callable. An agent may only call tools on
its allow-list (enforced in the runtime) — tool authority is the blast-radius
control that prompt-injection defense (ADR-0018) pointed at: even a hijacked
agent cannot invoke a tool its spec doesn't grant.

Tool handlers receive a ToolContext scoped to the caller's team, so a tool can
only ever touch that team's data — the same isolation as the rest of the platform.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from forge.audit import AuditBuffer
from forge.config import Settings
from forge.pii import PIIScrubber
from forge.rag.ingest import search_documents
from forge.rag.store import VectorStore


@dataclass
class ToolContext:
    team: str
    settings: Settings
    scrubber: PIIScrubber
    store: VectorStore
    audit: AuditBuffer
    api_key_hash: str


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema for the arguments
    handler: Callable[..., Awaitable[str]]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


async def _document_search(ctx: ToolContext, query: str, limit: int = 4) -> str:
    """Retrieve passages from the team's collection. Results are scrubbed text
    from the store (ADR-0012); the search is itself audited as a search event."""
    results = await search_documents(
        query=query,
        limit=min(int(limit), 10),
        team=ctx.team,
        settings=ctx.settings,
        scrubber=ctx.scrubber,
        store=ctx.store,
        audit=ctx.audit,
        api_key_hash=ctx.api_key_hash,
    )
    if not results:
        return "No matching documents."
    return "\n\n".join(
        f"[{i + 1}] {r.get('title') or 'document'}: {r['text']}" for i, r in enumerate(results)
    )


DOCUMENT_SEARCH = Tool(
    name="document_search",
    description="Search the team's document collection for passages relevant to a query.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "what to search for"},
            "limit": {"type": "integer", "description": "max passages (<=10)", "default": 4},
        },
        "required": ["query"],
    },
    handler=_document_search,
)

REGISTRY: dict[str, Tool] = {DOCUMENT_SEARCH.name: DOCUMENT_SEARCH}
