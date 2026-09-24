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
  zurich-hackathon/              Offline Zurich Hackathon handbook
    config/
      tools_config.yaml
      server_instructions.md
    tools/
      __init__.py
      hackathon.py
      handbook.json
  basel-parking/                 Live Basel-Stadt parking availability
    config/
      tools_config.yaml
      server_instructions.md
    tools/
      __init__.py
      basel_parking.py
      requirements/basel_parking.txt
  public-transport/              Swiss OJP journeys from a fixed Kraftwerk stop
    config/
      tools_config.yaml
      server_instructions.md
    tools/
      __init__.py
      public_transport.py
      requirements/public_transport.txt
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

The parking endpoint is **http://localhost:8559/mcp**. To start public transport,
configure `OJP_API_TOKEN` as described below, then run:

```bash
docker compose up --build -d public-transport
```

The transport endpoint is **http://localhost:8560/mcp**, bound to localhost.
All three non-example services start with `docker compose up --build -d`.
The transport server can start without a token, but only its origin tool works
until a valid token is configured.

To run all four bundled servers, including the optional example:

```bash
docker compose --profile examples up --build -d
```

The optional `text-demo` endpoint is **http://localhost:8556/mcp**. It exposes
`slugify_text`, which converts `Hello Dynamic MCP!` to `hello-dynamic-mcp`.

Set `HACKATHON_PORT`, `BASEL_PARKING_PORT`, `PUBLIC_TRANSPORT_PORT`, and
`TEXT_DEMO_PORT` in `.env` to change the host ports, or use:

```bash
HACKATHON_PORT=8655 BASEL_PARKING_PORT=8659 PUBLIC_TRANSPORT_PORT=8660 TEXT_DEMO_PORT=8656 docker compose --profile examples up -d
```

All containers listen on port 8555 internally; their host ports must differ.
Stop the services with `docker compose --profile examples down`.

When migrating a running single-server checkout, stop the old `dynamic-mcp`
service once with `docker compose --profile examples down --remove-orphans`
before starting the new services. This stops containers belonging to this Compose
project. The old top-level tools and configuration now live in `servers/text-demo/`.

## Zurich Hackathon Information

The Zurich server uses a **bundled offline snapshot dated September 24, 2026**
of the official participant handbook and challenge briefs. All data is stored in
[servers/zurich-hackathon/tools/handbook.json](servers/zurich-hackathon/tools/handbook.json)
and loaded at startup. Tools never make network requests, including on first use.
The module has no additional dependencies, so startup does not invoke pip.

Simple tools make it easy to demo without discovering topic IDs first:

| Tool | Purpose |
| --- | --- |
| `get_event_info()` | Event dates, venue, opening time and WiFi. |
| `get_schedule()` | Full two-day timetable, early badge pickup and deadlines. |
| `get_challenges()` | Seven challenge IDs, partners and short summaries. |
| `get_challenge(challenge)` | Detailed brief by ID, title, partner or an unambiguous partial name. |
| `get_location()` | Venue address, arrival, transit and parking information. |
| `get_faq()` | Participation, accessibility, registration, prizes and ownership. |
| `get_checklist()` | What to bring and check-in preparation. |
| `get_team_info()` | Team size, matching and finding teammates. |
| `get_judging_criteria()` | Evaluation criteria, pitch formats and jury rules. |
| `get_submission_info()` | Expert-jury deadline, pitch timing and rooms. |
| `get_contacts()` | Communication channels and finding organizers on site. |
| `get_tech_goodies()` | Offered models, APIs, credits and technical resources. |
| `get_travel_reimbursement()` | Expense caps, eligibility and submission deadline. |
| `get_first_aid()` | Emergency numbers and first-aid location. |

Example tool calls for a demo:

```python
get_schedule()
get_challenges()
get_challenge("Swisscom (1)")
get_challenge("Viseca")
get_location()
```

