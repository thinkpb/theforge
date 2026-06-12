"""PII leakage test suite (docs/TESTING.md Layer 3, ADR-0007).

All PII below is synthetic — no real personal data, ever. (Note: Presidio
deliberately invalidates the classic fake SSN 123-45-6789 as a known test
number, so fixtures use realistic-format fictional values.)

The mocked LiteLLM captures exactly what would have left the gateway for an
upstream provider, so these tests assert on the outbound boundary itself.
"""

import httpx

from forge.config import get_settings
from forge.main import create_app
from tests.conftest import TEST_DB_URL, TEST_KEY

# Synthetic patient note: identifiers must be scrubbed, clinical content kept.
SYNTHETIC_NOTE = (
    "Patient John Smith (SSN 536-90-4399, email john.smith@example.com, "
    "phone 212-555-0173) is prescribed Metformin 1000mg twice daily."
)


def _chat_body(content: str):
    return {"model": "gpt-4o", "messages": [{"role": "user", "content": content}]}


def _sent_text(fake_completion) -> str:
    return " ".join(
        m["content"] for m in fake_completion["messages"] if isinstance(m["content"], str)
    )


async def test_pii_never_leaves_the_gateway(client, auth_headers, fake_completion):
    response = await client.post(
        "/v1/chat/completions", headers=auth_headers, json=_chat_body(SYNTHETIC_NOTE)
    )
    assert response.status_code == 200
    sent = _sent_text(fake_completion)

    # identifiers scrubbed
    assert "John Smith" not in sent
    assert "536-90-4399" not in sent
    assert "john.smith@example.com" not in sent
    assert "212-555-0173" not in sent
    # replaced with type markers; dosage instructions preserved
    assert "<PERSON>" in sent
    assert "<US_SSN>" in sent
    assert "1000mg twice daily" in sent


async def test_allow_list_preserves_domain_terms(
    monkeypatch, db_engine, auth_headers, fake_completion
):
    """The small NER model tags drug names as PERSON; operators allow-list
    domain vocabulary so over-scrubbing doesn't destroy clinical content."""
    monkeypatch.setenv("FORGE_MASTER_KEY", TEST_KEY)
    monkeypatch.setenv("FORGE_DATABASE_URL", TEST_DB_URL)
    monkeypatch.setenv("FORGE_PII_ALLOW_LIST", '["Metformin"]')
    get_settings.cache_clear()
    application = create_app()
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await c.post(
                "/v1/chat/completions", headers=auth_headers, json=_chat_body(SYNTHETIC_NOTE)
            )
    sent = _sent_text(fake_completion)
    assert "Metformin 1000mg twice daily" in sent  # domain term kept
    assert "John Smith" not in sent  # real PII still scrubbed
    assert "536-90-4399" not in sent
    get_settings.cache_clear()


async def test_clean_text_passes_through_unchanged(client, auth_headers, fake_completion):
    await client.post(
        "/v1/chat/completions", headers=auth_headers, json=_chat_body("What is 2 + 2?")
    )
    assert _sent_text(fake_completion) == "What is 2 + 2?"


async def test_multimodal_text_parts_are_scrubbed(client, auth_headers, fake_completion):
    body = {
        "model": "gpt-4o",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Summarize: {SYNTHETIC_NOTE}"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}},
                ],
            }
        ],
    }
    await client.post("/v1/chat/completions", headers=auth_headers, json=body)
    (message,) = fake_completion["messages"]
    text_part = next(p for p in message["content"] if p.get("type") == "text")
    assert "536-90-4399" not in text_part["text"]
    assert "John Smith" not in text_part["text"]
    # non-text parts untouched
    assert {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}} in (
        message["content"]
    )


async def test_audit_records_redaction_count(client, app, auth_headers, fake_completion):
    await client.post(
        "/v1/chat/completions", headers=auth_headers, json=_chat_body(SYNTHETIC_NOTE)
    )
    await app.state.audit_buffer.drain()

    response = await client.get("/v1/audit", headers=auth_headers)
    (record,) = response.json()["data"]
    # at least person + SSN + email + phone
    assert record["pii_redactions"] >= 4


async def test_scrubbing_disabled_is_visible_in_audit(
    monkeypatch, db_engine, auth_headers, fake_completion
):
    """Opt-out must work and must leave a trace (ADR-0007)."""
    monkeypatch.setenv("FORGE_MASTER_KEY", TEST_KEY)
    monkeypatch.setenv("FORGE_DATABASE_URL", TEST_DB_URL)
    monkeypatch.setenv("FORGE_PII_SCRUBBING_ENABLED", "false")
    get_settings.cache_clear()
    application = create_app()
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await c.post(
                "/v1/chat/completions", headers=auth_headers, json=_chat_body(SYNTHETIC_NOTE)
            )
            await application.state.audit_buffer.drain()

            # content passed through untouched
            assert "536-90-4399" in _sent_text(fake_completion)

            response = await c.get("/v1/audit", headers=auth_headers)
            (record,) = response.json()["data"]
            assert record["pii_redactions"] is None  # None = off, 0 = on-but-clean
    get_settings.cache_clear()