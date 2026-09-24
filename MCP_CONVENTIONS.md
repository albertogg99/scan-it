# Swiss Discover MCP conventions

These conventions describe how a registered MCP server supplies context to the Swiss Discover chat shell. They are an application-level agreement, not an extension to the MCP protocol. Keep the chat shell independent of the domain behind each MCP.

## Required prompt

- Expose one MCP prompt named exactly `general_context` using FastMCP's `@mcp.prompt` decorator (or the equivalent in another MCP implementation).
- For the MVP, require **no arguments**. The backend lists prompts and calls `prompts/get` with name `general_context` once when creating a conversation, before the user's first model turn. Return at least one text message with useful context; do not embed API keys, credentials, or user-specific secrets.
- Include the service's purpose, geographic/operational scope, what its tools can and cannot do, what counts as live versus static data, and concise response guidance (including units, language behavior, and source freshness where relevant). Keep it short enough to fit beside tool definitions and chat history.
- The backend validates nonempty text, treats the result as **untrusted MCP-provided context** below application safety rules, snapshots it with the conversation, and reports a clear error if unavailable. Do not silently substitute a fabricated prompt. Reopened conversations use their stored snapshot; new conversations fetch again.
- A server may additionally publish other prompts, resources, or tools. The app must not assume particular names for those tools; discover and validate their schemas through MCP at chat start. Do not invoke tools automatically just because the prompt is loaded.

Example shape for a new demo server (confirm the installed FastMCP decorator signature while implementing):

```python
@mcp.prompt(name="general_context")
def general_context() -> str:
    return (
        "You are a guide for this service. Use its tools for current facts; "
        "label static descriptions as static and never invent live values."
    )
```

## Registry and access

- Give each server a stable, opaque registry id such as `zurich-transit` or `museum-guide`. The inbound `/m/{mcp_id}` id maps to a configured backend endpoint; it is not a URL and must not be treated as an arbitrary host.
- Use Streamable HTTP for the MVP. The backend connects with server-side credentials, if any. Keep original public endpoint URLs separate from internal Docker addresses; Configure Other shows a public endpoint only if it is independently reachable and safe to disclose, without private headers or bearer tokens.
- Registry entries specify approval policy (`auto`, `ask_always`, `ask_destructive`). Annotate tools accurately (`destructiveHint` and other MCP annotations where supported). Missing or unclear annotations under `ask_destructive` require approval. Tools should expose narrow, explicit input schemas and bounded outputs.
- Static header/bearer credentials are supported by the MVP; per-user MCP OAuth is reserved for a later iteration. Do not imply that external clients can reuse the backend's secret or its Keycloak login.

## Demo expectations

- `zurich-transit`: `general_context` explains Zurich transport, live departure freshness, and that upstream transport data can be unavailable. Tools accept a stop query and return clear timestamps and provenance.
- `museum-guide`: `general_context` describes the invented demo exhibits, that the catalog is static, and which tools provide exhibit details. Use original descriptions.
- Test both with `prompts/list`, `prompts/get`, `tools/list`, a benign tool invocation, and a missing-prompt/timeout path. A new MCP built with these conventions should work after adding only a registry record, without new chat UI code.