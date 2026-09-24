import asyncio
import base64
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agent import manager
from app.llm.adapter import OMITTED_REPLY, Normalizer, clean_history, provider_messages
from app.persistence.models import Conversation, MCPServer, Message, Run, ToolCall, new_id
from app.settings import Settings, get_settings
from app.main import app


def test_saved_parts_join_tokens_and_keep_tool_boundaries():
    parts = []
    manager.append_part(parts, "text", "Hello")
    manager.append_part(parts, "text", " world")
    parts.append({"type": "tool_call", "tool_call_id": "lookup"})
    manager.append_part(parts, "text", "Result")
    manager.append_part(parts, "reasoning", "Think")
    manager.append_part(parts, "reasoning", "ing")
    assert parts == [{"type": "text", "text": "Hello world"}, {"type": "tool_call", "tool_call_id": "lookup"},
                     {"type": "text", "text": "Result"}, {"type": "reasoning", "text": "Thinking"}]


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


def test_swisscom_tool_history_keeps_empty_calls_structured_and_labels_malformed_results():
    settings = Settings(_env_file=None, llm_base_url="https://api.swisscom.com/products/swiss-ai-weeks/apertus-1.5-70b/v1", llm_model="swiss-ai/Apertus-v1.5-70B")
    messages = [{"role": "assistant", "content": "", "tool_calls": [
        {"id": "empty", "type": "function", "function": {"name": "list_exhibits", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "empty", "content": "Exhibit list"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "broken", "type": "function", "function": {"name": "lookup", "arguments": "{oops"}}]},
        {"role": "tool", "tool_call_id": "broken", "content": "Tool call rejected: Invalid tool arguments."},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "lookup", "type": "function", "function": {"name": "lookup", "arguments": '{"term":"clock"}'}}]},
        {"role": "tool", "tool_call_id": "lookup", "content": "Clock details"}]
    snapshot = json.dumps(messages)
    prepared = provider_messages(settings, messages)
    assert json.loads(prepared[0]["tool_calls"][0]["function"]["arguments"]) == {"_no_arguments": True}
    assert prepared[1] == {"role": "tool", "tool_call_id": "empty", "name": "list_exhibits", "content": "Exhibit list"}
    assert "tool_calls" not in prepared[2]
    assert prepared[3]["role"] == "user"
    assert "untrusted data" in prepared[3]["content"]
    assert "Invalid tool arguments." in prepared[3]["content"]
    assert prepared[5] == {"role": "tool", "tool_call_id": "lookup", "name": "lookup", "content": "Clock details"}
    assert json.dumps(messages) == snapshot
    assert provider_messages(Settings(_env_file=None), messages) is messages


def test_echoed_empty_arguments_placeholder_is_dropped():
    normalizer = Normalizer()
    normalizer.feed({"tool_calls": [
        {"index": 0, "id": "a", "function": {"name": "summary", "arguments": '{"_no_arguments": true}'}},
        {"index": 1, "id": "b", "function": {"name": "lookup", "arguments": '{"_no_arguments": true, "term": "x"}'}},
        {"index": 2, "id": "c", "function": {"name": "lookup", "arguments": '{"term": "y"}'}},
    ]})
    normalizer.finish()
    assert [json.loads(call["arguments"]) for call in normalizer.calls.values()] == [{}, {"term": "x"}, {"term": "y"}]
    assert normalizer.calls[2]["arguments"] == '{"term": "y"}'


