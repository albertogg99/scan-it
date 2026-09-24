"""Live Zurich transit information from the Swiss public transport API."""

import os
from datetime import datetime, timezone

import httpx
from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse
from starlette.routing import Route


mcp = FastMCP("Zurich Transit Live")
API_URL = "https://transport.opendata.ch/v1"
REQUEST_TIMEOUT_S = 6.0


def example_stops() -> list[str]:
    return [stop.strip() for stop in os.getenv("ZURICH_EXAMPLE_STOPS", "Zürich HB,Zürich Bellevue,Zürich Stadelhofen").split(",") if stop.strip()][:10]


def unavailable(reason: str) -> dict:
    return {"status": "unavailable", "source": API_URL, "retrieved_at": datetime.now(timezone.utc).isoformat(), "message": reason}


async def fetch(path: str, params: dict) -> dict:
    async with httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT_S)) as client:
        response = await client.get(f"{API_URL}/{path}", params=params)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Unexpected upstream response")
        return data


@mcp.prompt(name="general_context")
def general_context() -> str:
    return (
        "Zurich Transit Live searches Swiss public transport stops and retrieves departures, "
        "primarily for the Zurich area. Example stops: " + ", ".join(example_stops()) + ". "
        "Search and departure tools contact transport.opendata.ch when invoked; they cannot book trips "
        "or guarantee that a departure will occur. Each result says when it was retrieved in UTC; "
        "scheduled departure time is not a data-freshness timestamp. When the upstream is unavailable, "
        "say so and do not invent live departures. Use minutes for delays, local Swiss departure times "
        "as returned by the provider, and answer in the visitor's language."
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True))
async def search_stops(query: str, limit: int = 5) -> dict:
    """Search Swiss public transport stops by name; use a Zurich stop name for local results."""
    if not query.strip() or len(query) > 100 or not 1 <= limit <= 10:
        return {"status": "invalid_input", "message": "Provide a stop query of 1-100 characters and a limit of 1-10."}
    try:
        data = await fetch("locations", {"query": query.strip(), "type": "station"})
        if not isinstance(data.get("stations"), list):
            raise ValueError("Missing upstream stations")
        stops = [
            {"id": stop.get("id"), "name": stop.get("name"), "latitude": (stop.get("coordinate") or {}).get("x"), "longitude": (stop.get("coordinate") or {}).get("y")}
            for stop in data.get("stations", [])[:limit]
        ]
        return {"status": "ok", "source": f"{API_URL}/locations", "retrieved_at": datetime.now(timezone.utc).isoformat(), "stops": stops}
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        return unavailable(f"Stop search is unavailable ({type(exc).__name__}).")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True))
async def departures(stop: str, limit: int = 5) -> dict:
    """Get upcoming departures for a named Swiss public transport stop."""
    if not stop.strip() or len(stop) > 100 or not 1 <= limit <= 10:
        return {"status": "invalid_input", "message": "Provide a stop name of 1-100 characters and a limit of 1-10."}
    try:
        data = await fetch("stationboard", {"station": stop.strip(), "limit": limit})
        if not isinstance(data.get("stationboard"), list):
            raise ValueError("Missing upstream stationboard")
        board = [
            {"line": entry.get("number"), "category": entry.get("category"), "destination": entry.get("to"),
             "scheduled_departure": (entry.get("stop") or {}).get("departure"),
             "predicted_departure": ((entry.get("stop") or {}).get("prognosis") or {}).get("departure"),
             "delay_minutes": (entry.get("stop") or {}).get("delay"),
             "platform": (entry.get("stop") or {}).get("platform")}
            for entry in data.get("stationboard", [])[:limit]
        ]
        return {"status": "ok", "source": f"{API_URL}/stationboard", "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "stop": (data.get("station") or {}).get("name", stop.strip()), "departures": board}
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        return unavailable(f"Departures are unavailable ({type(exc).__name__}).")


async def health(request):
    return JSONResponse({"status": "ok"})


app = mcp.http_app(path="/mcp", stateless_http=True)
app.routes.append(Route("/health", health, methods=["GET"]))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("MCP_PORT", "8000")))