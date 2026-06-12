import asyncio
import uuid

import asyncpg
import httpx
import litellm
import pytest
from qdrant_client import AsyncQdrantClient
from sqlalchemy import text

from forge.audit import APPEND_ONLY_DDL
from forge.config import get_settings
from forge.db import Base, create_engine_and_factory
from forge.main import create_app

TEST_KEY = "test-master-key"
TEST_DB_URL = "postgresql+asyncpg://forge:forge@localhost:5432/forge_test"
TEST_REDIS_URL = "redis://localhost:6379/9"  # dedicated test DB, away from dev


def set_test_env(monkeypatch):
    monkeypatch.setenv("FORGE_MASTER_KEY", TEST_KEY)
    monkeypatch.setenv("FORGE_DATABASE_URL", TEST_DB_URL)
    monkeypatch.setenv("FORGE_REDIS_URL", TEST_REDIS_URL)


@pytest.fixture(scope="session")
def _test_database():
    """Create the forge_test database once per session (sync fixture, own loop)."""

    async def _ensure():
        conn = await asyncpg.connect(
            user="forge", password="forge", host="localhost", port=5432, database="forge"
        )
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = 'forge_test'")
        if not exists:
            await conn.execute("CREATE DATABASE forge_test")
        await conn.close()

    asyncio.run(_ensure())


@pytest.fixture
async def db_engine(_test_database):
    """Fresh audit schema (table + append-only trigger) per test."""
    engine, _ = create_engine_and_factory(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        for statement in APPEND_ONLY_DDL:
            await conn.execute(text(statement))
    yield engine
    await engine.dispose()


@pytest.fixture
async def app(monkeypatch, db_engine):
    set_test_env(monkeypatch)
    # unique qdrant collection prefix per test → isolation without flushes
    prefix = f"t{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("FORGE_QDRANT_COLLECTION_PREFIX", prefix)
    get_settings.cache_clear()
    application = create_app()
    # httpx's ASGITransport doesn't run startup/shutdown; drive the lifespan
    # ourselves so the audit buffer and engine exist like in production.
    async with application.router.lifespan_context(application):
        yield application
    get_settings.cache_clear()
    try:
        qdrant = AsyncQdrantClient(url="http://localhost:6333")
        for collection in (await qdrant.get_collections()).collections:
            if collection.name.startswith(f"{prefix}_"):
                await qdrant.delete_collection(collection.name)
        await qdrant.close()
    except Exception:
        pass  # qdrant not running and test didn't need it


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {TEST_KEY}"}


class FakeResponse:
    def __init__(self, model: str):
        self._model = model

    def model_dump(self):
        return {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "model": self._model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "hello"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
        }


@pytest.fixture
def fake_completion(monkeypatch):
    """Replace litellm.acompletion; returns the kwargs the router sent."""
    calls = {}

    async def _fake(**kwargs):
        calls.update(kwargs)
        return FakeResponse(kwargs["model"])

    monkeypatch.setattr("forge.gateway.router.litellm.acompletion", _fake)
    return calls


def make_litellm_exc(exc_type: type[Exception]) -> Exception:
    kwargs = {"message": "boom", "llm_provider": "openai", "model": "gpt-4o"}
    if exc_type is litellm.exceptions.PermissionDeniedError:
        request = httpx.Request("POST", "http://upstream.test")
        kwargs["response"] = httpx.Response(403, request=request)
    return exc_type(**kwargs)
