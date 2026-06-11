"""Routing-layer tests with LiteLLM mocked out.

Per docs/TESTING.md Layer 1: the routing logic is deterministic and ours; the
LLM call is neither. Monkeypatching litellm.acompletion makes these tests fast,
free, offline, and CI-safe — we test the plumbing, not the model.
"""

import httpx
import litellm
import pytest


class FakeResponse:
    def __init__(self, model: str):
        self._model = model

    def model_dump(self):
        return {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "model": self._model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "hello"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
        }


@pytest.fixture
def fake_completion(monkeypatch):
    """Replace litellm.acompletion; returns the kwargs the router sent."""
    calls = {}

    async def _fake(**kwargs):
        calls.update(kwargs)
        return FakeResponse(kwargs["model"])

    monkeypatch.setattr("forge.gateway.router.litellm.acompletion", _fake)
    return calls


def _chat_body(model: str, **extra):
    return {"model": model, "messages": [{"role": "user", "content": "hi"}], **extra}


async def test_completion_response_contract(client, auth_headers, fake_completion):
    response = await client.post(
        "/v1/chat/completions", headers=auth_headers, json=_chat_body("gpt-4o-mini")
    )
    assert response.status_code == 200
    body = response.json()
    for field in ("id", "model", "choices", "usage"):
        assert field in body
    assert body["usage"]["prompt_tokens"] > 0
    # ADR-0001: clients see the alias, never the upstream provider string
    assert body["model"] == "gpt-4o-mini"
    assert fake_completion["model"] == "openai/gpt-4o-mini"


async def test_ollama_alias_gets_api_base(client, auth_headers, fake_completion):
    response = await client.post(
        "/v1/chat/completions", headers=auth_headers, json=_chat_body("llama3.2")
    )
    assert response.status_code == 200
    assert fake_completion["model"] == "ollama/llama3.2:1b"
    assert fake_completion["api_base"] == "http://localhost:11434"


async def test_hosted_alias_gets_no_api_base(client, auth_headers, fake_completion):
    await client.post("/v1/chat/completions", headers=auth_headers, json=_chat_body("gpt-4o"))
    assert "api_base" not in fake_completion


async def test_params_forwarded_only_when_set(client, auth_headers, fake_completion):
    await client.post(
        "/v1/chat/completions",
        headers=auth_headers,
        json=_chat_body("gpt-4o", temperature=0.2, max_tokens=50),
    )
    assert fake_completion["temperature"] == 0.2
    assert fake_completion["max_tokens"] == 50

    fake_completion.clear()
    await client.post("/v1/chat/completions", headers=auth_headers, json=_chat_body("gpt-4o"))
    assert "temperature" not in fake_completion
    assert "max_tokens" not in fake_completion


def _make_exc(exc_type: type[Exception]) -> Exception:
    kwargs = {"message": "boom", "llm_provider": "openai", "model": "gpt-4o"}
    if exc_type is litellm.exceptions.PermissionDeniedError:
        request = httpx.Request("POST", "http://upstream.test")
        kwargs["response"] = httpx.Response(403, request=request)
    return exc_type(**kwargs)


@pytest.mark.parametrize(
    ("exc_name", "expected_status"),
    [
        ("Timeout", 504),
        ("APIConnectionError", 504),
        ("RateLimitError", 429),
        ("AuthenticationError", 502),
        ("PermissionDeniedError", 502),
        ("BadRequestError", 400),
        ("InternalServerError", 502),  # unmapped APIError subclass -> fallback
    ],
)
async def test_upstream_errors_mapped(
    client, auth_headers, monkeypatch, exc_name, expected_status
):
    exc_type = getattr(litellm.exceptions, exc_name)

    async def _boom(**kwargs):
        raise _make_exc(exc_type)

    monkeypatch.setattr("forge.gateway.router.litellm.acompletion", _boom)
    response = await client.post(
        "/v1/chat/completions", headers=auth_headers, json=_chat_body("gpt-4o")
    )
    assert response.status_code == expected_status
    assert "Upstream provider error" in response.json()["detail"]