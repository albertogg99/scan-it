import asyncio
import json

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import manager
from app.api.attachments import require_image_input, require_media_input
from app.auth.jwt import current_user
from app.persistence.conversations import owned_conversation
from app.persistence.models import Attachment, Conversation, Message, Run, RunEvent, ToolCall, new_id, utcnow
from app.persistence.session import get_session
from app.registry.service import error
from app.settings import Settings, get_settings

router = APIRouter()


class SendMessage(BaseModel):
    text: str
    attachment_ids: list[str] = []


async def owned_run(session, run_id, user_sub):
    run = await session.scalar(select(Run).where(Run.id == run_id, Run.user_sub == user_sub))
    if run is None:
        raise error(404, "run_not_found", "Run not found.")
    return run


@router.post("/api/conversations/{conversation_id}/messages", status_code=202)
async def send_message(conversation_id: str, request: SendMessage, user_sub: str = Depends(current_user),
                       session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    conversation = await owned_conversation(session, conversation_id, user_sub)
    if len(request.text) > settings.max_message_chars or len(set(request.attachment_ids)) != len(request.attachment_ids):
        raise error(422, "message_invalid", "Message or attachments are invalid.")
    if not settings.api_key:
        raise error(503, "llm_not_configured", "Language model is not configured.")
    require_image_input(settings, request.attachment_ids)
    attachments = (await session.scalars(select(Attachment).where(Attachment.id.in_(request.attachment_ids),
        Attachment.user_sub == user_sub, Attachment.conversation_id.is_(None), Attachment.message_id.is_(None)))).all()
    voice = any(item.mime.startswith("audio/") for item in attachments)
    if len(attachments) != len(request.attachment_ids) or not (request.text.strip() or voice):
        raise error(422, "message_invalid", "Message or attachments are invalid.")
    require_media_input(settings, attachments)
    run_id, user_id, assistant_id = new_id(), new_id(), new_id()
    locked = await session.execute(update(Conversation).where(Conversation.id == conversation_id,
        Conversation.user_sub == user_sub, Conversation.active_run_id.is_(None)).values(active_run_id=run_id))
    if locked.rowcount != 1:
        await session.rollback()
        raise error(409, "run_in_progress", "A run is already active.")
    sequence = (await session.scalar(select(func.max(Message.seq)).where(Message.conversation_id == conversation_id)) or 0) + 1
    session.add_all([
        Message(id=user_id, conversation_id=conversation_id, seq=sequence, role="user", content=request.text, status="completed"),
        Message(id=assistant_id, conversation_id=conversation_id, seq=sequence + 1, role="assistant", content="", status="streaming", parts=[], llm_messages=[]),
    ])
    await session.flush()
    session.add(Run(id=run_id, conversation_id=conversation_id, user_sub=user_sub,
                    user_message_id=user_id, assistant_message_id=assistant_id, status="running"))
    for attachment in attachments:
        attachment.conversation_id, attachment.message_id = conversation_id, user_id
    if conversation.title == "New conversation":
        conversation.title = request.text.strip()[:60] or "Voice message"
    await session.commit()
    manager.start(async_sessionmaker(session.bind, expire_on_commit=False), settings, run_id)
    return {"run_id": run_id, "user_message_id": user_id, "assistant_message_id": assistant_id}


@router.get("/api/runs/{run_id}/events")
async def events(run_id: str, after: int = Query(0, ge=0), user_sub: str = Depends(current_user),
                 session: AsyncSession = Depends(get_session)):
    await owned_run(session, run_id, user_sub)
    earliest = await session.scalar(select(func.min(RunEvent.seq)).where(RunEvent.run_id == run_id))
    if earliest is not None and after < earliest - 1:
        raise error(410, "event_cursor_expired", "The requested events are no longer available.")
    factory = async_sessionmaker(session.bind, expire_on_commit=False)

    async def stream():
        sequence = after
        next_keepalive = asyncio.get_running_loop().time() + 15
        while True:
            async with factory() as reader:
                rows = (await reader.scalars(select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.seq > sequence)
                    .order_by(RunEvent.seq).limit(512))).all()
                status = await reader.scalar(select(Run.status).where(Run.id == run_id))
            for row in rows:
                data = row.data
                sequence = row.seq
                yield f"id: {sequence}\nevent: {data['type']}\ndata: {json.dumps(data)}\n\n"
            if status in manager.TERMINAL and any(row.data["type"] in {"run.completed", "run.failed", "run.cancelled"} for row in rows):
                break
            if status in manager.TERMINAL and not rows:
                async with factory() as reader:
                    last = await reader.scalar(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq.desc()).limit(1))
                if last and last.data["type"] in {"run.completed", "run.failed", "run.cancelled"}:
                    break
            if not rows:
                if asyncio.get_running_loop().time() >= next_keepalive:
                    yield ": keepalive\n\n"
                    next_keepalive = asyncio.get_running_loop().time() + 15
                await asyncio.sleep(0.25)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.post("/api/runs/{run_id}/cancel", status_code=202)
async def cancel(run_id: str, user_sub: str = Depends(current_user), session: AsyncSession = Depends(get_session)):
    run = await owned_run(session, run_id, user_sub)
    if run.status == "running":
        run.cancel_requested = True
        await session.commit()
    return {"run_id": run_id}


class Decision(BaseModel):
    approved: bool


@router.post("/api/tool-calls/{tool_id}/decision")
async def decide(tool_id: str, request: Decision, user_sub: str = Depends(current_user), session: AsyncSession = Depends(get_session)):
    call = await session.scalar(select(ToolCall).join(Run, Run.assistant_message_id == ToolCall.message_id).where(
        ToolCall.id == tool_id, Run.user_sub == user_sub, Run.status == "running"))
    if not call:
        raise error(404, "tool_call_not_found", "Tool call not found.")
    status = "approved" if request.approved else "denied"
    changed = await session.execute(update(ToolCall).where(ToolCall.id == tool_id,
        ToolCall.status == "pending_approval").values(status=status, decided_at=utcnow()))
    if changed.rowcount != 1:
        await session.rollback()
        raise error(409, "not_pending", "Tool call is no longer pending approval.")
    await session.commit()
    run = await session.scalar(select(Run).where(Run.assistant_message_id == call.message_id))
    await manager.emit(async_sessionmaker(session.bind, expire_on_commit=False), run.id,
                       "approval.decided", tool_call_id=tool_id, approved=request.approved)
    return {"id": call.id, "name": call.name, "arguments": call.arguments,
            "arguments_raw": call.arguments_raw, "status": status, "requires_approval": True,
            "result": call.result, "truncated": call.truncated, "error": call.error}