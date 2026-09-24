# Swiss Discover

Local chat for two independent MCP demos: live Zurich transit and a fictional museum guide. The SvelteKit frontend uses Keycloak login; FastAPI stores conversations, attachments, runs, and bounded stream events in PostgreSQL. See [the walkthrough](docs/WALKTHROUGH.md), [provider notes](docs/PROVIDER.md), [external clients](docs/CLIENTS.md), and [API contracts](docs/CONTRACTS.md).

## Prerequisites and secrets

From PowerShell in the repository root, install Docker Desktop for the Compose route, or Python 3.12, `uv`, Node.js/npm, PostgreSQL 16, and Keycloak 26.1 for the native route. Do not paste secrets into documentation or commit `.env`. Create it only if absent:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

Set distinct, long, URL-safe `POSTGRES_PASSWORD`, `KEYCLOAK_ADMIN_PASSWORD`, and `MUSEUM_GUIDE_TOKEN`; keep existing values if already configured. Set `PUBLIC_HOST` to the laptop's reachable LAN IP before the first Keycloak realm import for phone access; otherwise leave `localhost`. For live chat configure the provider as in [PROVIDER](docs/PROVIDER.md). An empty model key lets the app start but prevents messages. `.env` is read by Compose and the probe script; native backend/frontend processes need their own environment variables (below). Never assume the native apps load `.env` themselves.

## Docker (after reboot)

From the repository root with Docker Desktop running:

```powershell
docker compose up --build -d
docker compose ps
docker compose logs backend keycloak
# Stop without erasing data:
docker compose down
```

Compose starts PostgreSQL, Keycloak (realm import), both MCP servers, then runs `alembic upgrade head` and `python -m app.registry.seed` before uvicorn; frontend follows backend health. Open `http://localhost:3000` (or `http://<PUBLIC_HOST>:3000`). Register a user through the Keycloak sign-in screen; the admin login is for Keycloak administration, not a demo user. `docker compose down` preserves the database volume; `down -v` deletes it, including chat history. Docker/real Keycloak/Postgres live E2E is **deferred until the user reboots**; no successful integrated run is implied here.

Host ports: frontend 3000, API 8000, Keycloak 8080, PostgreSQL 5433 on loopback, transit MCP 8101, museum MCP 8102. Keep the first three at their defaults: browser URLs, the Keycloak issuer, realm redirects and CORS use those fixed ports. LAN clients need 3000/8000/8080 reachable through the firewall; `localhost` on a phone is the phone. Keycloak's plain HTTP `start-dev` and static MCP token are only for a trusted local demo, not internet exposure.

## Windows native (no Docker)

Run from the repository root in PowerShell. Start PostgreSQL 16 using your installed Windows service (service name below is the usual installer default; use `Get-Service *postgres*` to find yours). Start/stop service commands may require an elevated PowerShell opened by **you**. Create the `swiss_discover` and `keycloak` databases once using `psql` as an administrative user, and use a local database user with rights to both. For example, with a local `postgres` user and `psql` on PATH:

```powershell
Start-Service postgresql-x64-16
psql -U postgres -h localhost -c 'CREATE DATABASE swiss_discover;'
psql -U postgres -h localhost -c 'CREATE DATABASE keycloak;'
```

Skip a `CREATE DATABASE` command if that database already exists. In each new backend/Keycloak terminal set the required environment for that process; use your own values, never the literal placeholders below. Set `PUBLIC_HOST` consistently to `localhost` or the laptop LAN IP. For the backend terminal:

