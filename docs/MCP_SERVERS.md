# Demo MCP servers

The independent Python 3.12 services live in `mcp-servers/zurich-transit` and
`mcp-servers/museum-guide`. Both use FastMCP 2.x Streamable HTTP at `/mcp`,
listen on port 8000 inside their own container, and serve `GET /health` with
`{"status":"ok"}`. Docker host ports are 8101 (transit) and 8102 (museum)
per the shared contracts. Neither endpoint needs a database or model key.
Both publish context using `FastMCP(..., instructions=...)` in the MCP
initialization response. They do not expose a `general_context` prompt.

From the repository root, use uv for local development (run the two commands
in separate terminals):

```powershell
$env:MCP_PORT=8101
uv run --project mcp-servers/zurich-transit --python 3.12 python mcp-servers/zurich-transit/server.py
$env:MCP_PORT=8102
uv run --project mcp-servers/museum-guide --python 3.12 python mcp-servers/museum-guide/server.py
```

Each command is for a separate terminal. `MCP_PORT` defaults to 8000; the
example overrides it to allow both processes on one host. Docker Compose is
responsible for mapping each container's default port to its distinct host
port; no Docker check was run here.
The internal registry URLs are `http://zurich-transit:8000/mcp` and
`http://museum-guide:8000/mcp`. Health checks should use `/health`, not `/mcp`.

The transit `search_stops` and `departures` tools use only a local sample of
Zürich HB, Zürich Bellevue, and Zürich Stadelhofen. Routes, delays, platforms,
and coordinates are demo fixtures; upcoming times are generated locally from
fixed route templates. They are not actual transit advice. Results retain the
existing stop and departure fields and include `source: static demo timetable`
and a UTC `retrieved_at` (the time the demo tool ran, not a data-freshness
guarantee). An unknown stop returns `not_found` with an empty departure list.
Neither transit tool calls an external API; both are annotated read-only.

`MUSEUM_GUIDE_TOKEN` is optional. When nonempty, every `/mcp` request requires
`Authorization: Bearer <token>`; `/health` remains public. Unset or empty
means anonymous access for a local demo. Do not expose the bearer token in
browser config or public endpoint instructions. The catalog and route
calculations are static fictional content; none save notes or visitor data.
All museum tools are read-only. For a demo requiring approval of their calls,
configure the museum registry entry with `approval_policy: ask_always`; the
shared default `ask_destructive` correctly skips approval for these annotated
read-only tools.

Focused tests (Python 3.12, no Docker or upstream network dependency):

```powershell
uv run --project mcp-servers/zurich-transit --extra test --python 3.12 pytest -q mcp-servers/zurich-transit/tests
uv run --project mcp-servers/museum-guide --extra test --python 3.12 pytest -q mcp-servers/museum-guide/tests
```