import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

import server


@pytest.mark.asyncio
async def test_discovery_prompt_and_tools():
    async with Client(server.mcp) as client:
        assert "general_context" in {prompt.name for prompt in await client.list_prompts()}
        prompt = await client.get_prompt("general_context")
        assert prompt.messages and "static" in prompt.messages[0].content.text
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert {"list_exhibits", "exhibit_details", "plan_visit"} == tools.keys()
        assert all(tool.annotations.readOnlyHint is True for tool in tools.values())
        catalog = (await client.call_tool("list_exhibits")).data
        detail = (await client.call_tool("exhibit_details", {"exhibit_id": "signal-room"})).data
        plan = (await client.call_tool("plan_visit", {"minutes_available": 20})).data
        with pytest.raises(Exception):
            await client.get_prompt("missing_prompt")
    assert len(catalog["exhibits"]) == 3
    assert detail["source"] == "static demo catalog"
    assert plan["exhibit_ids"] == ["signal-room", "rain-ledger"]


@pytest.mark.asyncio
async def test_optional_bearer_and_health(monkeypatch):
    monkeypatch.setenv("MUSEUM_GUIDE_TOKEN", "test-secret")
    async with server.app.router.lifespan_context(server.app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
            health = await client.get("/health")
            missing = await client.post("/mcp", json={})
            wrong = await client.post("/mcp", json={}, headers={"Authorization": "Bearer wrong"})
            allowed = await client.post("/mcp", json={})
            authorized = await client.post("/mcp", json={}, headers={"Authorization": "Bearer test-secret"})
            transport = StreamableHttpTransport(
                "http://localhost/mcp", headers={"Authorization": "Bearer test-secret"},
                httpx_client_factory=lambda **kwargs: httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=server.app), **kwargs
                ),
            )
            async with Client(transport) as mcp_client:
                authenticated_prompts = {prompt.name for prompt in await mcp_client.list_prompts()}
            monkeypatch.delenv("MUSEUM_GUIDE_TOKEN", raising=False)
            anonymous = await client.post("/mcp", json={})
    assert health.status_code == 200
    assert missing.status_code == wrong.status_code == 401
    assert allowed.status_code == 401
    assert authorized.status_code != 401
    assert "general_context" in authenticated_prompts
    assert anonymous.status_code != 401