from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)


def create_engine_and_factory(database_url: str) -> tuple[AsyncEngine, async_sessionmaker]:
    engine = create_async_engine(database_url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)
