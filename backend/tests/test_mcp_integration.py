import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastmcp import Client, FastMCP
from mcp.types import ToolAnnotations
from sqlalchemy import delete, select

from app.agent import manager
from app.auth import jwt as authentication
from app.main import app
from app.mcp_adapter import client as mcp_client
from app.persistence.models import Conversation, MCPServer, Run, RunEvent, ToolCall
from app.settings import Settings, get_settings


def build_server() -> FastMCP:
    server = FastMCP("Example MCP", instructions="The item catalog is static sample content.")

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
    def item_details(item_id: str) -> dict:
        """Retrieve one item from the static catalog."""
        return {"status": "ok", "id": item_id, "description": "A sample item used for integration tests."}

    return server


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [True, False])
async def test_signed_identity_conversation_and_bounded_replay(api, monkeypatch, approved):
    client, factory, _ = api
    app.dependency_overrides.pop(authentication.current_user)
    settings = Settings(_env_file=None, llm_api_key="local-test-only", approval_timeout_s=4)
    app.dependency_overrides[get_settings] = lambda: settings
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    monkeypatch.setattr(authentication.validator, "_keys", {"local-key": {**jwk, "kid": "local-key"}})
    monkeypatch.setattr(authentication.validator, "_expires_at", float("inf"))

    def authorization(subject):
        token = jwt.encode({"sub": subject, "iss": settings.oidc_issuer, "aud": settings.oidc_audience,
                            "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
                           private_key, algorithm="RS256", headers={"kid": "local-key"})
        return {"Authorization": f"Bearer {token}"}

    example = build_server()

    @asynccontextmanager
    async def in_process_server(server, settings):
        assert server.backend_url == "http://example-mcp:8000/mcp"
        async with Client(example) as session:
            yield session

    monkeypatch.setattr(mcp_client, "connect", in_process_server)
    executions = []

    async def actual_tool(server, settings, name, arguments):
        executions.append((name, arguments))
        return await mcp_client.call_tool(server, settings, name, arguments)

    monkeypatch.setattr(manager, "call_tool", actual_tool)
    requests = []

    async def model_stream(settings, messages, tools):
        requests.append((messages, tools))
        if len(requests) == 1:
            yield {"tool_calls": [{"index": 0, "id": "example-call", "function": {
                "name": "item_details", "arguments": '{"item_id":"sample"}'}}]}, "tool_calls"
        else:
            yield {"content": "The sample item is ready."}, "stop"

    monkeypatch.setattr(manager, "stream_completion", model_stream)
    async with factory() as session:
        session.add(MCPServer(id="example-mcp", title="Example MCP", description="Test",
                              backend_url="http://example-mcp:8000/mcp", auth_type="none",
                              enabled=True, approval_policy="ask_always"))
        await session.commit()

    alice, bob = authorization("alice"), authorization("bob")
    assert (await client.get("/api/conversations", headers={})).status_code == 401
    signed = alice["Authorization"]
    forged = {"Authorization": signed[:-8] + ("a" if signed[-8] != "a" else "b") + signed[-7:]}
    assert (await client.get("/api/conversations", headers=forged)).status_code == 401
    assert (await client.get("/api/public/mcps/example-mcp", headers=alice)).status_code == 200
    assert [item["id"] for item in (await client.get("/api/public/mcps")).json()] == ["example-mcp"]
    created = await client.post("/api/conversations", json={"mcp_id": "example-mcp"}, headers=alice)
    assert created.status_code == 201, created.text
    conversation = created.json()
    conversation_id = conversation["id"]
    async with factory() as session:
        assert "static sample content" in (await session.get(Conversation, conversation_id)).context_snapshot
    assert any(tool["name"] == "item_details" and tool["requires_approval"] for tool in conversation["tools"])
    assert (await client.get(f"/api/conversations/{conversation_id}", headers=bob)).status_code == 404
    sent = await client.post(f"/api/conversations/{conversation_id}/messages", json={"text": "Tell me about the sample"}, headers=alice)
    assert sent.status_code == 202, sent.text
    run_id = sent.json()["run_id"]

    async with asyncio.timeout(4):
        while True:
            async with factory() as session:
                call = await session.scalar(select(ToolCall).where(ToolCall.conversation_id == conversation_id))
            if call:
                break
            await asyncio.sleep(0.02)
    assert call.status == "pending_approval" and call.arguments == {"item_id": "sample"}
    assert "static sample content" in requests[0][0][1]["content"]
    assert any(tool["function"]["name"] == "item_details" for tool in requests[0][1])
    assert (await client.get(f"/api/runs/{run_id}/events", headers=bob)).status_code == 404
    assert (await client.post(f"/api/tool-calls/{call.id}/decision", json={"approved": True}, headers=bob)).status_code == 404
    decision = await client.post(f"/api/tool-calls/{call.id}/decision", json={"approved": approved}, headers=alice)
    assert decision.status_code == 200, decision.text
    await manager.TASKS[run_id]
    assert (await client.get("/api/conversations", headers=bob)).json() == []
    assert (await client.post(f"/api/conversations/{conversation_id}/messages", json={"text": "Hello"}, headers=bob)).status_code == 404

    event_response = await client.get(f"/api/runs/{run_id}/events", headers=alice)
    assert event_response.status_code == 200
    lines = event_response.text.splitlines()
    events = [json.loads(line[6:]) for line in lines if line.startswith("data: ")]
    assert events[0]["type"] == "run.started"
    assert [event["type"] for event in events][-1] == "run.completed"
    assert any(event["type"] == "approval.pending" for event in events)
    assert sum(event["type"] == "tool.running" for event in events) == int(approved)
    assert executions == ([("item_details", {"item_id": "sample"})] if approved else [])
    after = events[0]["seq"]
    replay = await client.get(f"/api/runs/{run_id}/events?after={after}", headers=alice)
    assert "event: run.started" not in replay.text and "event: run.completed" in replay.text
    stale = await client.get(f"/api/runs/{run_id}/events?after=999999", headers=alice)
    assert stale.status_code == 200 and stale.text == ""
    async with factory() as session:
        await session.execute(delete(RunEvent).where(RunEvent.run_id == run_id, RunEvent.seq <= after))
        await session.commit()
    expired = await client.get(f"/api/runs/{run_id}/events?after=0", headers=alice)
    assert expired.status_code == 410 and expired.json()["detail"]["code"] == "event_cursor_expired"
    assert len(executions) == int(approved)
    assert len(requests) == 2
    if approved:
        assert "A sample item used for integration tests." in requests[1][0][-1]["content"]
    else:
        assert requests[1][0][-1]["content"] == "The user denied this tool call."
    detail = (await client.get(f"/api/conversations/{conversation_id}", headers=alice)).json()
    assert [message["role"] for message in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["content"] == "The sample item is ready."
    assert detail["messages"][1]["tool_calls"][0]["status"] == ("succeeded" if approved else "denied")
    assert detail["active_run_id"] is None
    async with factory() as session:
        assert (await session.get(Run, run_id)).status == "completed"
