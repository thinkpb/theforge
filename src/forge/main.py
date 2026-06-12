from contextlib import asynccontextmanager

from fastapi import FastAPI

from forge import __version__
from forge.api import audit, chat, health, keys
from forge.audit import AuditBuffer
from forge.config import get_settings
from forge.db import create_engine_and_factory
from forge.pii import PIIScrubber


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
    )
    yield
    await buffer.stop()
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
    return app


app = create_app()
