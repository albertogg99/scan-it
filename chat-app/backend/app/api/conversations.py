from fastapi import APIRouter, Depends, Response
from sqlalchemy import update
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.jwt import current_user
from app.mcp_adapter.client import discover
from app.persistence.conversations import owned_conversation
from app.persistence.models import Conversation
from app.persistence.session import get_session
from app.registry.service import resolve_server
from app.settings import Settings, get_settings
from app.registry.service import error

router = APIRouter(prefix="/api/conversations")


class CreateConversation(BaseModel):
    mcp_id: str


def summary(conversation: Conversation) -> dict:
    return {
        "id": conversation.id, "mcp_id": conversation.mcp_id,
        "mcp_title": conversation.server.title, "title": conversation.title,
        "created_at": conversation.created_at, "updated_at": conversation.updated_at,
    }


def detail(conversation: Conversation) -> dict:
    messages = []
    for message in conversation.messages:
        messages.append({
            "id": message.id, "role": message.role, "content": message.content,
            "reasoning": message.reasoning, "parts": message.parts, "status": message.status,
            "error": message.error, "created_at": message.created_at,
            "attachments": [{"id": item.id, "mime": item.mime, "size": item.size} for item in message.attachments],
            "tool_calls": [{
                "id": item.id, "name": item.name, "arguments": item.arguments,
                "arguments_raw": item.arguments_raw, "status": item.status,
                "requires_approval": item.requires_approval, "result": item.result,
                "truncated": item.truncated, "error": item.error,
            } for item in message.tool_calls],
        })
    return {
        **summary(conversation), "approval_policy": conversation.server.approval_policy,
        "tools": [{"name": tool["name"], "description": tool["description"], "requires_approval": tool["requires_approval"]} for tool in conversation.tools_snapshot],
        "messages": messages, "active_run_id": conversation.active_run_id,
    }


@router.get("")
async def list_conversations(user_sub: str = Depends(current_user), session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(Conversation).where(Conversation.user_sub == user_sub)
        .options(selectinload(Conversation.server))
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
    )
    return [summary(item) for item in result.scalars()]


@router.post("", status_code=201)
async def create_conversation(
    request: CreateConversation,
    user_sub: str = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    server = await resolve_server(session, request.mcp_id, settings)
    context, context_hash, tools = await discover(server, settings)
    conversation = Conversation(
        user_sub=user_sub, mcp_id=server.id, server=server,
        context_snapshot=context, context_sha256=context_hash, tools_snapshot=tools,
        messages=[],
    )
    session.add(conversation)
    await session.commit()
    return detail(conversation)


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: str, user_sub: str = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return detail(await owned_conversation(session, conversation_id, user_sub))


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str, user_sub: str = Depends(current_user), session: AsyncSession = Depends(get_session)):
    conversation = await owned_conversation(session, conversation_id, user_sub)
    if conversation.active_run_id:
        raise error(409, "run_in_progress", "Cancel the active run before deleting this conversation.")
    await session.delete(conversation)
    await session.commit()
    return Response(status_code=204)