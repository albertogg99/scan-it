# Host constraints

The chat host connects to the server as an MCP client. These behaviors are fixed on the host side; the server must be designed around them.

## Transport and connection

- **Streamable HTTP only**, at the path `/mcp`. Build the app with `mcp.http_app(path="/mcp", stateless_http=True)`.
- **A new connection per operation.** Discovery and each tool call open their own MCP session. Do not keep per-session state; anything a later call needs must come from its arguments.
- **Timeouts.** Connecting and initializing must finish within about 8 seconds. A tool call must return within about 20 seconds, including upstream calls. Give upstream HTTP clients a timeout well below that (e.g. 10 s) and return `upstream_error` instead of hanging.
- **No redirects** are followed and proxy environment variables are ignored. Serve `/mcp` directly, without redirecting (e.g. no `/mcp` → `/mcp/`).
- **Health.** `GET /health` must return HTTP 200 with `{"status": "ok"}` and no auth. Container healthchecks use it; never use `/mcp` for health.

## Credentials

- Supported: none, a static bearer token (`Authorization: Bearer <token>`), or a static custom header. The host holds the secret and sends it server-side on every request.
- Not supported: per-user OAuth or any interactive login. The server cannot know which end user is chatting; design tools that do not need a user identity.
- Compare tokens with `hmac.compare_digest`. Keep `/health` public.

## Discovery

- At conversation start the host reads `instructions` and `tools/list`. It never calls tools, prompts, or resources during discovery.
- Tool definitions are snapshotted per conversation. Renaming or removing a tool breaks calls in existing conversations; add new tools instead of renaming.
- Only tools are used during chat. Prompts and resources may exist but the host ignores them.

## Tool calls

- **Names** must match `^[a-zA-Z0-9_-]{1,64}$`; others are silently dropped.
- **Arguments** are validated against the tool's JSON input schema before the call. Invalid arguments are rejected with a generic message, so the schema must be precise and self-explanatory.
- **Results reach the model as text only.** The host concatenates the text content blocks. If there are none, it uses `structured_content` serialized as JSON. Images, audio, and embedded resources are dropped. Returning a `dict` from a FastMCP tool yields JSON text, which is what you want.
- **Errors.** If a tool raises, the model only sees "MCP tool returned an error." with no detail. For every expected failure return a normal result instead:
  ```python
  return {"status": "not_found", "message": "No item with id 'x'. Use search_items to find valid ids."}
  ```
  Reserve exceptions for real bugs.

## Result design

The model reading the results is mid-size. Help it:

- Return only what is needed to answer the question the tool exists for. Drop internal ids, raw upstream payloads, pagination tokens, HTML, and debug fields.
- Use flat, descriptive keys (`opening_time`, `price_eur`, `distance_km`). Put units in key names or values.
- Put a `status` first. Add `source` and `retrieved_at` (UTC ISO 8601) when freshness matters.
- Cap lists with a `limit` parameter (default around 5, max 10-20) and say when more exist (`"more_available": true`).
- Prefer short, readable strings over nested structures. Summarize long upstream text to a few sentences.
- Use consistent field names across tools so ids from one tool plug straight into another.

## Secrets and safety

- Read upstream keys from environment variables at call time.
- Never include secrets, internal URLs, or stack traces in instructions, descriptions, or results.
- Validate and bound any value used to build upstream URLs or queries; do not pass user input into arbitrary hosts, paths, or shell commands.
