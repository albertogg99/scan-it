from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.api import conversations
from app.api.attachments import require_image_input
from app.persistence.models import Attachment, Conversation, MCPServer, Message, ToolCall, utcnow
from app.persistence.session import purge_unattached
from app.settings import Settings
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_user_isolation_snapshots_attachments_and_cascade(api, monkeypatch):
    client, factory, user = api
    async with factory() as session:
        session.add(MCPServer(
            id="museum-guide", title="Museum", description="Demo", backend_url="http://museum-guide:8000/mcp",
            auth_type="none", enabled=True, approval_policy="ask_destructive",
        ))
        await session.commit()

    async def discover(server, settings):
        return "Static context", "f" * 64, [{"name": "lookup", "description": "Find", "requires_approval": True, "annotations": {}, "input_schema": {}}]

    monkeypatch.setattr(conversations, "discover", discover)
    created = await client.post("/api/conversations", json={"mcp_id": "museum-guide"})
    assert created.status_code == 201, created.text
    data = created.json()
    conversation_id = data["id"]
    assert data["active_run_id"] is None
    assert data["tools"][0]["name"] == "lookup"
    png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR" + b"\x00" * 8
    upload = await client.post("/api/attachments", files={"file": ("image.png", png, "image/png")})
    assert upload.status_code == 201, upload.text
    attachment_id = upload.json()["id"]
    assert (await client.get(f"/api/attachments/{attachment_id}")).content == png
    assert (await client.post("/api/attachments", files={"file": ("wrong.png", b"not png", "image/png")})).status_code == 415

    async with factory() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation.context_snapshot == "Static context"
        assert conversation.context_sha256 == "f" * 64
        message = Message(conversation_id=conversation_id, seq=1, role="assistant", content="", status="completed")
        session.add(message)
        await session.flush()
        session.add(ToolCall(conversation_id=conversation_id, message_id=message.id, round=1, provider_call_id="call-1", name="lookup", arguments={}, arguments_raw="{}", requires_approval=True, status="succeeded"))
        attachment = await session.get(Attachment, attachment_id)
        attachment.conversation_id = conversation_id
        attachment.message_id = message.id
        await session.commit()

    assert len((await client.get(f"/api/conversations/{conversation_id}")).json()["messages"]) == 1
    user["sub"] = "bob"
    assert (await client.get("/api/conversations")).json() == []
    for path in (f"/api/conversations/{conversation_id}", f"/api/attachments/{attachment_id}"):
        assert (await client.get(path)).status_code == 404
    assert (await client.delete(f"/api/conversations/{conversation_id}")).status_code == 404
    user["sub"] = "alice"
    assert (await client.delete(f"/api/conversations/{conversation_id}")).status_code == 204
    async with factory() as session:
        for model in (Conversation, Message, ToolCall, Attachment):
            assert (await session.scalar(select(func.count()).select_from(model))) == 0


@pytest.mark.asyncio
async def test_unattached_expiration(api):
    _, factory, _ = api
    async with factory() as session:
        session.add_all([
            Attachment(user_sub="alice", mime="image/png", data=b"old", size=3, created_at=utcnow() - timedelta(hours=25)),
            Attachment(user_sub="alice", mime="image/png", data=b"new", size=3),
        ])
        await session.commit()
        assert await purge_unattached(session) == 1
        assert (await session.scalar(select(func.count()).select_from(Attachment))) == 1


def test_unverified_image_input_is_rejected():
    with pytest.raises(HTTPException) as exception:
        require_image_input(Settings(_env_file=None, llm_image_input="unknown"), ["upload-id"])
    assert exception.value.detail["code"] == "image_input_unsupported"