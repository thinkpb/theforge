async def test_models_requires_auth(client):
    response = await client.get("/v1/models")
    assert response.status_code == 401


async def test_models_rejects_wrong_key(client):
    response = await client.get("/v1/models", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


async def test_models_with_valid_key(client, auth_headers):
    response = await client.get("/v1/models", headers=auth_headers)
    assert response.status_code == 200
    ids = [m["id"] for m in response.json()["data"]]
    assert "claude-fable-5" in ids


async def test_chat_rejects_unknown_model(client, auth_headers):
    response = await client.post(
        "/v1/chat/completions",
        headers=auth_headers,
        json={"model": "not-a-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 400
    assert "Unknown model" in response.json()["detail"]
