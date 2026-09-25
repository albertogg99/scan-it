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

All services share one Compose network, so in `/admin` register the bundled MCP
servers with their internal URLs, e.g. `http://zurich-hackathon:8555/mcp`,
`http://basel-parking:8555/mcp`, `http://public-transport:8555/mcp`.
