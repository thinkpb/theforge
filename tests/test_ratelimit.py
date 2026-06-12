"""Token-aware rate limiting tests (ADR-0009). Requires Redis (compose/CI)."""

import httpx
import pytest

from forge.config import get_settings
from forge.main import create_app
from tests.conftest import set_test_env


def _chat_body():
    return {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture
async def limited_app(monkeypatch, db_engine, request):
    """App with tight limits from test params: (rpm, tpm)."""
    rpm, tpm = request.param
    set_test_env(monkeypatch)
    monkeypatch.setenv("FORGE_RATE_LIMIT_RPM", str(rpm))
    monkeypatch.setenv("FORGE_RATE_LIMIT_TPM", str(tpm))
    get_settings.cache_clear()
    application = create_app()
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, application
    get_settings.cache_clear()


async def _team_headers(client, auth_headers, name: str):
    created = await client.post(
        "/v1/keys", headers=auth_headers, json={"name": name, "team": "load"}
    )
    return {"Authorization": f"Bearer {created.json()['key']}"}


@pytest.mark.parametrize("limited_app", [(2, 100_000)], indirect=True)
async def test_request_limit_429_and_audited(limited_app, auth_headers, fake_completion):
    client, application = limited_app
    team = await _team_headers(client, auth_headers, "rpm-test")

    for _ in range(2):
        ok = await client.post("/v1/chat/completions", headers=team, json=_chat_body())
        assert ok.status_code == 200

    limited = await client.post("/v1/chat/completions", headers=team, json=_chat_body())
    assert limited.status_code == 429
    assert "request limit" in limited.json()["detail"]
    assert 0 < int(limited.headers["Retry-After"]) <= 60

    await application.state.audit_buffer.drain()
    audit = await client.get("/v1/audit", headers=auth_headers)
    outcomes = [r["outcome"] for r in audit.json()["data"]]
    assert outcomes.count("rate_limited") == 1
    assert outcomes.count("success") == 2


@pytest.mark.parametrize("limited_app", [(100, 10)], indirect=True)
async def test_token_budget_enforced_after_debit(limited_app, auth_headers, fake_completion):
    client, _ = limited_app
    team = await _team_headers(client, auth_headers, "tpm-test")

    # first request passes (budget unknown until usage comes back: 12 tokens > 10)
    first = await client.post("/v1/chat/completions", headers=team, json=_chat_body())
    assert first.status_code == 200

    second = await client.post("/v1/chat/completions", headers=team, json=_chat_body())
    assert second.status_code == 429
    assert "token limit" in second.json()["detail"]


@pytest.mark.parametrize("limited_app", [(1, 100_000)], indirect=True)
async def test_master_key_is_exempt(limited_app, auth_headers, fake_completion):
    client, _ = limited_app
    for _ in range(3):
        response = await client.post(
            "/v1/chat/completions", headers=auth_headers, json=_chat_body()
        )
        assert response.status_code == 200