```powershell
$env:DATABASE_URL = 'postgresql+asyncpg://postgres:<URL-SAFE-DB-PASSWORD>@localhost:5432/swiss_discover'
$env:OIDC_ISSUER = 'http://localhost:8080/realms/swiss-discover'
$env:OIDC_JWKS_URL = 'http://localhost:8080/realms/swiss-discover/protocol/openid-connect/certs'
$env:CORS_ORIGINS = 'http://localhost:3000'
$env:MCP_ALLOWED_HOSTS = 'localhost,127.0.0.1'
$env:REGISTRY_SEED_PATH = (Resolve-Path seed/registry.yaml).Path
$env:ZURICH_TRANSIT_URL = 'http://localhost:8101/mcp'
$env:MUSEUM_GUIDE_URL = 'http://localhost:8102/mcp'
$env:MUSEUM_GUIDE_TOKEN = '<YOUR-MUSEUM-TOKEN>'
$env:LLM_BASE_URL = 'https://api.swisscom.com/products/swiss-ai-weeks/apertus-1.5-70b/v1'
$env:LLM_MODEL = 'swiss-ai/Apertus-v1.5-70B'
$env:LLM_API_KEY = '<YOUR-PROVIDER-KEY>'
uv sync --project backend --python 3.12
Push-Location backend
uv run alembic upgrade head
uv run python -m app.registry.seed
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
Pop-Location
```

Start Keycloak 26.1 in a separate terminal **before** backend migration/seed and uvicorn (wait for Keycloak and MCP readiness). Set `KC_HOME` to the extracted Keycloak directory and substitute your own database/admin secrets. The realm import is only applied on first creation; edits to the JSON do not replace an existing realm.

```powershell
$env:KC_HOME = 'C:\path\to\keycloak-26.1.0'
$env:KC_DB = 'postgres'
$env:KC_DB_URL = 'jdbc:postgresql://localhost:5432/keycloak'
$env:KC_DB_USERNAME = 'postgres'
$env:KC_DB_PASSWORD = '<YOUR-DB-PASSWORD>'
$env:KC_BOOTSTRAP_ADMIN_USERNAME = 'admin'
$env:KC_BOOTSTRAP_ADMIN_PASSWORD = '<YOUR-ADMIN-PASSWORD>'
$env:KC_HOSTNAME = 'http://localhost:8080'
$env:KC_HOSTNAME_BACKCHANNEL_DYNAMIC = 'true'
$env:PUBLIC_HOST = 'localhost'
$env:KC_HEALTH_ENABLED = 'true'
New-Item -ItemType Directory -Force "$env:KC_HOME/data/import" | Out-Null
Copy-Item keycloak/realm/swiss-discover-realm.json "$env:KC_HOME/data/import/swiss-discover-realm.json"
Copy-Item keycloak/themes/swiss-discover "$env:KC_HOME/themes/swiss-discover" -Recurse -Force
& "$env:KC_HOME/bin/kc.bat" start-dev --import-realm
```

For LAN use, replace `localhost` in `KC_HOSTNAME`, `PUBLIC_HOST`, `OIDC_ISSUER`, `CORS_ORIGINS` and frontend public URLs with the **same** laptop IP (JWKS stays `localhost` on the backend). Run these in two more terminals:

```powershell
$env:MCP_PORT = '8101'
uv run --project mcp-servers/zurich-transit --python 3.12 python mcp-servers/zurich-transit/server.py
```

```powershell
$env:MCP_PORT = '8102'
$env:MUSEUM_GUIDE_TOKEN = '<YOUR-MUSEUM-TOKEN>'
uv run --project mcp-servers/museum-guide --python 3.12 python mcp-servers/museum-guide/server.py
```

Frontend, in another terminal (`npm install` is only needed once):

```powershell
$env:PUBLIC_API_BASE_URL = 'http://localhost:8000'
$env:PUBLIC_OIDC_ISSUER = 'http://localhost:8080/realms/swiss-discover'
$env:PUBLIC_OIDC_CLIENT_ID = 'swiss-discover-web'
Push-Location frontend
npm install
npm run dev -- --host 0.0.0.0 --port 3000
Pop-Location
```

Open `http://localhost:3000`, choose a demo, sign up in Keycloak, then start chat. Stop native foreground processes with Ctrl+C in each terminal; stop PostgreSQL with `Stop-Service postgresql-x64-16` (or your actual service name) after the other services stop. The native backend uses **one** uvicorn worker: persisted runs/events permit reconnect, not job resumption; restart marks interrupted runs failed without rerunning tools. See [WALKTHROUGH](docs/WALKTHROUGH.md) for the user flow.