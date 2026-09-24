import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agent import manager
from app.llm.adapter import Normalizer
from app.persistence.models import Conversation, MCPServer, Message, Run, ToolCall, new_id
from app.settings import Settings, get_settings
from app.main import app


def test_markers_structured_and_same_index_fragments():
    normalizer = Normalizer()
    output = []
    for content in ["Start <|inn", "er_prefix|>thought<|inner_suf", "fix|> end"]:
        output.extend(normalizer.feed({"content": content}))
    output.extend(normalizer.finish())
    assert "".join(text for kind, text in output if kind == "text") == "Start  end"
    assert "".join(text for kind, text in output if kind == "reasoning") == "thought"
    normalizer.feed({"tool_calls": [
        {"index": 0, "id": "call-", "function": {"name": "look", "arguments": "{"}},
        {"index": 0, "id": "one", "function": {"name": "up", "arguments": "}"}},
        {"index": 1, "id": "two", "function": {"name": "other", "arguments": "{}"}},
    ]})
    assert list(normalizer.calls.values()) == [
        {"id": "call-one", "name": "lookup", "arguments": "{}"},
        {"id": "two", "name": "other", "arguments": "{}"},
    ]
    assert Normalizer().feed({"reasoning_content": "private", "content": "public"}) == [
        ("reasoning", "private"), ("text", "public")]


def test_real_swisscom_tool_probe_assembly():
    normalizer = Normalizer()
    sample = Path(__file__).resolve().parents[2] / "probe-samples" / "tool.jsonl"
    for line in sample.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        for choice in record.get("choices", []):
            normalizer.feed(choice["delta"], choice.get("finish_reason"))
    assert normalizer.finish_reason == "tool_calls"
    assert normalizer.calls[0]["name"] == "get_departures"
    assert json.loads(normalizer.calls[0]["arguments"]) == {"stop": "Zürich, Central", "limit": 10}


@pytest.fixture
async def running_api(api, monkeypatch):
    client, factory, user = api
    settings = Settings(_env_file=None, llm_api_key="fake-test-key", approval_timeout_s=3)
    app.dependency_overrides[get_settings] = lambda: settings
    async with factory() as session:
        session.add(MCPServer(id="test-mcp", title="Test", description="", backend_url="http://localhost/mcp",
                              auth_type="none", enabled=True, approval_policy="ask_always"))
        session.add(Conversation(id="conv-1", user_sub="alice", mcp_id="test-mcp", title="New conversation",
                                 context_snapshot="Untrusted text", context_sha256="x" * 64,
                                 tools_snapshot=[{"name": "lookup", "description": "", "input_schema": {"type": "object", "properties": {"term": {"type": "string"}}, "required": ["term"]}, "requires_approval": True}]))
        await session.commit()
    yield client, factory, user
    for task in list(manager.TASKS.values()):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    app.dependency_overrides.pop(get_settings, None)


async def pending(factory, run_id):
    for _ in range(200):
        async with factory() as session:
            call = await session.scalar(select(ToolCall).where(ToolCall.conversation_id == "conv-1"))
        if call:
            return call
        await asyncio.sleep(0.01)
    raise AssertionError("Tool call was not created")


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [True, False])
async def test_approval_denial_reconnect_and_replay(running_api, monkeypatch, approved):
    client, factory, user = running_api
    requests = []
    executions = []

    async def completion(settings, messages, tools):
        requests.append(messages)
        if len(requests) == 1:
            yield {"tool_calls": [{"index": 0, "id": "provider-1", "function": {"name": "lookup", "arguments": '{"term":"valid"}'}}]}, "tool_calls"
        else:
            yield {"content": "Done"}, "stop"

    async def tool(server, settings, name, arguments):
        executions.append(arguments)
        class Result:
            is_error = False
            content = [type("Text", (), {"type": "text", "text": "Real result"})()]
        return Result()

    monkeypatch.setattr(manager, "stream_completion", completion)
    monkeypatch.setattr(manager, "call_tool", tool)
    response = await client.post("/api/conversations/conv-1/messages", json={"text": "Question"})
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    assert (await client.delete("/api/conversations/conv-1")).status_code == 409
    call = await pending(factory, run_id)
    user["sub"] = "bob"
    assert (await client.get(f"/api/runs/{run_id}/events")).status_code == 404
    assert (await client.post(f"/api/runs/{run_id}/cancel")).status_code == 404
    assert (await client.post(f"/api/tool-calls/{call.id}/decision", json={"approved": True})).status_code == 404
    user["sub"] = "alice"
    decided = await client.post(f"/api/tool-calls/{call.id}/decision", json={"approved": approved})
    assert decided.status_code == 200, decided.text
    assert (await client.post(f"/api/tool-calls/{call.id}/decision", json={"approved": approved})).status_code == 409
    await manager.TASKS[run_id]
    response = await client.get(f"/api/runs/{run_id}/events")
    assert response.status_code == 200
    assert response.text.count("event: tool.running") == int(approved)
    assert len(executions) == int(approved)
    assert "event: run.completed" in response.text
    first_seq = int(response.text.splitlines()[0].removeprefix("id: "))
    resumed = await client.get(f"/api/runs/{run_id}/events?after={first_seq}")
    assert "event: run.started" not in resumed.text
    assert "event: run.completed" in resumed.text
    assert len(requests) == 2 and len(executions) == int(approved)
    async with factory() as session:
        assistant = await session.get(Message, (await session.get(Run, run_id)).assistant_message_id)
        assert assistant.llm_messages == [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "provider-1", "type": "function", "function": {"name": "lookup", "arguments": '{"term":"valid"}'}}]},
            {"role": "tool", "tool_call_id": "provider-1", "content": "Real result" if approved else "The user denied this tool call."},
            {"role": "assistant", "content": "Done"},
        ]


