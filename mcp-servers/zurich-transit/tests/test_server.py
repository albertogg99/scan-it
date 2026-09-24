import asyncio

import httpx
import pytest
from fastmcp import Client

import server


@pytest.mark.asyncio
async def test_discovery_prompt_and_live_tool(monkeypatch):
    calls = []

    async def fake_fetch(path, params):
        calls.append((path, params))
        if path == "locations":
            return {"stations": [{"id": "8503000", "name": "Zürich HB", "coordinate": {"x": 47.378, "y": 8.54}}]}
        return {"station": {"name": "Zürich HB"}, "stationboard": [
            {"number": "4", "category": "T", "to": "Altstetten", "stop": {
                "departure": "2026-09-24T12:00:00+0200", "delay": 2,
                "prognosis": {"departure": "2026-09-24T12:02:00+0200"}}}
        ]}

    monkeypatch.setattr(server, "fetch", fake_fetch)
    async with Client(server.mcp) as client:
        assert "general_context" in {prompt.name for prompt in await client.list_prompts()}
        prompt = await client.get_prompt("general_context")
        assert prompt.messages and "unavailable" in prompt.messages[0].content.text
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert {"search_stops", "departures"} <= tools.keys()
        assert all(tool.annotations.readOnlyHint is True for tool in tools.values())
        stops = (await client.call_tool("search_stops", {"query": "Zürich HB"})).data
        board = (await client.call_tool("departures", {"stop": "Zürich HB"})).data
    assert stops["stops"][0]["id"] == "8503000"
    assert stops["retrieved_at"] and board["retrieved_at"]
    assert board["departures"][0]["delay_minutes"] == 2
    assert calls == [("locations", {"query": "Zürich HB", "type": "station"}),
                     ("stationboard", {"station": "Zürich HB", "limit": 5})]


@pytest.mark.asyncio
async def test_missing_prompt_and_upstream_timeout(monkeypatch):
    async def timed_out(path, params):
        raise httpx.ReadTimeout("upstream timed out")

    monkeypatch.setattr(server, "fetch", timed_out)
    async with Client(server.mcp) as client:
        with pytest.raises(Exception):
            await client.get_prompt("missing_prompt")
        result = (await asyncio.wait_for(client.call_tool("departures", {"stop": "Zürich HB"}), timeout=2)).data
    assert result["status"] == "unavailable"
    assert "Timeout" in result["message"]


@pytest.mark.asyncio
async def test_missing_upstream_board_is_not_an_empty_departure_list(monkeypatch):
    async def malformed_fetch(path, params):
        return {"unexpected": []}

    monkeypatch.setattr(server, "fetch", malformed_fetch)
    async with Client(server.mcp) as client:
        result = (await client.call_tool("departures", {"stop": "Zürich HB"})).data
    assert result["status"] == "unavailable"


@pytest.mark.asyncio
async def test_health():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}