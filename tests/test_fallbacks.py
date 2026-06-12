"""Fallback chain tests (ADR-0010) — LiteLLM mocked, per-attempt recording."""

import httpx
import litellm
import pytest

from forge.config import get_settings
from forge.main import create_app
from tests.conftest import FakeResponse, make_litellm_exc, set_test_env


def _chat_body():
    return {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture
async def fallback_app(monkeypatch, db_engine):
    set_test_env(monkeypatch)
    monkeypatch.setenv("FORGE_FALLBACK_MAP", '{"gpt-4o": ["claude-fable-5", "llama3.2"]}')
    get_settings.cache_clear()
    application = create_app()
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, application
    get_settings.cache_clear()


@pytest.fixture
def attempts(monkeypatch):
    """Mock acompletion with per-upstream behavior; records each attempt."""
    calls: list[str] = []
    behavior: dict[str, Exception] = {}

    async def _fake(**kwargs):
        calls.append(kwargs["model"])
        exc = behavior.get(kwargs["model"])
        if exc is not None:
            raise exc
        return FakeResponse(kwargs["model"])

    monkeypatch.setattr("forge.gateway.router.litellm.acompletion", _fake)
    return calls, behavior


async def test_transient_failure_falls_through_to_next_provider(
    fallback_app, auth_headers, attempts
):
    client, application = fallback_app
    calls, behavior = attempts
    behavior["openai/gpt-4o"] = make_litellm_exc(litellm.exceptions.Timeout)

    response = await client.post("/v1/chat/completions", headers=auth_headers, json=_chat_body())
    assert response.status_code == 200
    # client contract: the requested alias, even when a fallback served it
    assert response.json()["model"] == "gpt-4o"
    assert calls == ["openai/gpt-4o", "anthropic/claude-fable-5"]

    # the audit trail records who actually served it
    await application.state.audit_buffer.drain()
    audit = await client.get("/v1/audit", headers=auth_headers)
    (record,) = audit.json()["data"]
    assert record["outcome"] == "success"
    assert record["upstream_model"] == "anthropic/claude-fable-5"


async def test_non_transient_failure_does_not_fall_through(fallback_app, auth_headers, attempts):
    client, _ = fallback_app
    calls, behavior = attempts
    behavior["openai/gpt-4o"] = make_litellm_exc(litellm.exceptions.BadRequestError)

    response = await client.post("/v1/chat/completions", headers=auth_headers, json=_chat_body())
    assert response.status_code == 400
    assert calls == ["openai/gpt-4o"]  # no second attempt


async def test_exhausted_chain_returns_last_error(fallback_app, auth_headers, attempts):
    client, application = fallback_app
    calls, behavior = attempts
    behavior["openai/gpt-4o"] = make_litellm_exc(litellm.exceptions.Timeout)
    behavior["anthropic/claude-fable-5"] = make_litellm_exc(litellm.exceptions.RateLimitError)
    behavior["ollama/llama3.2:1b"] = make_litellm_exc(litellm.exceptions.RateLimitError)

    response = await client.post("/v1/chat/completions", headers=auth_headers, json=_chat_body())
    assert response.status_code == 429  # last error mapped honestly
    assert len(calls) == 3

    await application.state.audit_buffer.drain()
    audit = await client.get("/v1/audit", headers=auth_headers)
    (record,) = audit.json()["data"]
    assert record["outcome"] == "upstream_error"
    assert record["error_type"] == "RateLimitError"


async def test_no_fallback_configured_behaves_as_before(client, auth_headers, monkeypatch):
    async def _boom(**kwargs):
        raise make_litellm_exc(litellm.exceptions.Timeout)

    monkeypatch.setattr("forge.gateway.router.litellm.acompletion", _boom)
    response = await client.post("/v1/chat/completions", headers=auth_headers, json=_chat_body())
    assert response.status_code == 504