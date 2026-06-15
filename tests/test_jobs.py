"""Async ingestion job tests (ADR-0017).

The arq worker is a separate process in production; here we test the task
function directly against real Postgres + Qdrant (embeddings mocked), which is
the honest coverage — the task is just an async function taking a ctx.
"""

import uuid

import pytest
from qdrant_client import AsyncQdrantClient
from sqlalchemy import select

from forge.audit import AuditLog
from forge.config import get_settings
from forge.jobs import COMPLETE, FAILED, RUNNING, create_job, get_job
from forge.rag.store import collection_for_team
from forge.worker import ingest_job

NOTE = "Patient John Smith (SSN 536-90-4399) was prescribed Metformin 1000mg daily."


def _worker_ctx(app):
    """Build the ctx the arq worker would assemble in on_startup, from the
    already-running app resources."""
    return {
        "settings": get_settings(),
        "session_factory": app.state.db_session_factory,
        "audit_buffer": app.state.audit_buffer,
        "vector_store": app.state.vector_store,
        "scrubber": app.state.pii_scrubber,
    }


async def _point_count(team: str) -> int:
    settings = get_settings()
    collection = collection_for_team(settings.qdrant_collection_prefix, team)
    client = AsyncQdrantClient(url=settings.qdrant_url)
    try:
        if not await client.collection_exists(collection):
            return 0
        return (await client.count(collection)).count
    finally:
        await client.close()


async def _ingestion_audits(app, api_key_hash: str) -> list[AuditLog]:
    await app.state.audit_buffer.drain()
    async with app.state.db_session_factory() as session:
        rows = await session.scalars(
            select(AuditLog).where(
                AuditLog.event == "ingestion", AuditLog.api_key_hash == api_key_hash
            )
        )
        return list(rows)


# --- enqueue endpoint ---------------------------------------------------------


