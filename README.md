# dynamic-mcp

A shared Python 3.12 / MCP SDK 2.x runtime for independent MCP servers. Each
directory under `servers/` owns its tools, configuration, requirements, and server
instructions. Docker Compose runs each server in its own container using the same
base image, with no runtime dependency on the original `multi-agent` project.

```text
main.py                         Shared startup and tool loader
Dockerfile                      Server-independent base image
docker-compose.yaml             One service per server
servers/
  zurich-hackathon/              Live Zurich Hackathon handbook
    config/
      tools_config.yaml
      server_instructions.md
    tools/
      __init__.py
      hackathon.py
      requirements/hackathon.txt
  basel-parking/                 Live Basel-Stadt parking availability
    config/
      tools_config.yaml
      server_instructions.md
    tools/
      __init__.py
      basel_parking.py
      requirements/basel_parking.txt
  text-demo/                    Original slugify example
    config/
      tools_config.yaml
      server_instructions.md
    tools/
      __init__.py
      text_tools.py
      requirements/text_tools.txt
tests/
```

## Run With Docker

Requires Docker Engine and the Docker Compose plugin. On WSL, enable Docker
Desktop integration for your distro first.

```bash
docker compose up --build -d zurich-hackathon
docker compose logs -f zurich-hackathon
```

The hackathon endpoint is **http://localhost:8555/mcp**. To start Basel parking:

```bash
docker compose up --build -d basel-parking
docker compose logs -f basel-parking
```

The parking endpoint is **http://localhost:8559/mcp**. Both production services
start when running `docker compose up --build -d` without a service name.

To run all three bundled servers, including the optional example:

```bash
docker compose --profile examples up --build -d
```

The optional `text-demo` endpoint is **http://localhost:8556/mcp**. It exposes
`slugify_text`, which converts `Hello Dynamic MCP!` to `hello-dynamic-mcp`.

Set `HACKATHON_PORT`, `BASEL_PARKING_PORT`, and `TEXT_DEMO_PORT` in `.env` to change
the host ports, or use:

```bash
HACKATHON_PORT=8655 BASEL_PARKING_PORT=8659 TEXT_DEMO_PORT=8656 docker compose --profile examples up -d
```

All containers listen on port 8555 internally; their host ports must differ.
Stop the services with `docker compose --profile examples down`.

When migrating a running single-server checkout, stop the old `dynamic-mcp`
service once with `docker compose --profile examples down --remove-orphans`
before starting the new services. This stops containers belonging to this Compose
project. The old top-level tools and configuration now live in `servers/text-demo/`.

## Zurich Hackathon Information

The first server uses the official participant handbook at
https://zh.ai-weeks.ch/. It exposes three tools:

| Tool | Purpose |
| --- | --- |
| `list_hackathon_topics()` | List configured topic IDs and titles without a network request. |
| `get_hackathon_info(topic="overview", force_refresh=False)` | Return topic text and tables fetched by the server, with UTC fetch time and no website links. |
| `search_hackathon_info(query, limit=5)` | Search page titles and content for all query words, ignoring case; return ranked excerpts. |

The 21 topics cover updates, FAQs, location, the venue map, challenges, team
building, schedules, judging, submissions, support, travel reimbursement, and
other handbook sections. The FAQ topic reads the main event page directly,
including answers embedded in the page data rather than visible static HTML.
Use the listing tool for topic IDs, then read a matching topic for full details.

Source URLs stay in server configuration, not tool responses. The server removes
hyperlink destinations, image URLs, and raw web URLs from extracted content and
does not expose request URLs in errors. Link labels and image descriptions are
retained as text; clients use the MCP tools rather than browsing to read more.
Images, videos, and external forms themselves are not returned as text.

