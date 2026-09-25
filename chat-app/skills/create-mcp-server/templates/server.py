"""<One-line description of what this MCP server provides>."""

import hmac
import os
from typing import Annotated

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route


SOURCE = "<short label for the data source>"

mcp = FastMCP("<Service title>", instructions=(
    "<Service> provides <what> for <scope>. Its tools can <capabilities>; they cannot <main non-capabilities>. "
    "Data is <live | static | updated daily>. "
    "Use search_items to find items by name, then item_details with the returned id. "
    "<Units, time zone, and language guidance.>"
))

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

# Replace with real data access (bundled data, database, or upstream API).
ITEMS = {
    "item-1": {"name": "<Sample item one>", "summary": "<One or two sentences about item one.>"},
    "item-2": {"name": "<Sample item two>", "summary": "<One or two sentences about item two.>"},
}


@mcp.tool(annotations=READ_ONLY)
def search_items(
    query: Annotated[str, Field(description="Part of the item name", min_length=1, max_length=100)],
    limit: Annotated[int, Field(description="Maximum number of results", ge=1, le=10)] = 5,
) -> dict:
    """Search items by name and return their ids and names."""
    needle = query.strip().casefold()
    matches = [{"id": item_id, "name": item["name"]} for item_id, item in ITEMS.items()
               if needle in item["name"].casefold()]
    if not matches:
        return {"status": "not_found", "message": f"No items match '{query.strip()}'.", "items": []}
    return {"status": "ok", "source": SOURCE, "items": matches[:limit], "more_available": len(matches) > limit}


@mcp.tool(annotations=READ_ONLY)
def item_details(
    item_id: Annotated[str, Field(description="Item id returned by search_items", min_length=1, max_length=64)],
) -> dict:
    """Return the name and summary of one item."""
    item = ITEMS.get(item_id)
    if item is None:
        return {"status": "not_found", "message": f"No item with id '{item_id}'. Use search_items to find valid ids."}
    return {"status": "ok", "source": SOURCE, "id": item_id, **item}


async def health(request):
    return JSONResponse({"status": "ok"})


async def bearer_guard(request, call_next):
    token = os.getenv("MCP_TOKEN", "")
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
