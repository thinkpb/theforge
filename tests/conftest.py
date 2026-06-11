import httpx
import pytest

from forge.config import get_settings
from forge.main import create_app

TEST_KEY = "test-master-key"


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setenv("FORGE_MASTER_KEY", TEST_KEY)
    get_settings.cache_clear()
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    get_settings.cache_clear()


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {TEST_KEY}"}
