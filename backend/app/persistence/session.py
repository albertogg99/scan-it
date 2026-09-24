from collections.abc import AsyncIterator
from datetime import timedelta

from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.persistence.models import Attachment, utcnow
from app.settings import get_settings

engine = create_async_engine(get_settings().database_url)
session_factory = async_sessionmaker(engine, expire_on_commit=False)

if engine.dialect.name == "sqlite":
    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(connection, connection_record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


async def purge_unattached(session: AsyncSession) -> int:
    result = await session.execute(
        delete(Attachment).where(
            Attachment.conversation_id.is_(None),
            Attachment.message_id.is_(None),
            Attachment.created_at < utcnow() - timedelta(hours=24),
        )
    )
    await session.commit()
    return result.rowcount