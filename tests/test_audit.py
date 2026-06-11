"""Audit trail tests against real Postgres (ADR-0006).

The append-only guarantee is a database trigger, so it must be tested against
the real database — SQLite can't stand in for a compliance control.
"""

import litellm
import pytest
from sqlalchemy import text

from forge.config import get_settings
from forge.main import create_app
from tests.conftest import TEST_DB_URL, TEST_KEY, make_litellm_exc


def _chat_body(model: str):
    return {"model": model, "messages": [{"role": "user", "content": "hi"}]}


async def _rows(db_engine):
    async with db_engine.connect() as conn:
        result = await conn.execute(text("SELECT * FROM audit_log ORDER BY id"))
        return result.mappings().all()


async def test_success_writes_audit_row(client, app, auth_headers, fake_completion, db_engine):
    await client.post("/v1/chat/completions", headers=auth_headers, json=_chat_body("gpt-4o-mini"))
    await app.state.audit_buffer.drain()

    (row,) = await _rows(db_engine)
    assert row["model_alias"] == "gpt-4o-mini"
    assert row["upstream_model"] == "openai/gpt-4o-mini"
    assert row["outcome"] == "success"
    assert row["status_code"] == 200
    assert row["prompt_tokens"] == 5
    assert row["completion_tokens"] == 7
    assert row["total_tokens"] == 12
    assert row["latency_ms"] >= 0
    # the credential itself must never appear — only its fingerprint
    assert row["api_key_hash"] != TEST_KEY
    assert len(row["api_key_hash"]) == 64


async def test_upstream_error_writes_audit_row(client, app, auth_headers, monkeypatch, db_engine):
    async def _boom(**kwargs):
        raise make_litellm_exc(litellm.exceptions.RateLimitError)

    monkeypatch.setattr("forge.gateway.router.litellm.acompletion", _boom)
    response = await client.post(
        "/v1/chat/completions", headers=auth_headers, json=_chat_body("gpt-4o")
    )
    assert response.status_code == 429
    await app.state.audit_buffer.drain()

    (row,) = await _rows(db_engine)
    assert row["outcome"] == "upstream_error"
    assert row["error_type"] == "RateLimitError"
    assert row["status_code"] == 429
    assert row["prompt_tokens"] is None


async def test_unknown_alias_writes_rejected_row(client, app, auth_headers, db_engine):
    response = await client.post(
        "/v1/chat/completions", headers=auth_headers, json=_chat_body("nope")
    )
    assert response.status_code == 400
    await app.state.audit_buffer.drain()

    (row,) = await _rows(db_engine)
    assert row["outcome"] == "rejected"
    assert row["status_code"] == 400
    assert row["upstream_model"] is None


async def test_audit_log_is_append_only(client, app, auth_headers, fake_completion, db_engine):
    await client.post("/v1/chat/completions", headers=auth_headers, json=_chat_body("gpt-4o"))
    await app.state.audit_buffer.drain()

    async with db_engine.begin() as conn:
        with pytest.raises(Exception, match="append-only"):
            await conn.execute(text("UPDATE audit_log SET model_alias = 'tampered'"))
    async with db_engine.begin() as conn:
        with pytest.raises(Exception, match="append-only"):
            await conn.execute(text("DELETE FROM audit_log"))


async def test_audit_endpoint(client, app, auth_headers, fake_completion):
    await client.post("/v1/chat/completions", headers=auth_headers, json=_chat_body("gpt-4o"))
    await app.state.audit_buffer.drain()

    unauthed = await client.get("/v1/audit")
    assert unauthed.status_code == 401

    response = await client.get("/v1/audit", headers=auth_headers)
    assert response.status_code == 200
    (record,) = response.json()["data"]
    assert record["model_alias"] == "gpt-4o"
    assert record["outcome"] == "success"


async def test_full_buffer_rejects_requests(monkeypatch, db_engine, auth_headers, fake_completion):
    """Backpressure backstop: a request that can't be audited doesn't happen."""
    import httpx

    monkeypatch.setenv("FORGE_MASTER_KEY", TEST_KEY)
    monkeypatch.setenv("FORGE_DATABASE_URL", TEST_DB_URL)
    monkeypatch.setenv("FORGE_AUDIT_QUEUE_SIZE", "1")
    get_settings.cache_clear()
    application = create_app()
    async with application.router.lifespan_context(application):
        buffer = application.state.audit_buffer
        buffer.cancel()  # simulate a stuck sink: nothing ever drains

        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            first = await c.post(
                "/v1/chat/completions", headers=auth_headers, json=_chat_body("gpt-4o")
            )
            assert first.status_code == 200  # fills the queue (maxsize=1)

            second = await c.post(
                "/v1/chat/completions", headers=auth_headers, json=_chat_body("gpt-4o")
            )
            assert second.status_code == 503
            assert "Audit backlog" in second.json()["detail"]

        # empty the queue by hand so lifespan shutdown doesn't wait on a
        # cancelled worker
        while not buffer._queue.empty():
            buffer._queue.get_nowait()
            buffer._queue.task_done()
    get_settings.cache_clear()