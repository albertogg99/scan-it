import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.jwt import current_user
from app.main import app
from app.persistence.session import get_session


@pytest.fixture
async def api(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    from app.persistence.models import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    user = {"sub": "alice"}

    async def session_override():
        async with factory() as session:
            yield session

    async def user_override():
        return user["sub"]

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = user_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, factory, user
    app.dependency_overrides.clear()
    await engine.dispose()