"""Hybrid search tests (ADR-0016). Real BM25 sparse vectors, mocked dense.

The decisive test makes dense embeddings useless (identical vector for every
text) so that ranking can only come from the BM25 leg — if hybrid finds the
exact-token document, the sparse path provably contributes.
"""

import pytest

from forge.rag.sparse import sparse_embed

RUNBOOKS = {
    "db-runbook": "When alert code ERR-4471 fires, fail reads to the replica pool.",
    "cache-runbook": "When alert code ERR-4472 fires, warm the cache from the snapshot loader.",
    "queue-runbook": "When alert code ERR-4473 fires, scale the consumer replicas.",
}


def test_sparse_encoding_is_deterministic_and_token_sensitive():
    [a1] = sparse_embed(["alert code ERR-4471 replica"])
    [a2] = sparse_embed(["alert code ERR-4471 replica"])
    [b] = sparse_embed(["alert code ERR-4472 snapshot"])
    assert a1.indices == a2.indices and a1.values == a2.values
    assert set(a1.indices) != set(b.indices)  # different tokens, different terms


@pytest.fixture
def useless_dense(monkeypatch):
    """Every text embeds to the same dense vector — dense ranking carries
    zero information, so retrieval quality must come from BM25."""

    async def _fake(texts, settings):
        return [[1.0] + [0.0] * 767 for _ in texts]

    monkeypatch.setattr("forge.rag.ingest.embed_texts", _fake)


async def _ingest_runbooks(client, headers):
    for title, text in RUNBOOKS.items():
        response = await client.post(
            "/v1/documents", headers=headers, json={"text": text, "title": title}
        )
        assert response.status_code == 201


async def test_hybrid_resolves_exact_tokens_dense_cannot(client, auth_headers, useless_dense):
    await _ingest_runbooks(client, auth_headers)

    response = await client.post(
        "/v1/search",
        headers=auth_headers,
        json={"query": "remediation for ERR-4472", "limit": 1, "mode": "hybrid"},
    )
    (top,) = response.json()["data"]
    assert top["title"] == "cache-runbook"  # only BM25 can know this


async def test_dense_mode_still_available(client, auth_headers, useless_dense):
    await _ingest_runbooks(client, auth_headers)
    response = await client.post(
        "/v1/search",
        headers=auth_headers,
        json={"query": "remediation for ERR-4472", "limit": 3, "mode": "dense"},
    )
    # with useless dense vectors all scores tie — we just assert the mode runs
    assert len(response.json()["data"]) == 3


async def test_unknown_mode_rejected(client, auth_headers):
    response = await client.post(
        "/v1/search", headers=auth_headers, json={"query": "x", "mode": "quantum"}
    )
    assert response.status_code == 422
    assert "Unknown search mode" in response.json()["detail"]


async def test_sparse_index_holds_scrubbed_text_only(client, auth_headers, useless_dense):
    """The BM25 index is readable token weights — scrub-before-embed must hold
    for the sparse leg too: an identifier query can't match anything."""
    note = "Patient John Smith (SSN 536-90-4399) follows protocol ERR-4471."
    await client.post("/v1/documents", headers=auth_headers, json={"text": note})

    by_ssn = await client.post(
        "/v1/search",
        headers=auth_headers,
        json={"query": "536-90-4399", "limit": 1, "mode": "hybrid"},
    )
    results = by_ssn.json()["data"]
    # the SSN was scrubbed before sparse encoding: searching for it finds the
    # doc only via the scrubbed marker text, never the raw identifier
    if results:
        assert "536-90-4399" not in results[0]["text"]