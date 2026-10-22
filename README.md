# Scan it!

Monorepo for the Scan it! hackathon project: a chat app that talks to MCP
servers, plus the MCP servers themselves.

```text
chat-app/        SvelteKit frontend, FastAPI backend, Keycloak, PostgreSQL
mcp-servers/     Shared Python MCP runtime and the bundled MCP servers
docker-compose.yml   Starts both projects together
```

- [chat-app/README.md](chat-app/README.md): chat app setup, admin, native run.
- [mcp-servers/README.md](mcp-servers/README.md): MCP runtime, servers, tests.

## Run everything

Configure the chat app secrets first (see the chat app README):

```bash
cp -n chat-app/.env.example chat-app/.env
```

Then, from the repository root:

```bash
docker compose up --build -d
docker compose ps
```

Each included project reads its own `.env` (`chat-app/.env`,
`mcp-servers/.env`). Either project can still be run on its own from its folder.

All services share one Compose network. Fresh databases are seeded with the
three EMT Madrid servers: `http://emt-bus-stop:8555/mcp`,
`http://emt-tickets:8555/mcp` and `http://bicimad:8555/mcp`. The previous Swiss
demo servers (`zurich-hackathon`, `basel-parking`, `public-transport`) remain in
the repository but only start with `docker compose --profile legacy up -d`.

Live bus arrivals need free EMT MobilityLabs credentials in `mcp-servers/.env`
(see [mcp-servers/README.md](mcp-servers/README.md#emt-madrid-servers)).
