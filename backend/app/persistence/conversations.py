from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.persistence.models import Conversation, Message
from app.registry.service import error


async def owned_conversation(session: AsyncSession, conversation_id: str, user_sub: str) -> Conversation:
    statement = (
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.user_sub == user_sub)
        .options(
            selectinload(Conversation.server),
            selectinload(Conversation.messages).selectinload(Message.attachments),
            selectinload(Conversation.messages).selectinload(Message.tool_calls),
        )
    )
    result = await session.execute(statement)
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise error(404, "conversation_not_found", "Conversation not found.")
    return conversation