Information is fetched from the official pages on demand, not copied into a
static event snapshot. Successful reads are cached in memory for five minutes;
`force_refresh=True` bypasses the cache. Fetch time is not the site's last-update
time. Search fetches pages with up to four concurrent requests and reports any
failures in `errors`, with `complete=False`; an unavailable page is not treated
as evidence that information does not exist. Reads fail explicitly if a site is
unavailable or its HTML is not readable. Internet access and working TLS trust
are required on the MCP server, not on the client for reading the source pages.

Edit `servers/zurich-hackathon/config/tools_config.yaml` to maintain the topic
catalog, timeout, and cache lifetime. URLs must be HTTPS on the official AI Weeks
domains. Callers select a configured topic rather than supplying arbitrary URLs;
HTTP redirects are not followed. HTTPS uses the system CA store with certificate
verification enabled.

## Basel Parking Availability

The parking server reads the official Basel-Stadt dataset
https://data.bs.ch/explore/dataset/100088/ through its JSON API. It exposes four
read-only tools:

| Tool | Purpose |
| --- | --- |
| `list_parking_facilities(query="")` | List facilities, optionally filtering IDs, names, titles, or addresses. |
| `get_parking_status(parking_id)` | Get reported counts, status, source timestamps, and data-quality warnings for one exact ID. |
| `find_available_parking(latitude, longitude, min_free_spaces=1, radius_meters=5000, limit=5)` | Rank fresh, usable availability by straight-line distance, excluding closed facilities. |
| `get_parking_summary()` | Summarize status, freshness, count quality, eligible free spaces, and weighted occupancy. |

Responses include the source URL, original publication timestamp, UTC retrieval
and evaluation times, and whether the snapshot was cached. Successful snapshots
are cached for 30 seconds; freshness is reevaluated on each call. Observations
older than five minutes, or with missing, invalid, or future publication times,
are never ranked as current availability. Unknown opening status is explicitly
flagged and is not confirmation that a facility is open.

Missing counts remain null. Occupancy is calculated only from valid free and
total counts; the summary reports the subset used for weighted occupancy.
Distances are straight-line metres, not driving distances. Free spaces are
observations, not reservations or guarantees of availability on arrival.

The client fetches a complete snapshot in stable ID order, with bounded
pagination, HTTPS verification, and no redirects or caller-supplied URLs.
Malformed responses and source failures fail explicitly instead of looking like
empty results or falling back to an expired cache. Settings live in
`servers/basel-parking/config/tools_config.yaml` and take effect after a restart.

## Add Another Server

1. Create `servers/my-server/` with `config/`, `tools/`, and `tools/__init__.py`,
  following a bundled server. `tools_config.yaml` enables modules;
   `server_instructions.md` contains optional plain-text or Markdown guidance.
2. Add a service under `services:` in `docker-compose.yaml`, reusing the anchors:

   ```yaml
   my-server:
     <<: *dynamic-mcp
     environment:
       <<: *mcp-environment
       MCP_SERVER_NAME: "My MCP Server"
     ports:
       - "127.0.0.1:${MY_SERVER_PORT:-8559}:8555"
     volumes:
       - ./servers/my-server:/app/server:ro
   ```

3. Start it with `docker compose up -d my-server` after building the base image.
   Each service has its own process, tool imports, dependency directory, cache,
   and instructions. Server folders are not automatically added to Compose;
   explicitly declaring a service controls what gets deployed.

The shared image contains only the runtime. To use it outside Compose, mount a
server directory at `/app/server`; tool dependencies are installed into the
container's writable `/app/tool_packages` directory before tool imports.

## Add Or Reuse Tools

Within the chosen server directory:

1. Add `tools/greeting.py` with the existing `register(mcp, config)` interface:

   ```python
   def register(mcp, config: dict):
       prefix = config.get("prefix", "Hello")

       @mcp.tool()
       def greet(name: str) -> str:
           """Return a greeting for a name."""
           return f"{prefix}, {name}!"
   ```

