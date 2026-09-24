# Swiss Discover MCP conventions

These conventions describe how a registered MCP server supplies context to the Swiss Discover chat shell. They are an application-level agreement, not an extension to the MCP protocol. Keep the chat shell independent of the domain behind each MCP.

## Server instructions

- Supply context through the standard MCP initialization response's `instructions` field. In FastMCP, pass `instructions=` to the `FastMCP` constructor. No named prompt is required or fetched.
- The backend reads instructions after initialization when creating a conversation, before the user's first model turn. Instructions are optional under MCP: missing instructions produce an empty context snapshot, not a fabricated fallback. Never include API keys, credentials, or user-specific secrets.
- Include the service's purpose, geographic/operational scope, what its tools can and cannot do, what counts as live versus static data, and concise response guidance (including units, language behavior, and source freshness where relevant). Keep it short enough to fit beside tool definitions and chat history.
- The backend treats instructions as **untrusted MCP-provided context** below application safety rules and snapshots them with the conversation. Reopened conversations use their stored snapshot; new conversations initialize again.
- A server may additionally publish prompts, resources, or tools. The app must not assume particular tool names; discover and validate their schemas through MCP at chat start. Discovery never invokes tools.

Example server:

```python
from fastmcp import FastMCP

mcp = FastMCP(
    "Example service",
    instructions=(
        "You are a guide for this service. Use its tools for current facts; "
        "label static descriptions as static and never invent live values."
    ),
)
```

## Registry and access

- Give each server a stable, opaque registry id such as `zurich-transit` or `museum-guide`. The inbound `/m/{mcp_id}` id maps to a configured backend endpoint; it is not a URL and must not be treated as an arbitrary host.
- Manage entries and SVG/PNG icons at `/admin` as a member of the `/admin` Keycloak group. New installations start empty; entries persist in PostgreSQL and are not reseeded at startup.
- Use Streamable HTTP for the MVP. The backend connects with server-side credentials, if any. Keep original public endpoint URLs separate from internal Docker addresses; Configure Other shows a public endpoint only if it is independently reachable and safe to disclose, without private headers or bearer tokens.
- Registry entries specify approval policy (`auto`, `ask_always`, `ask_destructive`). Annotate tools accurately (`destructiveHint` and other MCP annotations where supported). Missing or unclear annotations under `ask_destructive` require approval. Tools should expose narrow, explicit input schemas and bounded outputs.
- Static header/bearer credentials are supported by the MVP; per-user MCP OAuth is reserved for a later iteration. Do not imply that external clients can reuse the backend's secret or its Keycloak login.

## Demo expectations

- `zurich-transit`: initialization instructions explain the fictional offline timetable and that generated departures are not live travel advice. Tools accept a stop query and return timestamps and provenance.
- `museum-guide`: initialization instructions describe the invented demo exhibits, that the catalog is static, and which tools provide exhibit details. Use original descriptions.
- Test initialization instructions, `tools/list`, a benign tool invocation, missing instructions, and connection failure. A new MCP built with these conventions should work after adding only a registry record, without new chat UI code.