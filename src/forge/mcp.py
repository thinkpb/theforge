"""Model Context Protocol server, JSON-RPC 2.0 (ADR-0021).

Exposes Forge's agent tools to any MCP client. This implements the
request/response subset of the Streamable HTTP transport: the client POSTs a
JSON-RPC message and receives a JSON-RPC response (no server-initiated SSE
stream). `initialize`, `tools/list`, and `tools/call` are the functional core of
the spec — enough for a client to discover and invoke tools.

MCP is another surface onto the same tools, not a bypass: calls run in the
caller's team (from the Forge API key), are team-scoped, and are audited exactly
like agent tool calls.
"""

import uuid
from typing import Any

from forge.agents.tools import REGISTRY, ToolContext
from forge.audit import AuditBuffer, AuditRecord

PROTOCOL_VERSION = "2024-11-05"

# JSON-RPC 2.0 error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def result(req_id: Any, payload: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": payload}


def error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _server_info() -> dict:
    from forge import __version__

    return {"name": "forge", "version": __version__}


def _audit_tool_call(audit: AuditBuffer, tool_ctx: ToolContext, tool: str, outcome: str) -> None:
    audit.put(
        AuditRecord(
            request_id=uuid.uuid4(),
            api_key_hash=tool_ctx.api_key_hash,
            model_alias="mcp",
            upstream_model=None,
            outcome=outcome,
            status_code=200 if outcome == "success" else 500,
            error_type=None if outcome == "success" else outcome,
            latency_ms=0,
            event="tool_call",
            agent="mcp",  # attributes MCP-originated calls in the audit trail
            tool=tool,
        )
    )


async def dispatch(message: dict, tool_ctx: ToolContext, audit: AuditBuffer) -> dict | None:
    """Handle one JSON-RPC message. Returns a response dict, or None for
    notifications (messages without an id), which get no response per JSON-RPC."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return error(None, INVALID_REQUEST, "invalid JSON-RPC 2.0 request")

    method = message.get("method")
    req_id = message.get("id")
    is_notification = "id" not in message

    if method == "initialize":
        return result(
            req_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": _server_info(),
            },
        )

    if isinstance(method, str) and method.startswith("notifications/"):
        return None  # e.g. notifications/initialized — no response

    if method == "tools/list":
        tools = [
            {"name": t.name, "description": t.description, "inputSchema": t.parameters}
            for t in REGISTRY.values()
        ]
        return result(req_id, {"tools": tools})

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        tool = REGISTRY.get(name)
        if tool is None:
            return error(req_id, INVALID_PARAMS, f"unknown tool {name!r}")
        try:
            text = await tool.handler(tool_ctx, **arguments)
            outcome = "success"
        except TypeError as exc:  # bad arguments for this tool
            text = f"Error: invalid arguments: {exc}"
            outcome = "error"
        except Exception as exc:  # a tool fault is reported to the client, audited
            text = f"Error: {exc}"
            outcome = "error"
        _audit_tool_call(audit, tool_ctx, name, outcome)
        # MCP convention: tool faults are isError results, not JSON-RPC errors
        return result(req_id, {"content": [{"type": "text", "text": text}],
                               "isError": outcome != "success"})

    if is_notification:
        return None
    return error(req_id, METHOD_NOT_FOUND, f"unknown method {method!r}")
