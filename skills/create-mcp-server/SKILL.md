---
name: create-mcp-server
description: Build a new MCP server (Python, FastMCP 2.x, Streamable HTTP) that plugs into a chat host which reads server context from the MCP `instructions` field and gates tool calls using tool annotations. Use when asked to create, scaffold, or design an MCP server for a given purpose or data source, for example "build an MCP for X based on these API docs".
---

# Create an MCP server for the chat host

This skill produces a self-contained MCP server folder that a chat host can connect to over Streamable HTTP. The host discovers everything it needs at conversation start: the `instructions` string from the initialization response and the tool list from `tools/list`. No host-side code changes are needed for a new server.

The content, domain, and data source of the server are decided by the user. This skill only defines how the server must be built so it works well with the host.

## Resources

| File | Use it for |
|---|---|
| [reference/instructions-guidelines.md](reference/instructions-guidelines.md) | Writing the `instructions` field |
| [reference/tool-annotations.md](reference/tool-annotations.md) | Choosing annotations for every tool and understanding approval behavior |
| [reference/host-constraints.md](reference/host-constraints.md) | Hard limits and behaviors of the host that the server must respect |
| [templates/server.py](templates/server.py) | Server skeleton (instructions, tools, `/health`, optional bearer guard) |
| [templates/pyproject.toml](templates/pyproject.toml) | Pinned dependencies and test config |
| [templates/Dockerfile](templates/Dockerfile) | Container image |
| [templates/compose.yml](templates/compose.yml) | Standalone compose file deploying only this server |
| [templates/tests/test_server.py](templates/tests/test_server.py) | Contract tests every server must pass |

Read the three reference files before designing tools.

## Workflow

1. **Understand the purpose.** From the user's request and any provided material (API docs, datasets, descriptions), list the questions an end user of the chat will ask. Each tool should answer one kind of question. Ask the user when the purpose, data source, or credentials are unclear.

2. **Pick a server id.** Lowercase, digits and hyphens, 2-63 chars, starting with a letter or digit (`^[a-z0-9][a-z0-9-]{1,62}$`), e.g. `city-library`. Use it as the folder name, the compose service name, and the package name suffix.

3. **Scaffold.** Copy `templates/` into a new folder named after the id:
   ```
   <server-id>/
   ├── server.py
   ├── pyproject.toml
   ├── Dockerfile
   ├── compose.yml
   └── tests/test_server.py
   ```
   Replace every `<placeholder>` and remove the sample tool once real tools exist.

4. **Design tools.** Follow [reference/host-constraints.md](reference/host-constraints.md):
   - Few, narrow tools with clear verb-noun names (`search_books`, `book_details`).
   - Typed parameters with `Annotated[..., Field(description=..., ge=..., le=..., max_length=...)]` so the JSON schema is explicit and bounded.
   - A one-sentence docstring per tool stating what it returns; this becomes the tool description the model sees.
   - Return a small dict with a `status` field (`ok`, `not_found`, `invalid_input`, `upstream_error`) and only the fields needed to answer. Never raise for expected failures.
   - Keep results concise: only the necessary information, flat and clearly named, so a mid-size model can read and relay it without confusion.

5. **Annotate every tool.** Set `readOnlyHint` and `destructiveHint` explicitly on every tool, plus `idempotentHint` and `openWorldHint`. See [reference/tool-annotations.md](reference/tool-annotations.md).

6. **Write `instructions`.** Pass `instructions=` to the `FastMCP` constructor following [reference/instructions-guidelines.md](reference/instructions-guidelines.md). Do not create a named prompt for this purpose; the host does not fetch prompts.

7. **Handle external data and secrets.**
   - Upstream API keys and base URLs come from environment variables read at call time; never hard-code them or return them in results.
   - Use `httpx.AsyncClient` with an explicit timeout below the host's tool timeout (see constraints), and map upstream failures to `{"status": "upstream_error", ...}`.
   - Add `httpx` to `dependencies` in `pyproject.toml` when calling external APIs.

8. **Optional access token.** The template reads `MCP_TOKEN`. When nonempty, every `/mcp` request must carry `Authorization: Bearer <token>`; `/health` stays public. Leave it empty for anonymous access. The host sends this token server-side; never put it in `instructions` or tool output.

9. **Test.** Adapt `tests/test_server.py` to the real tool names and arguments and run:
   ```bash
   uv run --project <server-id> --extra test --python 3.12 pytest -q <server-id>/tests
   ```
   Tests must cover: instructions present, expected tool set, explicit annotations on every tool, one benign call per tool, `not_found`/`invalid_input` paths, `/health`, and bearer accept/reject. Tests must not depend on the network; mock upstream calls.

10. **Run.**
    ```bash
    cd <server-id>
    docker compose up --build -d
    curl -fsS http://localhost:<host-port>/health
    ```
    The MCP endpoint is `http://<host>:<host-port>/mcp`.

## Required server contract

- [ ] Python 3.12, `fastmcp>=2.12,<3`
- [ ] Streamable HTTP at path `/mcp`, created with `mcp.http_app(path="/mcp", stateless_http=True)`
- [ ] `GET /health` returns `{"status": "ok"}` with HTTP 200 and no auth
- [ ] Listens on `0.0.0.0`, port from `MCP_PORT` (default `8000`)
- [ ] `instructions` set on the `FastMCP` constructor
- [ ] Every tool has explicit `readOnlyHint` and `destructiveHint`
- [ ] Tool names match `^[a-zA-Z0-9_-]{1,64}$`
- [ ] Results are text/JSON only, concise, and include a `status` field
- [ ] Stateless: every tool call may arrive on a new connection, so no per-session state
- [ ] Secrets only via environment variables; none in instructions, descriptions, or results
- [ ] Tests pass without network access