Challenge lookup ignores case, accents and separators. For example,
`get_challenge("swiss-grounding-mcp")` and `get_challenge("MCP")` select the same
brief. `get_challenge("Swisscom")` reports both matching IDs rather than choosing
one silently. Empty or unknown queries return an actionable tool error.

The original tools remain available:

| Tool | Purpose |
| --- | --- |
| `list_hackathon_topics()` | List all 21 handbook topics and seven challenge briefs. |
| `get_hackathon_info(topic="overview", force_refresh=False)` | Read any bundled topic, including warnings and related topics. `force_refresh` is accepted but ignored. |
| `search_hackathon_info(query, limit=5)` | Search all 28 topics for every query word, ignoring case, and return ranked excerpts. |

Responses include `offline: true`, `snapshot_date` and a static-information
notice instead of live fetch times or cache status. Source URLs remain in the
data file for maintainers, not in tool responses. Search covers the local
snapshot only. Published disagreements about end times, room names and Q&A
weekdays are flagged in `warnings`; missing facts are not invented.

The bundle contains participant information and challenge descriptions, not
image-only floor plans, videos, external technical guides/datasets, API services,
registration forms or booking functionality. These limitations are stated in
the relevant topics. The FAQ includes answers that were embedded in the source
page data, not just the visible question headings.

To update information, edit the bundled JSON and its `snapshot_date`, restart the
Zurich server, and reconnect the MCP client. There is no automatic refresh or
file watcher. Old URL, timeout and cache settings are no longer used; the module
is enabled with `hackathon: {}` in its tool configuration.

Build the shared Docker image or install the shared Python runtime **before**
disconnecting from the internet. Then start only the offline server with
`docker compose up --no-build -d zurich-hackathon`. The Basel server still requires
internet access for live parking data.

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

## Public Transport From Kraftwerk

This server uses **OJP 2.0** for nationwide stop search, departure boards, and
journeys, with predictions and incidents where operators provide them. Backend
requests are XML POSTs to `https://api.opentransportdata.swiss/ojp20`.

