"""An original, fictional exhibition catalog for the demo museum."""

import hmac
import os

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route


mcp = FastMCP("Museum Guide (demo)", instructions=(
    "Museum Guide is a fictional demo exhibition. Its exhibit catalog and suggested visit "
    "durations are static sample content, not live museum hours, availability, accessibility "
    "information, ticketing, or reservations. Use list_exhibits for the catalog, exhibit_details "
    "for one work, and plan_visit to calculate a suggested route without saving visitor data. "
    "Never claim a real venue or live status. Give durations in minutes and answer in the "
    "visitor's language while keeping exhibit titles in their original form."
))
CATALOG = {
    "signal-room": {
        "title": "Signal Room", "gallery": "North Hall", "duration_minutes": 12,
        "description": "A wall of softly lit switches invites visitors to arrange an imaginary city's evening signals. Each combination changes a nearby soundscape.",
    },
    "rain-ledger": {
        "title": "Rain Ledger", "gallery": "East Gallery", "duration_minutes": 8,
        "description": "Hand-built paper columns record invented weather reports. Turning a page reveals how different neighbors describe the same afternoon.",
    },
    "borrowed-horizon": {
        "title": "Borrowed Horizon", "gallery": "South Studio", "duration_minutes": 10,
        "description": "Layered translucent panels frame a changing skyline. Visitors can shift the panels to compare what a place reveals and what it hides.",
    },
}
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)


@mcp.tool(annotations=READ_ONLY)
def list_exhibits() -> dict:
    """List the fictional exhibits and galleries in the static demo catalog."""
    return {"source": "static demo catalog", "exhibits": [
        {"id": exhibit_id, "title": item["title"], "gallery": item["gallery"]}
        for exhibit_id, item in CATALOG.items()
    ]}


@mcp.tool(annotations=READ_ONLY)
def exhibit_details(exhibit_id: str) -> dict:
    """Retrieve the original static description and suggested time for one exhibit."""
    item = CATALOG.get(exhibit_id)
    if item is None:
        return {"status": "not_found", "source": "static demo catalog", "exhibit_id": exhibit_id}
    return {"status": "ok", "source": "static demo catalog", "id": exhibit_id, **item}


@mcp.tool(annotations=READ_ONLY)
def plan_visit(minutes_available: int = 30) -> dict:
    """Calculate a visit suggestion from static durations without storing personal data."""
    if not 1 <= minutes_available <= 240:
        return {"status": "invalid_input", "message": "Minutes must be between 1 and 240."}
    remaining = minutes_available
    selected = []
    for exhibit_id, item in CATALOG.items():
        if item["duration_minutes"] <= remaining:
            selected.append(exhibit_id)
            remaining -= item["duration_minutes"]
    return {"status": "ok", "source": "static demo catalog", "exhibit_ids": selected,
            "suggested_minutes": minutes_available - remaining, "minutes_available": minutes_available}


async def health(request):
    return JSONResponse({"status": "ok"})


async def bearer_guard(request, call_next):
    token = os.getenv("MUSEUM_GUIDE_TOKEN", "")
    if request.url.path.startswith("/mcp") and token and not hmac.compare_digest(
        request.headers.get("authorization", ""), f"Bearer {token}"
    ):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return await call_next(request)


app = mcp.http_app(path="/mcp", stateless_http=True)
app.routes.append(Route("/health", health, methods=["GET"]))
app.add_middleware(BaseHTTPMiddleware, dispatch=bearer_guard)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("MCP_PORT", "8000")))