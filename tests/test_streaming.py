"""SSE streaming tests (ADR-0011) — audit and scrubbing must work on streams."""

import json

import litellm
import pytest

from tests.conftest import make_litellm_exc


class FakeChunk:
    def __init__(self, model, content=None, usage=None, finish=None):
        self._model = model
        self._content = content
        self._usage = usage
        self._finish = finish

    def model_dump(self):
        data = {
            "id": "chatcmpl-fake",
            "object": "chat.completion.chunk",
            "model": self._model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": self._content},
                    "finish_reason": self._finish,
                }
            ],
        }
        if self._usage:
            data["usage"] = self._usage
        return data


@pytest.fixture
def fake_stream(monkeypatch):
    """Mock acompletion for stream=True; records sent kwargs, optionally fails."""
    calls = {}
    state = {"fail_midstream": False}

    async def _fake(**kwargs):
        calls.update(kwargs)
        assert kwargs.get("stream") is True

        async def _gen():
            model = kwargs["model"]
            yield FakeChunk(model, content="hel")
            if state["fail_midstream"]:
                raise make_litellm_exc(litellm.exceptions.Timeout)
            yield FakeChunk(model, content="lo")
            yield FakeChunk(
                model,
                finish="stop",
                usage={"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
            )

        return _gen()

    monkeypatch.setattr("forge.gateway.router.litellm.acompletion", _fake)
    calls["_state"] = state
    return calls


def _events(text: str) -> list[str]:
    return [line[len("data: ") :] for line in text.splitlines() if line.startswith("data: ")]


def _stream_body(content="hi"):
    return {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": content}],
        "stream": True,
    }


async def test_stream_chunks_alias_and_done(client, app, auth_headers, fake_stream):
    response = await client.post(
        "/v1/chat/completions", headers=auth_headers, json=_stream_body()
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _events(response.text)
    assert events[-1] == "[DONE]"
    chunks = [json.loads(e) for e in events[:-1]]
    # ADR-0001 holds on every chunk: alias, never the upstream model string
    assert all(c["model"] == "gpt-4o" for c in chunks)
    text = "".join(c["choices"][0]["delta"]["content"] or "" for c in chunks)
    assert text == "hello"

    # the stream is audited once it completes, with the usage it reported
    await app.state.audit_buffer.drain()
    audit = await client.get("/v1/audit", headers=auth_headers)
    (record,) = audit.json()["data"]
    assert record["outcome"] == "success"
    assert record["total_tokens"] == 12
    assert record["upstream_model"] == "openai/gpt-4o"


async def test_stream_scrubs_pii_outbound(client, auth_headers, fake_stream):
    note = "Contact jane.doe@example.com about the case."
    await client.post("/v1/chat/completions", headers=auth_headers, json=_stream_body(note))
    sent = fake_stream["messages"][0]["content"]
    assert "jane.doe@example.com" not in sent
    assert "<EMAIL_ADDRESS>" in sent


async def test_midstream_failure_emits_error_event_and_audits(
    client, app, auth_headers, fake_stream
):
    fake_stream["_state"]["fail_midstream"] = True
    response = await client.post(
        "/v1/chat/completions", headers=auth_headers, json=_stream_body()
    )
    assert response.status_code == 200  # headers were already sent
    events = _events(response.text)
    assert "[DONE]" not in events
    assert "error" in json.loads(events[-1])

    await app.state.audit_buffer.drain()
    audit = await client.get("/v1/audit", headers=auth_headers)
    (record,) = audit.json()["data"]
    assert record["outcome"] == "upstream_error"
    assert record["error_type"] == "Timeout"


async def test_pre_stream_failure_gets_real_status_code(client, auth_headers, monkeypatch):
    async def _boom(**kwargs):
        raise make_litellm_exc(litellm.exceptions.AuthenticationError)

    monkeypatch.setattr("forge.gateway.router.litellm.acompletion", _boom)
    response = await client.post(
        "/v1/chat/completions", headers=auth_headers, json=_stream_body()
    )
    assert response.status_code == 502  # setup failed before the stream began