@pytest.mark.asyncio
async def test_invalid_arguments_cancel_and_images(running_api, monkeypatch):
    client, factory, user = running_api
    invoked = []

    async def completion(settings, messages, tools):
        invoked.append(messages)
        if len(invoked) == 1:
            yield {"tool_calls": [{"index": 0, "id": "bad", "function": {"name": "lookup", "arguments": "{"}}]}, "tool_calls"
        else:
            yield {"content": "Recovered"}, "stop"

    monkeypatch.setattr(manager, "stream_completion", completion)
    response = await client.post("/api/conversations/conv-1/messages", json={"text": "Q", "attachment_ids": ["unknown"]})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "image_input_unsupported"
    sent = await client.post("/api/conversations/conv-1/messages", json={"text": "Q"})
    run_id = sent.json()["run_id"]
    await manager.TASKS[run_id]
    async with factory() as session:
        call = await session.scalar(select(ToolCall).where(ToolCall.provider_call_id == "bad"))
        assert call.status == "failed" and call.arguments is None
    assert invoked[1][-1] == {"role": "tool", "tool_call_id": "bad", "content": "Tool call rejected: Invalid tool arguments."}


@pytest.mark.asyncio
async def test_cancel_pending_and_restart_recovery(running_api, monkeypatch):
    client, factory, user = running_api

    async def completion(settings, messages, tools):
        yield {"tool_calls": [{"index": 0, "id": "pending", "function": {"name": "lookup", "arguments": '{"term":"x"}'}}]}, "tool_calls"

    monkeypatch.setattr(manager, "stream_completion", completion)
    sent = await client.post("/api/conversations/conv-1/messages", json={"text": "Q"})
    run_id = sent.json()["run_id"]
    call = await pending(factory, run_id)
    assert (await client.post(f"/api/runs/{run_id}/cancel")).status_code == 202
    task = manager.TASKS.get(run_id)
    if task:
        await task
    async with factory() as session:
        assert (await session.get(Run, run_id)).status == "cancelled"
        assert (await session.get(ToolCall, call.id)).status == "failed"
        conversation = await session.get(Conversation, "conv-1")
        assistant_id = new_id()
        session.add(Message(id=assistant_id, conversation_id="conv-1", seq=3, role="assistant", content="Partial", status="streaming"))
        await session.flush()
        stale_id = new_id()
        session.add(Run(id=stale_id, conversation_id="conv-1", user_sub="alice", user_message_id=sent.json()["user_message_id"],
                        assistant_message_id=assistant_id, status="running"))
        session.add(ToolCall(conversation_id="conv-1", message_id=assistant_id, round=1, provider_call_id="stale",
                             name="lookup", arguments={}, arguments_raw="{}", requires_approval=True, status="pending_approval"))
        conversation.active_run_id = stale_id
        await session.commit()
    await manager.reconciliate(factory)
    async with factory() as session:
        assert (await session.get(Conversation, "conv-1")).active_run_id is None
        assert (await session.get(Run, stale_id)).status == "failed"
        assert (await session.get(Message, assistant_id)).content == "Partial"
        assert (await session.scalar(select(ToolCall.status).where(ToolCall.provider_call_id == "stale"))) == "failed"


@pytest.mark.asyncio
async def test_supported_image_data_urls_in_history(running_api, monkeypatch):
    client, factory, user = running_api
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, llm_api_key="fake-test-key", llm_image_input="supported")
    seen = []

    async def completion(settings, messages, tools):
        seen.append(messages)
        yield {"content": "Answer"}, "stop"

    monkeypatch.setattr(manager, "stream_completion", completion)
    image = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR" + b"\x00" * 8
    uploaded = await client.post("/api/attachments", files={"file": ("small.png", image, "image/png")})
    assert uploaded.status_code == 201
    sent = await client.post("/api/conversations/conv-1/messages", json={"text": "Describe", "attachment_ids": [uploaded.json()["id"]]})
    assert sent.status_code == 202, sent.text
    await manager.TASKS[sent.json()["run_id"]]
    assert seen[0][-1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert seen[0][0]["role"] == "system"
    assert seen[0][1]["role"] == "user"
    assert (await client.get("/api/conversations/conv-1")).json()["active_run_id"] is None
    second = await client.post("/api/conversations/conv-1/messages", json={"text": "Follow up"})
    assert second.status_code == 202
    await manager.TASKS[second.json()["run_id"]]
    assert seen[1][2]["content"][1]["image_url"]["url"] == seen[0][-1]["content"][1]["image_url"]["url"]
    assert seen[1][3] == {"role": "assistant", "content": "Answer"}