from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.mcp_adapter import client as adapter
from app.persistence.models import MCPServer
from app.settings import Settings


@pytest.mark.asyncio
async def test_missing_and_required_prompt(monkeypatch):
    fake = SimpleNamespace(list_prompts=None)

    @asynccontextmanager
    async def fake_connect(server, settings):
        yield fake

    monkeypatch.setattr(adapter, "connect", fake_connect)
    server = MCPServer(approval_policy="ask_destructive")
    settings = Settings(_env_file=None)

    async def no_prompts():
        return []

    fake.list_prompts = no_prompts
    with pytest.raises(HTTPException) as missing:
        await adapter.discover(server, settings)
    assert missing.value.detail["code"] == "context_prompt_missing"

    async def requires_argument():
        return [SimpleNamespace(name="general_context", arguments=[SimpleNamespace(required=True)])]

    fake.list_prompts = requires_argument
    with pytest.raises(HTTPException) as required:
        await adapter.discover(server, settings)
    assert required.value.detail["code"] == "context_prompt_requires_arguments"


@pytest.mark.asyncio
async def test_discovery_only_lists_never_invokes(monkeypatch):
    calls = []

    class FakeClient:
        async def list_prompts(self):
            return [SimpleNamespace(name="general_context", arguments=[])]

        async def get_prompt(self, name, arguments):
            calls.append((name, arguments))
            return SimpleNamespace(messages=[SimpleNamespace(content=SimpleNamespace(type="text", text="Original static context"))])

        async def list_tools(self):
            return [SimpleNamespace(name="lookup", description="Find", inputSchema={"type": "object"}, annotations=None)]

        async def call_tool(self, *args):
            raise AssertionError("must not call tools on entry or create")

    @asynccontextmanager
    async def fake_connect(server, settings):
        yield FakeClient()

    monkeypatch.setattr(adapter, "connect", fake_connect)
    context, digest, tools = await adapter.discover(MCPServer(approval_policy="ask_destructive"), Settings(_env_file=None))
    assert context == "Original static context"
    assert len(digest) == 64
    assert tools[0]["requires_approval"] is True
    assert calls == [("general_context", {})]