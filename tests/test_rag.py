"""RAG foundation tests (ADR-0012). Qdrant must be running (compose/CI);
embeddings are mocked — we test the pipeline, not the model (TESTING.md).

Retrieval itself is deterministic and gets Layer-1 asserts: with orthogonal
fake vectors, the planted fact either comes back or it doesn't.
"""

import pytest
from qdrant_client import AsyncQdrantClient

from forge.config import get_settings
from forge.rag.chunking import chunk_text

FACT = "The refund window is 47 days for all enterprise contracts."
DECOY = "Quarterly parking assignments rotate among building tenants."
SYNTHETIC_NOTE = (
    "Patient John Smith (SSN 536-90-4399) is prescribed Metformin 1000mg twice daily."
)


# --- chunking: pure logic, no infrastructure ---------------------------------


def test_chunker_single_chunk_for_short_text():
    assert chunk_text("a b c", max_words=10, overlap=2) == ["a b c"]


def test_chunker_overlap_repeats_boundary_words():
    words = " ".join(str(i) for i in range(10))
    chunks = chunk_text(words, max_words=4, overlap=2)
    assert chunks[0] == "0 1 2 3"
    assert chunks[1] == "2 3 4 5"  # overlap of 2
    # every word appears in at least one chunk
    assert set(words.split()) == {w for c in chunks for w in c.split()}


def test_chunker_empty_and_validation():
    assert chunk_text("   ", 10, 2) == []
    with pytest.raises(ValueError):
        chunk_text("x", max_words=5, overlap=5)


# --- ingestion + search ------------------------------------------------------


async def test_planted_fact_is_retrieved(client, auth_headers, fake_embeddings):
    for text in (FACT, DECOY):
        response = await client.post(
            "/v1/documents", headers=auth_headers, json={"text": text}
        )
        assert response.status_code == 201

    # dense mode pins the dense-pipeline contract: orthogonal fakes → exact match
    search = await client.post(
        "/v1/search",
        headers=auth_headers,
        json={"query": "how long is the return period?", "limit": 2, "mode": "dense"},
    )
    results = search.json()["data"]
    assert "47 days" in results[0]["text"]
    assert results[0]["score"] > 0.99

    # hybrid (default) must rank the same doc first — scores are RRF-scale
    hybrid = await client.post(
        "/v1/search",
        headers=auth_headers,
        json={"query": "how long is the return period?", "limit": 2},
    )
    assert "47 days" in hybrid.json()["data"][0]["text"]


async def test_vector_store_never_holds_raw_pii(client, auth_headers, fake_embeddings):
    response = await client.post(
        "/v1/documents", headers=auth_headers, json={"text": SYNTHETIC_NOTE, "title": "note"}
    )
    body = response.json()
    assert body["pii_redactions"] >= 2

    # read the stored payloads straight from qdrant — the compliance claim
    # is about what's at rest, so assert on what's at rest
    settings = get_settings()
    qdrant = AsyncQdrantClient(url=settings.qdrant_url)
    points, _ = await qdrant.scroll(body["collection"], limit=10, with_payload=True)
    await qdrant.close()
    stored = " ".join(p.payload["text"] for p in points)
    assert "536-90-4399" not in stored
    assert "John Smith" not in stored
    assert "<US_SSN>" in stored


async def test_collections_are_team_scoped(client, auth_headers, fake_embeddings):
    a = await client.post(
        "/v1/keys", headers=auth_headers, json={"name": "a", "team": "team-a"}
    )
    b = await client.post(
        "/v1/keys", headers=auth_headers, json={"name": "b", "team": "team-b"}
    )
    headers_a = {"Authorization": f"Bearer {a.json()['key']}"}
    headers_b = {"Authorization": f"Bearer {b.json()['key']}"}

    await client.post("/v1/documents", headers=headers_a, json={"text": FACT})

    own = await client.post(
        "/v1/search", headers=headers_a, json={"query": "return period"}
    )
    assert len(own.json()["data"]) == 1

    other = await client.post(
        "/v1/search", headers=headers_b, json={"query": "return period"}
    )
    assert other.json()["data"] == []  # team B sees nothing of team A's


async def test_rag_operations_are_audited(client, app, auth_headers, fake_embeddings):
    await client.post("/v1/documents", headers=auth_headers, json={"text": SYNTHETIC_NOTE})
    await client.post("/v1/search", headers=auth_headers, json={"query": "medication?"})
    await app.state.audit_buffer.drain()

    audit = await client.get("/v1/audit", headers=auth_headers)
    by_event = {r["event"]: r for r in audit.json()["data"]}
    assert by_event["ingestion"]["outcome"] == "success"
    assert by_event["ingestion"]["pii_redactions"] >= 2
    assert by_event["search"]["outcome"] == "success"


async def test_pii_opt_out_key_stores_raw_and_leaves_trace(
    client, app, auth_headers, fake_embeddings
):
    created = await client.post(
        "/v1/keys",
        headers=auth_headers,
        json={"name": "legacy", "team": "legacy", "pii_opt_out": True},
    )
    headers = {"Authorization": f"Bearer {created.json()['key']}"}

    response = await client.post(
        "/v1/documents", headers=headers, json={"text": SYNTHETIC_NOTE}
    )
    assert response.json()["pii_redactions"] is None  # opt-out is visible

    await app.state.audit_buffer.drain()
    audit = await client.get("/v1/audit", headers=auth_headers)
    ingestion = next(r for r in audit.json()["data"] if r["event"] == "ingestion")
    assert ingestion["pii_redactions"] is None