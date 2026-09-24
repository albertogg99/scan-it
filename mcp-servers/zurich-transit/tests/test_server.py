from datetime import datetime, timedelta

import httpx
import pytest
from fastmcp import Client

import server


@pytest.mark.asyncio
async def test_discovery_instructions_and_demo_tools(monkeypatch):
    async def no_network(*args, **kwargs):
        raise AssertionError("Transit tools must not use external HTTP")

    monkeypatch.setattr(httpx.AsyncClient, "get", no_network)
    async with Client(server.mcp) as client:
        assert "not real-time" in client.initialize_result.instructions
        assert await client.list_prompts() == []
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert {"search_stops", "departures"} == tools.keys()
        assert all(tool.annotations.readOnlyHint is True and tool.annotations.openWorldHint is False for tool in tools.values())
        stops = (await client.call_tool("search_stops", {"query": "zürich", "limit": 2})).data
        board = (await client.call_tool("departures", {"stop": "zürich hb", "limit": 3})).data
    assert [stop["name"] for stop in stops["stops"]] == ["Zürich HB", "Zürich Bellevue"]
    assert stops["stops"][0]["id"] == "8503000"
    assert stops["source"] == board["source"] == "static demo timetable"
    assert stops["retrieved_at"] and board["retrieved_at"]
    assert board["stop"] == "Zürich HB"
    assert len(board["departures"]) == 3
    assert {"line", "category", "destination", "scheduled_departure", "predicted_departure", "delay_minutes", "platform"} == board["departures"][0].keys()
    assert board["departures"][0]["delay_minutes"] == 2
    scheduled = datetime.fromisoformat(board["departures"][0]["scheduled_departure"])
    predicted = datetime.fromisoformat(board["departures"][0]["predicted_departure"])
    assert predicted - scheduled == timedelta(minutes=2)
    assert board["departures"][1]["predicted_departure"] is None


@pytest.mark.asyncio
async def test_missing_prompt_and_unknown_stop():
    async with Client(server.mcp) as client:
        with pytest.raises(Exception):
            await client.get_prompt("missing_prompt")
        result = (await client.call_tool("departures", {"stop": "Unknown"})).data
        search = (await client.call_tool("search_stops", {"query": "Unknown"})).data
    assert result["status"] == "not_found" and result["departures"] == []
    assert search["status"] == "ok" and search["stops"] == []


@pytest.mark.asyncio
async def test_invalid_input():
    async with Client(server.mcp) as client:
        search = (await client.call_tool("search_stops", {"query": " "})).data
        board = (await client.call_tool("departures", {"stop": "Zürich HB", "limit": 11})).data
    assert search["status"] == board["status"] == "invalid_input"


@pytest.mark.asyncio
async def test_health():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}