The fixed origin is **Zurich Selnau railway station**, SLOID `ch:1:sloid:3090`,
near Kraftwerk, not the nearby tram stop "Zurich Selnau, Bahnhof".
[Kraftwerk](https://kraftwerk.zuerich/) gives its address as
Selnaustrasse 25, 8001 Zurich; the stop ID and coordinates come from
[SBB's official stop directory](https://data.sbb.ch/explore/dataset/dienststellen-gemass-opentransportdataswiss/).
This is a nearby stop, not a claim about the shortest walking route. The app is
assumed to be at the stop: walking time from the building is not included.

The origin is enforced by Python, not just model instructions. Callers cannot
override it. To deploy at another stop, edit `origin` in
`servers/public-transport/config/tools_config.yaml` using verified stop data and
restart. Locations never follow a caller's changing GPS position.

### Tools And Example Questions

| Tool | Questions it can resolve |
| --- | --- |
| `get_transport_origin()` | "Which stop are we using?" / "What is the local date and time?" |
| `search_transport_stops(query, limit=5)` | "Which stop should I use for Zurich Airport?" / "Find Zurich HB." |
| `get_departures(at=None, limit=10, force_refresh=False)` | "What leaves here next?" / "Is the next train delayed or cancelled?" / "Which platform?" |
| `plan_journey(destination_stop_id, at=None, arrive_by=False, limit=3, max_transfers=None, force_refresh=False)` | "How do I get to the airport from here?" / "Can I arrive by 18:00?" / "Can I get there without changing?" |

Search for a destination first, then pass its returned `stop_id` to
`plan_journey`. Ambiguous stop names require a user choice. Omit `at` to depart
now. Explicit times need a date and UTC offset, e.g. `2026-09-24T18:00:00+02:00`;
`arrive_by=True` requires `at`. Use `max_transfers=0` for direct journeys.
Departures are limited to 20 and journeys to 6; `truncated` signals more results.

Journey responses include public-transport, transfer, and walking legs, duration,
transfers, and platforms where available. Scheduled and estimated times remain
separate, with Europe/Zurich display times and delay seconds only where an
estimate exists. Missing flags and estimates stay unknown. Cancelled, infeasible,
or unboardable legs are flagged, not silently recommended as usable trips.

Boards and journeys include available `situations` and their validity periods,
supporting "What reported disruptions affect these services?" This is not a
complete national alerts feed. No GTFS Static, GTFS Realtime, or GTFS Service
Alerts ingestion is included; that would require a separate pipeline and matching
static/real-time IDs. Fares, bookings, arbitrary-address routing, and guaranteed
wheelchair-accessible journeys are outside this server's scope. Credit
[OpenStreetMap contributors](https://www.openstreetmap.org/copyright) when using
the walking-route geography provided by OJP.

### OJP Credentials And Freshness

In the [official API Manager](https://api-manager.opentransportdata.swiss/),
subscribe an app to **OJP 2.0** and use its **TOKEN**, not its token hash. Set
`OJP_API_TOKEN=your-token` in the root `.env` (git-ignored), or export it in the
shell that launches the server. Never commit or paste tokens into chat. A
shell-only setup that keeps the token out of command history is:

```bash
read -rsp "OJP token: " OJP_API_TOKEN
printf '\n'
export OJP_API_TOKEN
docker compose up -d --force-recreate public-transport
```

Compose and the VS Code stdio connection read `.env` automatically. Direct
`python main.py` launches require an exported variable. Recreate the container
after changing a token; `docker compose restart` does not reload its environment.
An exported shell variable takes precedence over `.env` in Compose. Credentials
are sent only to the fixed HTTPS OJP endpoint and never returned by tools or
included in their error messages. TLS is verified and redirects are not followed.

Successful responses are cached for 30 seconds in a bounded process-local cache;
`force_refresh=True` bypasses it. `retrieved_at` is the fetch time and
`response_timestamp` is OJP's response-generation time, not an individual vehicle
observation timestamp. Freshness is reevaluated on every call: timestamps older
than five minutes, future, missing, or invalid are flagged. A fresh response does
not guarantee real-time coverage for every service.

The client allows 45 uncached requests per minute per process by default, below
the published 50/minute and 20,000/day allowance. Instances sharing a token share
the upstream quota; the local limiter does not coordinate them or enforce a daily
allowance. Authentication failures, rate limits, malformed/unsafe XML, oversized
responses, and outages return errors, not empty timetables or expired snapshots.

Tests use synthetic OJP fixtures and need no token:

```bash
python -m unittest discover -s tests -p test_public_transport.py -v
```

Passing fixture tests is not authenticated live verification. An invalid token
or missing subscription must be fixed in API Manager before live queries can
succeed. API contracts used:
[location search](https://opentransportdata.swiss/en/cookbook/open-journey-planner-ojp-landing-page/ojplocationinformationrequest-2-0/),
[stop events](https://opentransportdata.swiss/en/cookbook/open-journey-planner-ojp-landing-page/ojpstopeventrequest-2-0/),
and [journey planning](https://opentransportdata.swiss/en/cookbook/open-journey-planner-ojp-landing-page/ojptriprequest-2-0/).

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
python -m pip install -r requirements.txt -r servers/basel-parking/tools/requirements/basel_parking.txt -r servers/public-transport/tools/requirements/public_transport.txt
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
port 8557 and the text demo on port 8558. `.vscode/mcp.json` also contains four
independent Docker/stdio MCP connections, including public transport. Run
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
- MCP endpoint authentication is not included. Keep HTTP ports bound to localhost
  or add an authenticated TLS proxy before exposing them to other machines.
- Keep tool output, dependency installation logs, and server logging off stdout
  when using stdio. Use logging or stderr.

SDK documentation: https://py.sdk.modelcontextprotocol.io/