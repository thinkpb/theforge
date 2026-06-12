from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis import asyncio as aioredis

from forge import __version__
from forge.api import audit, chat, costs, health, keys, rag
from forge.audit import AuditBuffer
from forge.config import get_settings
from forge.db import create_engine_and_factory
from forge.pii import PIIScrubber
from forge.rag.store import VectorStore
from forge.ratelimit import RateLimiter


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings = get_settings()
    engine, session_factory = create_engine_and_factory(settings.database_url)
    buffer = AuditBuffer(
        session_factory,
        maxsize=settings.audit_queue_size,
        flush_batch=settings.audit_flush_batch,
    )
    buffer.start()
    app.state.db_engine = engine
    app.state.db_session_factory = session_factory
    app.state.audit_buffer = buffer
    app.state.pii_scrubber = PIIScrubber(
        enabled=settings.pii_scrubbing_enabled,
        allow_list=settings.pii_allow_list,
        entities=settings.pii_entities,
    )
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    app.state.rate_limiter = RateLimiter(
        redis,
        rpm=settings.rate_limit_rpm,
        tpm=settings.rate_limit_tpm,
        enabled=settings.rate_limit_enabled,
    )
    app.state.vector_store = VectorStore(settings.qdrant_url)
    yield
    await buffer.stop()
    await app.state.vector_store.close()
    await redis.aclose()
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Forge Gateway",
        description="Self-hostable LLM gateway for regulated industries",
        version=__version__,
        lifespan=_lifespan,
    )
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(audit.router)
    app.include_router(keys.router)
    app.include_router(costs.router)
    app.include_router(rag.router)
    return app


app = create_app()
