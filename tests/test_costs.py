"""Cost attribution tests — aggregation over the audit trail."""

import pytest


def _chat_body():
    return {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture
def priced_completion(fake_completion, monkeypatch):
    """Make every mocked completion cost a known amount."""
    monkeypatch.setattr(
        "forge.gateway.router.litellm.completion_cost", lambda completion_response: 0.01
    )
    return fake_completion


async def test_costs_grouped_by_team_and_key(client, app, auth_headers, priced_completion):
    created = await client.post(
        "/v1/keys", headers=auth_headers, json={"name": "bot", "team": "oncology"}
    )
    team_headers = {"Authorization": f"Bearer {created.json()['key']}"}

    await client.post("/v1/chat/completions", headers=auth_headers, json=_chat_body())
    await client.post("/v1/chat/completions", headers=team_headers, json=_chat_body())
    await client.post("/v1/chat/completions", headers=team_headers, json=_chat_body())
    await app.state.audit_buffer.drain()

    response = await client.get("/v1/costs", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()

    assert body["by_team"]["oncology"]["requests"] == 2
    assert body["by_team"]["oncology"]["cost_usd"] == pytest.approx(0.02)
    assert body["by_team"]["oncology"]["total_tokens"] == 24  # 12 per mocked call
    assert body["by_team"]["admin"]["requests"] == 1
    assert body["total_cost_usd"] == pytest.approx(0.03)

    names = {e["key_name"] for e in body["by_key"]}
    assert names == {"bot", "master"}


async def test_costs_requires_master_key(client, auth_headers):
    created = await client.post(
        "/v1/keys", headers=auth_headers, json={"name": "bot", "team": "x"}
    )
    team_headers = {"Authorization": f"Bearer {created.json()['key']}"}
    assert (await client.get("/v1/costs", headers=team_headers)).status_code == 403