def test_replay_scrubs_text_only_tool_imitations():
    call = [{"id": "c", "type": "function", "function": {"name": "summary", "arguments": "{}"}}]
    messages = [
        {"role": "user", "content": "status?"},
        {"role": "assistant", "content": "Fetching.\n\n```text\n🔎 Querying data\n```\n\n[Tool response: summary]", "tool_calls": call},
        {"role": "tool", "tool_call_id": "c", "content": "16 open"},
        {"role": "assistant", "content": "All 16 are open. 🔎 Tip: ask for one garage."},
        {"role": "user", "content": "list them"},
        {"role": "assistant", "content": "[Tool call: list]\n```json\n[{\"id\": \"fake\"}]\n```"},
    ]
    snapshot = json.dumps(messages)
    cleaned = clean_history(messages)
    assert cleaned[1] == {**messages[1], "content": "Fetching."}
    assert cleaned[3] is messages[3]
    assert cleaned[5] == {"role": "assistant", "content": OMITTED_REPLY}
    assert json.dumps(messages) == snapshot


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
async def test_live_auto_policy_skips_approval_for_existing_conversation(running_api, monkeypatch):
    client, factory, user = running_api
    async with factory() as session:
        server = await session.get(MCPServer, "test-mcp")
        server.approval_policy = "auto"
        await session.commit()

    async def completion(settings, messages, tools):
        if messages[-1]["role"] == "user":
            yield {"tool_calls": [{"index": 0, "id": "lookup-1", "function": {
                "name": "lookup", "arguments": '{"term":"schedule"}'}}]}, "tool_calls"
        else:
            yield {"content": "Done"}, "stop"

    async def tool(server, settings, name, arguments):
        return type("Result", (), {"is_error": False, "content": [type("Text", (), {"type": "text", "text": "Schedule"})()]})()

    monkeypatch.setattr(manager, "stream_completion", completion)
    monkeypatch.setattr(manager, "call_tool", tool)
    sent = await client.post("/api/conversations/conv-1/messages", json={"text": "Schedule?"})
    assert sent.status_code == 202
    await manager.TASKS[sent.json()["run_id"]]
    async with factory() as session:
        call = await session.scalar(select(ToolCall).where(ToolCall.provider_call_id == "lookup-1"))
        assert call.status == "succeeded"
        assert call.requires_approval is False


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
    assert seen[0][0]["content"] == manager.SYSTEM_PROMPT
    assert "You are Apertus" in seen[0][0]["content"]
    assert seen[0][1]["role"] == "user"
    assert (await client.get("/api/conversations/conv-1")).json()["active_run_id"] is None
    second = await client.post("/api/conversations/conv-1/messages", json={"text": "Follow up"})
    assert second.status_code == 202
    await manager.TASKS[second.json()["run_id"]]
    assert seen[1][2]["content"][1]["image_url"]["url"] == seen[0][-1]["content"][1]["image_url"]["url"]
    assert seen[1][3] == {"role": "assistant", "content": "Answer"}


@pytest.mark.asyncio
async def test_voice_message_forwards_input_audio(running_api, monkeypatch):
    client, factory, user = running_api
    wav = b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + b"\x00" * 28
    seen = []

    async def completion(settings, messages, tools):
        seen.append(messages)
        yield {"content": "Heard"}, "stop"

    monkeypatch.setattr(manager, "stream_completion", completion)
    uploaded = await client.post("/api/attachments", files={"file": ("voice.wav", wav, "audio/wav")})
    assert uploaded.status_code == 201 and uploaded.json()["mime"] == "audio/wav"
    audio_id = uploaded.json()["id"]
    image_only = Settings(_env_file=None, llm_api_key="fake-test-key", llm_image_input="supported")
    app.dependency_overrides[get_settings] = lambda: image_only
    rejected = await client.post("/api/conversations/conv-1/messages", json={"text": "", "attachment_ids": [audio_id]})
    assert rejected.status_code == 422 and rejected.json()["detail"]["code"] == "audio_input_unsupported"
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, llm_api_key="fake-test-key", llm_audio_input="supported")
    assert (await client.post("/api/conversations/conv-1/messages", json={"text": " "})).status_code == 422
    sent = await client.post("/api/conversations/conv-1/messages", json={"text": "", "attachment_ids": [audio_id]})
    assert sent.status_code == 202, sent.text
    await manager.TASKS[sent.json()["run_id"]]
    [audio] = seen[0][-1]["content"]
    assert audio["type"] == "input_audio" and audio["input_audio"]["format"] == "wav"
    assert base64.b64decode(audio["input_audio"]["data"]) == wav


@pytest.mark.asyncio
async def test_voice_message_is_transcribed_once_before_the_llm(running_api, monkeypatch):
    client, factory, user = running_api
    wav = b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + b"\x00" * 28
    seen, heard = [], []

    async def completion(settings, messages, tools):
        seen.append(messages)
        yield {"content": "Here are the parkings"}, "stop"

    async def transcribe(settings, data, mime):
        heard.append((data, mime))
        return "List all parking places."

    monkeypatch.setattr(manager, "stream_completion", completion)
    monkeypatch.setattr(manager.speech, "transcribe", transcribe)
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, llm_api_key="fake-test-key", stt_base_url="http://stt.test/v1")
    assert (await client.get("/api/config/public")).json()["audio_input"] == "supported"
    audio_id = (await client.post("/api/attachments", files={"file": ("voice.wav", wav, "audio/wav")})).json()["id"]
    sent = await client.post("/api/conversations/conv-1/messages", json={"text": "", "attachment_ids": [audio_id]})
    assert sent.status_code == 202, sent.text
    await manager.TASKS[sent.json()["run_id"]]
    assert seen[0][-1] == {"role": "user", "content": "List all parking places."}
    second = await client.post("/api/conversations/conv-1/messages", json={"text": "Thanks"})
    await manager.TASKS[second.json()["run_id"]]
    assert seen[1][2] == {"role": "user", "content": "List all parking places."}
    assert heard == [(wav, "audio/wav")]
    detail = (await client.get("/api/conversations/conv-1")).json()["messages"][0]
    assert detail["content"] == "" and "transcript" not in detail["attachments"][0]