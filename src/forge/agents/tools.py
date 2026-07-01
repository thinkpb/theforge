"""Agent tool registry (ADR-0019).

A tool is a named, JSON-schema'd async callable. An agent may only call tools on
its allow-list (enforced in the runtime) — tool authority is the blast-radius
control that prompt-injection defense (ADR-0018) pointed at: even a hijacked
agent cannot invoke a tool its spec doesn't grant.

Tool handlers receive a ToolContext scoped to the caller's team, so a tool can
only ever touch that team's data — the same isolation as the rest of the platform.
"""

import ast
import operator
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from forge.audit import AuditBuffer
from forge.config import Settings
from forge.pii import PIIScrubber
from forge.rag.ingest import search_documents
from forge.rag.store import VectorStore, collection_for_team


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


async def _list_documents(ctx: ToolContext) -> str:
    """List the titles of documents in the team's collection — a read tool,
    distinct authority from search, still team-scoped (ADR-0020)."""
    collection = collection_for_team(
        ctx.settings.qdrant_collection_prefix, ctx.team
    )
    titles = await ctx.store.list_titles(collection)
    if not titles:
        return "No documents in this collection."
    return "\n".join(f"- {t}" for t in titles)


LIST_DOCUMENTS = Tool(
    name="list_documents",
    description="List the titles of documents available in the team's collection.",
    parameters={"type": "object", "properties": {}},
    handler=_list_documents,
)


# --- calculator: a pure tool that needs no data access at all (ADR-0020) ------

_CALC_OPS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}
_CALC_MAX_EXPONENT = 1000  # bound ** so 2**10**9 can't DoS the worker


def _calc_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _CALC_OPS:
        left, right = _calc_eval(node.left), _calc_eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _CALC_MAX_EXPONENT:
            raise ValueError("exponent too large")
        return _CALC_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _CALC_OPS:
        return _CALC_OPS[type(node.op)](_calc_eval(node.operand))
    raise ValueError("unsupported expression")


async def _calculator(ctx: ToolContext, expression: str) -> str:
    # no eval(): parse to an AST and walk only arithmetic nodes — names, calls,
    # attribute access, etc. all raise. A calculator can't touch data or code.
    try:
        tree = ast.parse(expression, mode="eval")
        return str(_calc_eval(tree.body))
    except Exception:
        return f"Error: could not evaluate {expression!r} as arithmetic."


CALCULATOR = Tool(
    name="calculator",
    description="Evaluate a basic arithmetic expression (+ - * / // % ** and parentheses).",
    parameters={
        "type": "object",
        "properties": {"expression": {"type": "string", "description": "e.g. (2 + 3) * 4"}},
        "required": ["expression"],
    },
    handler=_calculator,
)


REGISTRY: dict[str, Tool] = {
    DOCUMENT_SEARCH.name: DOCUMENT_SEARCH,
    LIST_DOCUMENTS.name: LIST_DOCUMENTS,
    CALCULATOR.name: CALCULATOR,
}
