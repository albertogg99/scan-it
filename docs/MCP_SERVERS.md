# Demo MCP servers

The independent Python 3.12 services live in `mcp-servers/zurich-transit` and
`mcp-servers/museum-guide`. Both use FastMCP 2.x Streamable HTTP at `/mcp`,
listen on port 8000 inside their own container, and serve `GET /health` with
`{"status":"ok"}`. Docker host ports are 8101 (transit) and 8102 (museum)
per the shared contracts. Neither endpoint needs a database or model key.

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

`ZURICH_EXAMPLE_STOPS` is an optional comma-separated list for the transit
`general_context` prompt (default: Zürich HB, Zürich Bellevue, Zürich
Stadelhofen; at most 10 examples). Transit tool requests call
`https://transport.opendata.ch/v1/locations` and `/stationboard` with a
six-second HTTPX timeout. Tool results include a UTC `retrieved_at` and the
upstream source; a timeout or upstream failure returns `status: unavailable`
without invented departures. The retrieval timestamp does not imply the
upstream schedule was updated at that instant. Both tools are read-only but
depend on an external service.

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