"""Chunking strategy tests (ADR-0015) — pure logic, no infrastructure."""

import pytest

from forge.rag.chunking import STRATEGIES, chunk_text

PARAGRAPHS = (
    "Alpha section. The cap is two million dollars. It applies per occurrence.\n\n"
    "Beta section. Notice must be written. It is due within thirty days.\n\n"
    "Gamma section. Credits are the exclusive remedy. They expire at term end."
)


def test_registry_and_unknown_strategy():
    assert set(STRATEGIES) == {"fixed", "sentence", "paragraph"}
    with pytest.raises(ValueError, match="Unknown chunking strategy"):
        chunk_text("text", 10, 2, strategy="quantum")


def test_fixed_cuts_mid_sentence_by_design():
    text = "The liability cap is two million dollars per occurrence."
    chunks = chunk_text(text, max_words=5, overlap=0, strategy="fixed")
    assert chunks[0] == "The liability cap is two"  # the known failure mode


def test_sentence_never_splits_a_sentence():
    text = "The cap is two million dollars. Notice is due in thirty days. Credits expire."
    chunks = chunk_text(text, max_words=8, overlap=0, strategy="sentence")
    for chunk in chunks:
        # every chunk is a concatenation of complete sentences
        assert chunk.rstrip().endswith(".")
    assert any("two million dollars." in c for c in chunks)


def test_sentence_overlap_carries_trailing_sentences():
    text = "One two three. Four five six. Seven eight nine."
    chunks = chunk_text(text, max_words=6, overlap=3, strategy="sentence")
    assert chunks[0] == "One two three. Four five six."
    assert chunks[1].startswith("Four five six.")  # trailing sentence carried


def test_sentence_giant_sentence_falls_back_to_fixed():
    giant = "word " * 30
    chunks = chunk_text(giant.strip(), max_words=10, overlap=2, strategy="sentence")
    assert len(chunks) > 1


def test_paragraph_respects_paragraph_boundaries():
    chunks = chunk_text(PARAGRAPHS, max_words=30, overlap=0, strategy="paragraph")
    # paragraphs pack together but never split internally
    for chunk in chunks:
        for paragraph in PARAGRAPHS.split("\n\n"):
            if paragraph[:20] in chunk:
                assert paragraph in chunk, "paragraph was split across chunks"


def test_paragraph_oversized_paragraph_falls_back_to_sentences():
    big = "This is a sentence about clause limits. " * 20  # ~160 words, one paragraph
    chunks = chunk_text(big.strip(), max_words=50, overlap=0, strategy="paragraph")
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.rstrip().endswith(".")


@pytest.mark.parametrize("strategy", sorted(STRATEGIES))
def test_no_content_is_lost(strategy):
    text = PARAGRAPHS
    chunks = chunk_text(text, max_words=20, overlap=5, strategy=strategy)
    chunk_words = {w for c in chunks for w in c.split()}
    assert set(text.split()) <= chunk_words


async def test_ingest_accepts_strategy_and_rejects_unknown(
    client, auth_headers, fake_embeddings
):
    ok = await client.post(
        "/v1/documents",
        headers=auth_headers,
        json={"text": PARAGRAPHS, "chunking": "paragraph"},
    )
    assert ok.status_code == 201
    assert ok.json()["chunks"] >= 1

    bad = await client.post(
        "/v1/documents",
        headers=auth_headers,
        json={"text": "x", "chunking": "quantum"},
    )
    assert bad.status_code == 422
    assert "Unknown chunking strategy" in bad.json()["detail"]