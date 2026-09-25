import re

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

import server

EXPECTED_TOOLS = {"search_items", "item_details"}


async def test_instructions_and_tools():
    async with Client(server.mcp) as client:
        assert client.initialize_result.instructions.strip()
        tools = {tool.name: tool for tool in await client.list_tools()}
    assert tools.keys() == EXPECTED_TOOLS
    for tool in tools.values():
        assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", tool.name)
        assert tool.description
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is not None, tool.name
        assert tool.annotations.destructiveHint is not None, tool.name


async def test_benign_calls():
    async with Client(server.mcp) as client:
        found = (await client.call_tool("search_items", {"query": "sample"})).data
        detail = (await client.call_tool("item_details", {"item_id": found["items"][0]["id"]})).data
    assert found["status"] == "ok" and found["items"]
    assert detail["status"] == "ok"


async def test_not_found_is_a_result_not_an_error():
    async with Client(server.mcp) as client:
        missing = await client.call_tool("item_details", {"item_id": "does-not-exist"})
        empty = await client.call_tool("search_items", {"query": "zzz-no-match"})
    assert not missing.is_error and missing.data["status"] == "not_found"
    assert not empty.is_error and empty.data["status"] == "not_found"


def http_client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test")


async def test_health_and_bearer(monkeypatch):
    monkeypatch.setenv("MCP_TOKEN", "test-secret")
    async with server.app.router.lifespan_context(server.app):
        async with http_client() as client:
            health = await client.get("/health")
            missing = await client.post("/mcp", json={})
            wrong = await client.post("/mcp", json={}, headers={"Authorization": "Bearer wrong"})
        transport = StreamableHttpTransport(
            "http://test/mcp", headers={"Authorization": "Bearer test-secret"},
            httpx_client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server.app), **kwargs),
        )
        async with Client(transport) as mcp_client:
            assert mcp_client.initialize_result.instructions.strip()
            assert {tool.name for tool in await mcp_client.list_tools()} == EXPECTED_TOOLS
        # The app lifespan can only run once per process, so the anonymous case shares it.
        monkeypatch.delenv("MCP_TOKEN")
        async with http_client() as client:
            anonymous = await client.post("/mcp", json={})
    assert health.status_code == 200 and health.json() == {"status": "ok"}
    assert missing.status_code == wrong.status_code == 401
    assert anonymous.status_code != 401