2. Put third-party dependencies in `tools/requirements/greeting.txt`, using pip
   requirements syntax. Omit the file for dependency-free tools.
3. Add the module to that server's `config/tools_config.yaml`:

   ```yaml
   greeting:
     prefix: Hello
   ```

4. Restart only the affected service, for example:

   ```bash
   docker compose restart zurich-hackathon
   ```

Only YAML-listed modules are loaded. Their requirements are resolved together
before importing any tool modules. A module may register multiple tools. Remove
an entry to disable it, or use `{}` for an empty registry. Invalid configuration,
missing modules, import failures, and failed dependency installations stop startup.

Loading happens at startup, not through a file watcher. Tools, config, and
instructions are mounted read-only, so editing them does not require rebuilding
the image. Reconnect clients after restarting. Recreate a container with
`docker compose up -d --force-recreate zurich-hackathon` to discard previously
installed tool dependencies. Changes to the shared runtime or base requirements
require `docker compose up --build -d` for the affected services.

## Server Context And Prompts

Each server reads its own `config/server_instructions.md` at startup and sends
the text in the MCP initialization response over both HTTP and stdio. The same
text is exposed as a no-argument MCP prompt named `general_context`, so each
server has its own context without duplicating it in Python tool modules.

Clients discover the prompt through `prompts/list` and retrieve it through
`prompts/get` with `{"name": "general_context"}`. With an initialized Python SDK
client session:

```python
context = await session.get_prompt("general_context")
```

The returned `context.messages` contain the server's guidance. Initialization
instructions arrive when a client connects; prompts must be requested by the
client or selected by the user. Clients decide whether to include either in
model context. Neither can enforce behavior or override client policies;
enforce mandatory restrictions in tool code.

Missing or whitespace-only files disable both instructions and the automatic
`general_context` prompt; other file errors stop startup. Edit the context file
and restart the server to apply changes, then reconnect clients.

## Local Development And VS Code

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt -r servers/zurich-hackathon/tools/requirements/hackathon.txt -r servers/basel-parking/tools/requirements/basel_parking.txt
python -m unittest discover -s tests -v
MCP_HOST=127.0.0.1 MCP_PORT=8557 python main.py --server-dir servers/zurich-hackathon
```

Select another folder with `--server-dir` or `MCP_SERVER_DIR`. The command-line
argument takes precedence, and the default is `servers/zurich-hackathon` relative
to the shared runtime, not the current working directory. Run one server per
process. For stdio:

```bash
python main.py --server-dir servers/text-demo --transport stdio
```

`MCP_SERVER_NAME` overrides the folder name advertised to clients. `MCP_HOST`,
`MCP_PORT`, and `LOG_LEVEL` configure each process. `MCP_PACKAGES_DIR` overrides
the dependency install directory, which defaults locally to `tool_packages/`
inside the selected server folder. Docker sets it outside the read-only mount.

Select `.venv` in VS Code. The two debug configurations launch the hackathon on
port 8557 and the text demo on port 8558. `.vscode/mcp.json` also contains three
independent Docker/stdio MCP connections, including Basel parking. Run
`docker compose build` first, then start the desired MCP connection in VS Code;
no HTTP container is needed.

## Dependency And Security Model

- All containers run as non-root, with their complete server directory mounted
  read-only and dependencies written only to their own container filesystem.
- Tools in one server share a process and dependency set. Separate containers
  isolate servers, not individual tools within one server.
- Tool modules and requirements are trusted executable input. Never load
  untrusted modules or grant tools unnecessary credentials.
- Startup requires package-index access when enabled modules declare dependencies.
  Packages needing native libraries require customizing the shared Dockerfile.
- Authentication is not included. HTTP ports bind to localhost only; add an
  authenticated TLS proxy before exposing them to other machines.
- Keep tool output, dependency installation logs, and server logging off stdout
  when using stdio. Use logging or stderr.

SDK documentation: https://py.sdk.modelcontextprotocol.io/