@pytest.fixture
def captured_enqueue(app, monkeypatch):
    calls = []

    async def _fake(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(app.state.arq, "enqueue_job", _fake)
    return calls


async def test_async_ingest_enqueues_and_creates_queued_job(
    client, auth_headers, captured_enqueue
):
    response = await client.post(
        "/v1/documents/async", headers=auth_headers, json={"text": "hello world", "title": "t"}
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    job_id = body["job_id"]

    # the work was actually enqueued, with the scrub flag derived from the key
    (call,) = captured_enqueue
    assert call[1]["scrub"] is True
    assert call[1]["_job_id"] == job_id

    status = await client.get(f"/v1/documents/jobs/{job_id}", headers=auth_headers)
    assert status.status_code == 200
    assert status.json()["status"] == "queued"


async def test_async_ingest_rejects_unknown_chunking(client, auth_headers, captured_enqueue):
    response = await client.post(
        "/v1/documents/async",
        headers=auth_headers,
        json={"text": "x", "chunking": "quantum"},
    )
    assert response.status_code == 422
    assert captured_enqueue == []  # never enqueued a bad job


async def test_job_status_is_team_scoped(client, auth_headers, captured_enqueue):
    created = await client.post(
        "/v1/documents/async", headers=auth_headers, json={"text": "secret team doc"}
    )
    job_id = created.json()["job_id"]

    other = await client.post(
        "/v1/keys", headers=auth_headers, json={"name": "other", "team": "other-team"}
    )
    other_headers = {"Authorization": f"Bearer {other.json()['key']}"}

    # another team cannot see this job — 404, not 403, so existence doesn't leak
    response = await client.get(f"/v1/documents/jobs/{job_id}", headers=other_headers)
    assert response.status_code == 404


async def test_unknown_job_id_is_404(client, auth_headers):
    response = await client.get(f"/v1/documents/jobs/{uuid.uuid4()}", headers=auth_headers)
    assert response.status_code == 404


# --- worker task --------------------------------------------------------------


async def test_worker_task_ingests_and_completes(app, fake_embeddings):
    session_factory = app.state.db_session_factory
    job_id = uuid.uuid4()
    await create_job(
        session_factory, job_id=job_id, team="admin", api_key_hash="hash", title="note"
    )

    result = await ingest_job(
        _worker_ctx(app),
        job_id=str(job_id),
        text=NOTE,
        title="note",
        team="admin",
        api_key_hash="hash",
        chunking=None,
        scrub=True,
    )
    assert result["chunks"] >= 1

    job = await get_job(session_factory, job_id, "admin")
    assert job.status == COMPLETE
    assert job.doc_id is not None
    assert job.chunks >= 1
    assert job.pii_redactions >= 2  # name + SSN, scrubbed before embed


async def test_worker_task_opt_out_skips_scrubbing(app, fake_embeddings):
    session_factory = app.state.db_session_factory
    job_id = uuid.uuid4()
    await create_job(
        session_factory, job_id=job_id, team="legacy", api_key_hash="optout", title=None
    )
    await ingest_job(
        _worker_ctx(app),
        job_id=str(job_id),
        text=NOTE,
        title=None,
        team="legacy",
        api_key_hash="optout",
        chunking=None,
        scrub=False,
    )
    job = await get_job(session_factory, job_id, "legacy")
    assert job.status == COMPLETE
    assert job.pii_redactions is None  # opt-out leaves its trace (ADR-0008)

    # the audit row must record NULL too — not 0 (which would mean "scrubbed,
    # found nothing"). The opt-out trace has to survive into the audit trail.
    (audit,) = await _ingestion_audits(app, "optout")
    assert audit.outcome == "success"
    assert audit.pii_redactions is None


async def test_worker_retry_does_not_duplicate_chunks(app, fake_embeddings):
    """arq is at-least-once. A re-run of the same job id must overwrite its
    points, never duplicate them (ADR-0017: deterministic ids + status guard)."""
    session_factory = app.state.db_session_factory
    job_id = uuid.uuid4()
    await create_job(session_factory, job_id=job_id, team="retry", api_key_hash="h", title=None)
    args = dict(
        job_id=str(job_id), text=NOTE, title=None, team="retry",
        api_key_hash="h", chunking=None, scrub=True,
    )

    await ingest_job(_worker_ctx(app), **args)
    after_first = await _point_count("retry")
    assert after_first >= 1

    # (a) retry after COMPLETE → early return, no re-embed, no growth
    await ingest_job(_worker_ctx(app), **args)
    assert await _point_count("retry") == after_first

    # (b) crash-mid-run simulation: status back to RUNNING, re-run → deterministic
    # point ids overwrite rather than duplicate
    from forge.jobs import _update

    await _update(session_factory, job_id, status=RUNNING)
    await ingest_job(_worker_ctx(app), **args)
    assert await _point_count("retry") == after_first


async def test_failed_ingestion_is_audited(app, monkeypatch):
    async def _boom(texts, settings):
        raise RuntimeError("embedding backend down")

    monkeypatch.setattr("forge.rag.ingest.embed_texts", _boom)
    session_factory = app.state.db_session_factory
    job_id = uuid.uuid4()
    await create_job(
        session_factory, job_id=job_id, team="admin", api_key_hash="failaudit", title=None
    )

    with pytest.raises(RuntimeError):
        await ingest_job(
            _worker_ctx(app),
            job_id=str(job_id),
            text=NOTE,
            title=None,
            team="admin",
            api_key_hash="failaudit",
            chunking=None,
            scrub=True,
        )
    # every ingestion is audited — including the failure (parity with chat path)
    (audit,) = await _ingestion_audits(app, "failaudit")
    assert audit.outcome == "error"
    assert audit.error_type == "RuntimeError"


async def test_worker_legacy_collection_marks_job_failed(app, fake_embeddings):
    """The sync path returns 409; the worker path records the same mismatch as a
    FAILED job with an actionable error (parity, ADR-0016/0017)."""
    from qdrant_client import models

    settings = get_settings()
    name = collection_for_team(settings.qdrant_collection_prefix, "legacyteam")
    legacy = AsyncQdrantClient(url=settings.qdrant_url)
    await legacy.create_collection(
        name, vectors_config=models.VectorParams(size=768, distance=models.Distance.COSINE)
    )
    await legacy.close()

    session_factory = app.state.db_session_factory
    job_id = uuid.uuid4()
    await create_job(
        session_factory, job_id=job_id, team="legacyteam", api_key_hash="h", title=None
    )
    with pytest.raises(Exception):  # noqa: B017 — CollectionSchemaMismatch re-raised
        await ingest_job(
            _worker_ctx(app),
            job_id=str(job_id),
            text=NOTE,
            title=None,
            team="legacyteam",
            api_key_hash="h",
            chunking=None,
            scrub=True,
        )
    job = await get_job(session_factory, job_id, "legacyteam")
    assert job.status == FAILED
    assert "re-ingest" in job.error.lower()


async def test_worker_lifecycle_drains_audit_on_shutdown(app, fake_embeddings):
    """on_startup/on_shutdown build and tear down the worker's OWN audit buffer;
    a successful ingestion's audit event must be flushed before the process exits."""
    from forge import worker

    ctx: dict = {}
    await worker.on_startup(ctx)
    try:
        job_id = uuid.uuid4()
        await create_job(
            app.state.db_session_factory,
            job_id=job_id,
            team="admin",
            api_key_hash="lifecycle",
            title=None,
        )
        await ingest_job(
            ctx, job_id=str(job_id), text=NOTE, title=None, team="admin",
            api_key_hash="lifecycle", chunking=None, scrub=True,
        )
    finally:
        await worker.on_shutdown(ctx)  # drains the worker's buffer to Postgres

    # read back through the app's engine — the worker's events are durable
    async with app.state.db_session_factory() as session:
        rows = list(
            await session.scalars(
                select(AuditLog).where(AuditLog.api_key_hash == "lifecycle")
            )
        )
    assert len(rows) == 1
    assert rows[0].event == "ingestion"
    assert rows[0].outcome == "success"


async def test_async_ingest_rejects_oversize_text(
    client, auth_headers, captured_enqueue, monkeypatch
):
    monkeypatch.setattr(get_settings(), "rag_max_upload_bytes", 50)
    response = await client.post(
        "/v1/documents/async", headers=auth_headers, json={"text": "x" * 200}
    )
    assert response.status_code == 413
    assert captured_enqueue == []  # rejected before enqueue


async def test_enqueue_failure_marks_job_failed(client, app, auth_headers, monkeypatch):
    async def _boom(*args, **kwargs):
        raise ConnectionError("redis gone")

    monkeypatch.setattr(app.state.arq, "enqueue_job", _boom)
    response = await client.post(
        "/v1/documents/async", headers=auth_headers, json={"text": "hello"}
    )
    assert response.status_code == 503  # honest failure, not a fake 202


async def test_worker_task_records_failure(app, monkeypatch):
    async def _boom(texts, settings):
        raise RuntimeError("embedding backend down")

    monkeypatch.setattr("forge.rag.ingest.embed_texts", _boom)
    session_factory = app.state.db_session_factory
    job_id = uuid.uuid4()
    await create_job(session_factory, job_id=job_id, team="admin", api_key_hash="h", title=None)

    with pytest.raises(RuntimeError):  # arq sees the raise and applies its retry policy
        await ingest_job(
            _worker_ctx(app),
            job_id=str(job_id),
            text="anything",
            title=None,
            team="admin",
            api_key_hash="h",
            chunking=None,
            scrub=True,
        )
    job = await get_job(session_factory, job_id, "admin")
    assert job.status == FAILED
    assert "embedding backend down" in job.error
