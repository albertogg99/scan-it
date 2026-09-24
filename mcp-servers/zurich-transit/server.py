"""Fictional Zurich transit departures for an offline demo."""

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse
from starlette.routing import Route


mcp = FastMCP("Zurich Transit (demo)")
SOURCE = "static demo timetable"
STOPS = (
    {"id": "8503000", "name": "Zürich HB", "latitude": 47.378, "longitude": 8.54},
    {"id": "8581453", "name": "Zürich Bellevue", "latitude": 47.366, "longitude": 8.545},
    {"id": "8503003", "name": "Zürich Stadelhofen", "latitude": 47.366, "longitude": 8.548},
)
ROUTES = {
    "Zürich HB": (
        {"line": "4", "category": "T", "destination": "Altstetten", "delay_minutes": 2, "platform": "3"},
        {"line": "10", "category": "T", "destination": "Zürich Flughafen", "delay_minutes": 0, "platform": "7"},
    ),
    "Zürich Bellevue": (
        {"line": "2", "category": "T", "destination": "Farbhof", "delay_minutes": 0, "platform": "A"},
        {"line": "5", "category": "T", "destination": "Zürich Flughafen", "delay_minutes": 1, "platform": "B"},
    ),
    "Zürich Stadelhofen": (
        {"line": "S3", "category": "S", "destination": "Zürich HB", "delay_minutes": 0, "platform": "1"},
        {"line": "S9", "category": "S", "destination": "Uster", "delay_minutes": 3, "platform": "2"},
    ),
}
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)


def example_stops() -> list[str]:
    return [stop["name"] for stop in STOPS]


@mcp.prompt(name="general_context")
def general_context() -> str:
    return (
        "Zurich Transit is a fictional offline demo for stop lookup and sample departures. "
        "Example stops: " + ", ".join(example_stops()) + ". "
        "No tool contacts a transit provider. Routes, delays, and platforms are invented; "
        "departure times are generated locally and are not real-time information. "
        "Never present them as actual travel advice. Answer in the visitor's language."
    )


@mcp.tool(annotations=READ_ONLY)
async def search_stops(query: str, limit: int = 5) -> dict:
    """Search the fictional demo catalog for Zurich stops by name."""
    if not query.strip() or len(query) > 100 or not 1 <= limit <= 10:
        return {"status": "invalid_input", "message": "Provide a stop query of 1-100 characters and a limit of 1-10."}
    stops = [stop.copy() for stop in STOPS if query.strip().casefold() in stop["name"].casefold()][:limit]
    return {"status": "ok", "source": SOURCE, "retrieved_at": datetime.now(timezone.utc).isoformat(), "stops": stops}


@mcp.tool(annotations=READ_ONLY)
async def departures(stop: str, limit: int = 5) -> dict:
    """Show locally generated sample departures for a demo Zurich stop."""
    if not stop.strip() or len(stop) > 100 or not 1 <= limit <= 10:
        return {"status": "invalid_input", "message": "Provide a stop name of 1-100 characters and a limit of 1-10."}
    matched = next((item for item in STOPS if item["name"].casefold() == stop.strip().casefold()), None)
    if matched is None:
        return {"status": "not_found", "source": SOURCE, "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "stop": stop.strip(), "departures": []}

    now = datetime.now(ZoneInfo("Europe/Zurich"))
    next_slot = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    routes = ROUTES[matched["name"]]
    board = []
    for index in range(limit):
        route = routes[index % len(routes)]
        scheduled = next_slot + timedelta(minutes=5 * index)
        delay = route["delay_minutes"]
        board.append({**route, "scheduled_departure": scheduled.isoformat(),
                      "predicted_departure": (scheduled + timedelta(minutes=delay)).isoformat() if delay else None})
    return {"status": "ok", "source": SOURCE, "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "stop": matched["name"], "departures": board}


async def health(request):
    return JSONResponse({"status": "ok"})


app = mcp.http_app(path="/mcp", stateless_http=True)
app.routes.append(Route("/health", health, methods=["GET"]))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("MCP_PORT", "8000")))