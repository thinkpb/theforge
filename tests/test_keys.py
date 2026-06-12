"""API key lifecycle tests (ADR-0008)."""

from sqlalchemy import text


def _chat_body(model: str = "gpt-4o"):
    return {"model": model, "messages": [{"role": "user", "content": "hi"}]}


async def _create_key(client, auth_headers, **overrides):
    body = {"name": "ci-bot", "team": "platform", **overrides}
    response = await client.post("/v1/keys", headers=auth_headers, json=body)
    assert response.status_code == 201
    return response.json()


async def test_create_key_returns_secret_exactly_once(client, auth_headers, db_engine):
    created = await _create_key(client, auth_headers)
    assert created["key"].startswith("fsk_")
    assert created["key_prefix"] == created["key"][:12]
    assert created["team"] == "platform"

    # the raw key is never stored — only its hash
    async with db_engine.connect() as conn:
        (row,) = (await conn.execute(text("SELECT key_hash, key_prefix FROM api_keys"))).all()
    assert row.key_hash != created["key"]
    assert created["key"] not in row.key_hash

    # and list responses never include it
    listed = await client.get("/v1/keys", headers=auth_headers)
    (record,) = listed.json()["data"]
    assert "key" not in record


async def test_team_key_works_for_completions_and_is_audited(
    client, app, auth_headers, fake_completion
):
    created = await _create_key(client, auth_headers)
    team_headers = {"Authorization": f"Bearer {created['key']}"}

    response = await client.post(
        "/v1/chat/completions", headers=team_headers, json=_chat_body()
    )
    assert response.status_code == 200

    await app.state.audit_buffer.drain()
    audit = await client.get("/v1/audit", headers=auth_headers)
    (record,) = audit.json()["data"]
    # audit row attributes the request to the team key, not the master key
    assert record["api_key_hash"] != "master"
    assert len(record["api_key_hash"]) == 64


async def test_team_key_cannot_manage_keys_or_read_audit(client, auth_headers):
    created = await _create_key(client, auth_headers)
    team_headers = {"Authorization": f"Bearer {created['key']}"}

    assert (await client.get("/v1/keys", headers=team_headers)).status_code == 403
    assert (await client.get("/v1/audit", headers=team_headers)).status_code == 403
    body = {"name": "x", "team": "x"}
    assert (await client.post("/v1/keys", headers=team_headers, json=body)).status_code == 403


async def test_revoked_key_is_rejected_but_never_deleted(client, auth_headers, db_engine):
    created = await _create_key(client, auth_headers)
    team_headers = {"Authorization": f"Bearer {created['key']}"}

    revoked = await client.delete(f"/v1/keys/{created['id']}", headers=auth_headers)
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None

    response = await client.post("/v1/chat/completions", headers=team_headers, json=_chat_body())
    assert response.status_code == 401

    # revoke-not-delete: the row survives for audit attribution
    async with db_engine.connect() as conn:
        count = (await conn.execute(text("SELECT count(*) FROM api_keys"))).scalar()
    assert count == 1


async def test_unknown_and_garbage_keys_rejected(client):
    for token in ("fsk_does-not-exist", "not-even-prefixed"):
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json=_chat_body(),
        )
        assert response.status_code == 401


async def test_pii_opt_out_key_skips_scrubbing_visibly(
    client, app, auth_headers, fake_completion
):
    created = await _create_key(client, auth_headers, name="legacy-app", pii_opt_out=True)
    team_headers = {"Authorization": f"Bearer {created['key']}"}

    note = "Reach me at jane.doe@example.com"
    await client.post(
        "/v1/chat/completions",
        headers=team_headers,
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": note}]},
    )
    # opt-out: content passes through unscrubbed
    assert fake_completion["messages"][0]["content"] == note

    await app.state.audit_buffer.drain()
    audit = await client.get("/v1/audit", headers=auth_headers)
    (record,) = audit.json()["data"]
    assert record["pii_redactions"] is None  # the opt-out leaves its trace