"""RAG-injected chat completions (ADR-0013)."""

from tests.test_streaming import FakeChunk

FACT = "The refund window is 47 days for all enterprise contracts."
DECOY = "Quarterly parking assignments rotate among building tenants."


async def _ingest(client, headers, *texts):
    for text in texts:
        response = await client.post(
            "/v1/documents", headers=headers, json={"text": text, "title": "policy"}
        )
        assert response.status_code == 201


def _rag_chat(query: str, **rag):
    return {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": query}],
        "rag": rag or {},
    }


async def test_rag_injects_context_and_reports_sources(
    client, auth_headers, fake_embeddings, fake_completion
):
    await _ingest(client, auth_headers, FACT, DECOY)

    response = await client.post(
        "/v1/chat/completions",
        headers=auth_headers,
        json=_rag_chat("how long is the return period?", top_k=2, min_score=0.5),
    )
    assert response.status_code == 200

    # context was prepended as a system message containing the retrieved fact
    sent = fake_completion["messages"]
    assert sent[0]["role"] == "system"
    assert "47 days" in sent[0]["content"]
    assert sent[1] == {"role": "user", "content": "how long is the return period?"}

    # the decoy scored below min_score — exactly one source, fully attributed
    body = response.json()
    (source,) = body["forge_rag"]["sources"]
    assert source["title"] == "policy"
    assert source["score"] > 0.99
    assert body["model"] == "gpt-4o"  # contract intact


async def test_rag_with_no_documents_degrades_gracefully(
    client, auth_headers, fake_embeddings, fake_completion
):
    response = await client.post(
        "/v1/chat/completions", headers=auth_headers, json=_rag_chat("anything?")
    )
    assert response.status_code == 200
    # no context to inject: messages unchanged, sources empty — not an error
    assert fake_completion["messages"][0]["role"] == "user"
    assert response.json()["forge_rag"]["sources"] == []


async def test_plain_completion_has_no_rag_extension(
    client, auth_headers, fake_completion
):
    response = await client.post(
        "/v1/chat/completions",
        headers=auth_headers,
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert "forge_rag" not in response.json()


async def test_rag_works_with_streaming(client, auth_headers, fake_embeddings, monkeypatch):
    await _ingest(client, auth_headers, FACT)

    captured = {}

    async def _fake(**kwargs):
        captured.update(kwargs)

        async def _gen():
            yield FakeChunk(kwargs["model"], content="ok", finish="stop")

        return _gen()

    monkeypatch.setattr("forge.gateway.router.litellm.acompletion", _fake)
    response = await client.post(
        "/v1/chat/completions",
        headers=auth_headers,
        json={**_rag_chat("return period?"), "stream": True},
    )
    assert response.status_code == 200
    assert "data:" in response.text
    assert captured["messages"][0]["role"] == "system"
    assert "47 days" in captured["messages"][0]["content"]


async def test_rag_chat_audits_both_search_and_completion(
    client, app, auth_headers, fake_embeddings, fake_completion
):
    await _ingest(client, auth_headers, FACT)
    await client.post(
        "/v1/chat/completions", headers=auth_headers, json=_rag_chat("return period?")
    )
    await app.state.audit_buffer.drain()

    audit = await client.get("/v1/audit", headers=auth_headers)
    events = [r["event"] for r in audit.json()["data"]]
    assert "search" in events
    assert "completion" in events


async def test_rag_respects_team_isolation(client, auth_headers, fake_embeddings, fake_completion):
    created = await client.post(
        "/v1/keys", headers=auth_headers, json={"name": "other", "team": "other-team"}
    )
    other_headers = {"Authorization": f"Bearer {created.json()['key']}"}

    await _ingest(client, auth_headers, FACT)  # ingested as admin

    response = await client.post(
        "/v1/chat/completions", headers=other_headers, json=_rag_chat("return period?")
    )
    # other team retrieves nothing from admin's collection
    assert response.json()["forge_rag"]["sources"] == []
    assert fake_completion["messages"][0]["role"] == "user"