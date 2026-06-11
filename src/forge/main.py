from fastapi import FastAPI

from forge import __version__
from forge.api import chat, health


def create_app() -> FastAPI:
    app = FastAPI(
        title="Forge Gateway",
        description="Self-hostable LLM gateway for regulated industries",
        version=__version__,
    )
    app.include_router(health.router)
    app.include_router(chat.router)
    return app


app = create_app()
