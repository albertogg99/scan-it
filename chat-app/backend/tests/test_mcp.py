from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastmcp import Client, FastMCP

from app.mcp_adapter import client as adapter
from app.persistence.models import MCPServer
from app.settings import Settings


@pytest.mark.asyncio
@pytest.mark.parametrize("instructions", [None, "Server initialization context"])
async def test_initialize_instructions_with_real_fastmcp(monkeypatch, instructions):
    @asynccontextmanager
    async def fake_connect(server, settings):
        async with Client(FastMCP("test", instructions=instructions)) as client:
            yield client

    monkeypatch.setattr(adapter, "connect", fake_connect)
    server = MCPServer(approval_policy="ask_destructive")
    settings = Settings(_env_file=None)

    context, digest, tools = await adapter.discover(server, settings)
    assert context == (instructions or "")
    assert len(digest) == 64
    assert tools == []


@pytest.mark.asyncio
async def test_discovery_only_lists_never_invokes(monkeypatch):
    calls = []

    class FakeClient:
        initialize_result = SimpleNamespace(instructions="Original static context")

        async def list_prompts(self):
            raise AssertionError("must not discover prompts")

        async def get_prompt(self, name, arguments):
            raise AssertionError("must not fetch prompts")

        async def list_tools(self):
            calls.append("list_tools")
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
    assert tools[0]["requires_approval"] is False
    assert calls == ["list_tools"]