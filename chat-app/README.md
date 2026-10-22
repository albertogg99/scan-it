# Scan it!

Local chat for MCP servers registered at `/admin`. The SvelteKit frontend uses Keycloak login; FastAPI stores conversations, attachments, runs, and bounded stream events in PostgreSQL. See [the walkthrough](docs/WALKTHROUGH.md), [provider notes](docs/PROVIDER.md), [external clients](docs/CLIENTS.md), and [API contracts](docs/CONTRACTS.md).

## Prerequisites and secrets

From PowerShell in the repository root, install Docker Desktop for the Compose route, or Python 3.12, `uv`, Node.js/npm, PostgreSQL 16, and Keycloak 26.1 for the native route. Do not paste secrets into documentation or commit `.env`. Create it only if absent:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

Set distinct, long, URL-safe `POSTGRES_PASSWORD` and `KEYCLOAK_ADMIN_PASSWORD`; keep existing values if already configured. Set `PUBLIC_HOST` to the laptop's reachable LAN IP before the first Keycloak realm import for phone access; otherwise leave `localhost`. For live chat configure the provider as in [PROVIDER](docs/PROVIDER.md). An empty model key lets the app start but prevents messages. `.env` is read by Compose and the probe script; native backend/frontend processes need their own environment variables (below). Never assume the native apps load `.env` themselves.

## Docker (after reboot)

From the repository root with Docker Desktop running:

```powershell
docker compose up --build -d
docker compose ps
docker compose logs backend keycloak
# Stop without erasing data:
docker compose down
```

Compose starts PostgreSQL, Keycloak, and a one-shot `keycloak-setup` service, then runs `alembic upgrade head` before uvicorn; frontend follows backend health. Open `http://localhost:3000` (or the configured host/port). Register ordinary users through Keycloak. Fresh databases are seeded once with the three MCPs from the root compose file (`zurich-hackathon`, `basel-parking`, `public-transport`); manage entries at `/admin`. Existing registry entries survive upgrades and are never overwritten at startup. `docker compose down` preserves the database volume; `down -v` deletes it, including registry entries, icons, users, and chat history.

Default host ports: frontend 3000, API 8000, Keycloak 8080, PostgreSQL 5433 on loopback. Compose propagates `FRONTEND_PORT`, `BACKEND_PORT`, and `KEYCLOAK_PORT` into browser URLs, the issuer, CORS, and the initial realm import. Open `http://<PUBLIC_HOST>:<FRONTEND_PORT>` and allow the configured frontend/API/Keycloak ports through the firewall. Changing the frontend port or hostname after the realm exists also requires updating the `swiss-discover-web` client's redirect URIs and web origins in Keycloak; restarting does not reimport an existing realm. `localhost` on a phone is the phone. Keycloak's plain HTTP `start-dev` is only for a trusted local demo, not internet exposure.

## MCP administration

Open `/admin` and sign in as `admin` with password `admin`, or the value of `SWISS_DISCOVER_ADMIN_PASSWORD` set before account creation. This is an application account in the `swiss-discover` realm, separate from the master-realm Keycloak administrator. Change the demo password before exposing this service beyond a trusted demo. The setup service creates missing groups, the access-token group mapper, and the account on existing installations too; it does not reset existing passwords. Change an existing password in the Keycloak console.

Any user can sign in, but only membership in the exact `/admin` group grants access to registry APIs. Grant additional users that group in Keycloak and sign in again to obtain a fresh token. Self-registration never grants admin membership.

The form creates, edits, disables, and deletes MCP entries and uploads SVG/PNG icons for the main directory. Entries with conversation history cannot be deleted; disable them instead. Icons are stored in PostgreSQL, limited to 1 MB, and validated (PNG up to 2048px; plain SVG without styles, scripts, or external references). The registry seed folder and startup loader are no longer used.

Enter a public endpoint separately when it is safe to disclose and reachable by external clients. Registry changes take effect without rebuilding containers. Registering an MCP does not create or deploy its server container. Backend endpoints may use any HTTP(S) host reachable from the backend; only grant registry administration to trusted users, since those endpoints can target internal services. Credential fields store environment variable names only; any new secret must be passed into the backend container environment. Chat snapshots server initialization instructions and tools when a conversation is created; existing conversations retain their snapshots.

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
$env:LLM_BASE_URL = 'https://api.swisscom.com/products/swiss-ai-weeks/apertus-1.5-70b/v1'
$env:LLM_MODEL = 'swiss-ai/Apertus-v1.5-70B'
$env:LLM_API_KEY = '<YOUR-PROVIDER-KEY>'
uv sync --project backend --python 3.12
Push-Location backend
uv run alembic upgrade head
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
Pop-Location
```

Start Keycloak 26.1 in a separate terminal **before** backend migration and uvicorn (wait for Keycloak readiness). Set `KC_HOME` to the extracted Keycloak directory and substitute your own database/admin secrets. The realm import is only applied on first creation; edits to the JSON do not replace an existing realm.

```powershell
$env:KC_HOME = 'C:\path\to\keycloak-26.1.0'
$env:KC_DB = 'postgres'
$env:KC_DB_URL = 'jdbc:postgresql://localhost:5432/keycloak'
$env:KC_DB_USERNAME = 'postgres'
$env:KC_DB_PASSWORD = '<YOUR-DB-PASSWORD>'
$env:KC_BOOTSTRAP_ADMIN_USERNAME = 'admin'
$env:KC_BOOTSTRAP_ADMIN_PASSWORD = '<YOUR-ADMIN-PASSWORD>'
$env:SWISS_DISCOVER_ADMIN_PASSWORD = '<YOUR-APP-ADMIN-PASSWORD>'
$env:KC_HOSTNAME = 'http://localhost:8080'
$env:KC_HOSTNAME_BACKCHANNEL_DYNAMIC = 'true'
$env:PUBLIC_HOST = 'localhost'
$env:FRONTEND_PORT = '3000'
$env:KC_HEALTH_ENABLED = 'true'
New-Item -ItemType Directory -Force "$env:KC_HOME/data/import" | Out-Null
Copy-Item keycloak/realm/swiss-discover-realm.json "$env:KC_HOME/data/import/swiss-discover-realm.json"
Copy-Item keycloak/themes/swiss-discover "$env:KC_HOME/themes/swiss-discover" -Recurse -Force
& "$env:KC_HOME/bin/kc.bat" start-dev --import-realm
```

For LAN use, replace `localhost` in `KC_HOSTNAME`, `PUBLIC_HOST`, `OIDC_ISSUER`, `CORS_ORIGINS` and frontend public URLs with the **same** laptop IP (JWKS stays `localhost` on the backend).

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

For an existing native realm, run `scripts/provision_keycloak_admin.py` with `KEYCLOAK_INTERNAL_URL=http://localhost:8080`, `KEYCLOAK_REALM_FILE` pointing to the realm JSON, and `KEYCLOAK_ADMIN_USERNAME`, `KEYCLOAK_ADMIN_PASSWORD`, and `SWISS_DISCOVER_ADMIN_PASSWORD` in that process environment. Then open `/admin` to register MCP servers. Ordinary users can sign up and start chat from the main directory. Stop native foreground processes with Ctrl+C in each terminal; stop PostgreSQL after the other services stop. The native backend uses **one** uvicorn worker: persisted runs/events permit reconnect, not job resumption; restart marks interrupted runs failed without rerunning tools. See [WALKTHROUGH](docs/WALKTHROUGH.md) for the user flow.