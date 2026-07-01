"""MCP server tests (ADR-0021) — JSON-RPC 2.0 over /mcp.

The tool handlers are real; the LLM isn't involved (MCP is direct tool
invocation). Auth, team scoping, and audit are the same controls as the agent
runtime — MCP is another surface, not a bypass.
"""

from sqlalchemy import select

from forge.audit import AuditLog, key_fingerprint
from forge.mcp import INVALID_PARAMS, METHOD_NOT_FOUND, PROTOCOL_VERSION


def _rpc(method: str, params: dict | None = None, req_id=1) -> dict:
    msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


async def test_requires_auth(client):
    response = await client.post("/mcp", json=_rpc("initialize"))
    assert response.status_code == 401


async def test_initialize_handshake(client, auth_headers):
    response = await client.post("/mcp", headers=auth_headers, json=_rpc("initialize"))
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "forge"
    assert "tools" in result["capabilities"]


async def test_tools_list_exposes_registry(client, auth_headers):
    response = await client.post("/mcp", headers=auth_headers, json=_rpc("tools/list"))
    tools = response.json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"document_search", "list_documents", "calculator"} <= names
    calc = next(t for t in tools if t["name"] == "calculator")
    assert "inputSchema" in calc and calc["inputSchema"]["type"] == "object"


async def test_tools_call_calculator(client, auth_headers):
    response = await client.post(
        "/mcp",
        headers=auth_headers,
        json=_rpc("tools/call", {"name": "calculator", "arguments": {"expression": "(2+3)*4"}}),
    )
    result = response.json()["result"]
    assert result["isError"] is False
    assert result["content"][0]["text"] == "20"


async def test_tools_call_document_search_is_audited(
    client, app, auth_headers, fake_embeddings
):
    await client.post(
        "/v1/documents", headers=auth_headers, json={"text": "Refund window is 47 days."}
    )
    response = await client.post(
        "/mcp",
        headers=auth_headers,
        json=_rpc("tools/call", {"name": "document_search", "arguments": {"query": "refund"}}),
    )
    assert response.json()["result"]["isError"] is False

    await app.state.audit_buffer.drain()
    key_hash = key_fingerprint(auth_headers["Authorization"].removeprefix("Bearer "))
    async with app.state.db_session_factory() as session:
        audits = list(await session.scalars(
            select(AuditLog).where(AuditLog.api_key_hash == key_hash)
        ))
    mcp_calls = [a for a in audits if a.event == "tool_call" and a.agent == "mcp"]
    assert mcp_calls and mcp_calls[0].tool == "document_search"


async def test_unknown_tool_is_invalid_params(client, auth_headers):
    response = await client.post(
        "/mcp",
        headers=auth_headers,
        json=_rpc("tools/call", {"name": "rm_rf", "arguments": {}}),
    )
    err = response.json()["error"]
    assert err["code"] == INVALID_PARAMS


async def test_tool_fault_is_iserror_not_rpc_error(client, auth_headers):
    # bad arguments for calculator → MCP isError result, not a JSON-RPC error
    response = await client.post(
        "/mcp",
        headers=auth_headers,
        json=_rpc("tools/call", {"name": "calculator", "arguments": {"wrong": "x"}}),
    )
    body = response.json()
    assert "error" not in body
    assert body["result"]["isError"] is True


async def test_unknown_method(client, auth_headers):
    response = await client.post("/mcp", headers=auth_headers, json=_rpc("frobnicate"))
    assert response.json()["error"]["code"] == METHOD_NOT_FOUND


async def test_notification_gets_no_body(client, auth_headers):
    # no "id" → a notification → 202, no JSON-RPC response
    response = await client.post(
        "/mcp", headers=auth_headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert response.status_code == 202
    assert response.content == b""


async def test_batch_request(client, auth_headers):
    batch = [
        _rpc("tools/call", {"name": "calculator", "arguments": {"expression": "1+1"}}, req_id="a"),
        _rpc("tools/call", {"name": "calculator", "arguments": {"expression": "2+2"}}, req_id="b"),
    ]
    response = await client.post("/mcp", headers=auth_headers, json=batch)
    results = {r["id"]: r["result"]["content"][0]["text"] for r in response.json()}
    assert results == {"a": "2", "b": "4"}


async def test_mcp_tools_are_team_scoped(client, auth_headers, fake_embeddings):
    await client.post("/v1/documents", headers=auth_headers, json={"text": "admin only doc"})
    created = await client.post(
        "/v1/keys", headers=auth_headers, json={"name": "t", "team": "other-team"}
    )
    other = {"Authorization": f"Bearer {created.json()['key']}"}

    response = await client.post(
        "/mcp",
        headers=other,
        json=_rpc("tools/call", {"name": "list_documents", "arguments": {}}),
    )
    text = response.json()["result"]["content"][0]["text"]
    assert "admin only doc" not in text  # other team sees none of admin's docs
