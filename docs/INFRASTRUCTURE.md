# Local infrastructure

The stack has six long-running services: Postgres, Keycloak, two independent Streamable HTTP MCP servers, FastAPI, and the SvelteKit adapter-node frontend, plus a one-shot `keycloak-setup` service. Compose builds the backend from the repository root using `backend/Dockerfile`, and the frontend and MCP images from their subdirectories. No model weights or GPU service are included.

## Configuration

Copy `.env.example` to `.env` and set distinct, long, random values for `POSTGRES_PASSWORD`, `KEYCLOAK_ADMIN_PASSWORD`, and `MUSEUM_GUIDE_TOKEN`. Compose refuses empty values. The database password is interpolated into an asyncpg URL, so use URL-safe characters (for example, letters and digits); never check `.env` into version control. `KEYCLOAK_ADMIN_USERNAME` defaults to `admin`. The root `.dockerignore` protects builds using the repo root as their context; the subdirectory Dockerfiles should also provide their own ignore files as appropriate.

Public AI is the default: `LLM_BASE_URL=https://api.publicai.co/v1`, `LLM_MODEL=swiss-ai/apertus-v1.5-70b-thinking`, and a configurable `LLM_USER_AGENT`. Supply `PUBLIC_AI_API_KEY` or `LLM_API_KEY` for live chat; leaving both empty allows the UI to start but chat must report missing configuration. Never send keys to the browser. `LLM_IMAGE_INPUT=unknown` remains conservative until a provider image probe confirms support.

To use Swisscom instead, set:

```dotenv
LLM_BASE_URL=https://api.swisscom.com/products/swiss-ai-weeks/apertus-1.5-70b/v1
LLM_MODEL=swiss-ai/Apertus-v1.5-70B
SWISSCOM_API_KEY=<your-secret>
PUBLIC_AI_API_KEY=
LLM_API_KEY=
```

Compose forwards `SWISSCOM_API_KEY` to the backend and uses it as `LLM_API_KEY` when that generic variable is empty. Provider behavior, model availability, and image/tool/reasoning support still need a real credential probe. For an alternative compatible endpoint, set `LLM_API_KEY` explicitly. Don't put a production token in a public URL or example file.

## Startup and state

After the required machine reboot, once Docker works again:

```powershell
docker compose up --build -d
docker compose ps
docker compose logs backend keycloak
```

The first Postgres initialization creates `swiss_discover` and `keycloak` databases. The named `pgdata` volume keeps both across restarts; init scripts only execute for a new volume. Keycloak imports the realm only when absent. `keycloak-setup` then ensures the `/admin` group, access-token group mapper, and application admin account exist, including on upgrades. It uses master-realm admin credentials only in the one-shot container, never in the backend. `SWISS_DISCOVER_ADMIN_PASSWORD` (demo default `admin`) sets the app password at creation, not on subsequent runs. The backend waits for setup, runs `alembic upgrade head`, and starts uvicorn from `/app/backend`. There is no seed mount or loader. Fresh registries are empty; entries and icons persist in PostgreSQL. The frontend waits for backend health. Health checks alone cannot establish MCP protocol or login functionality.

The default host ports are frontend 3000, backend 8000, Keycloak 8080, Postgres 5433 (bound to loopback), and MCP 8101/8102. Set `FRONTEND_PORT`, `BACKEND_PORT`, and `KEYCLOAK_PORT` to customize published ports; Compose propagates them into browser URLs, issuer, CORS and the initial realm redirects. Internal service ports stay unchanged. For an existing realm, also update the `swiss-discover-web` client's redirect URIs and web origins when changing the frontend hostname or port; import does not overwrite existing clients. Native Keycloak startup must set `FRONTEND_PORT` for realm substitution. Stop the stack without deleting data using `docker compose down`. `docker compose down -v` deletes the database volume and all local conversations, so use it only when intentionally resetting the demo.

For a QR opened on a phone, set `PUBLIC_HOST` to the laptop's reachable LAN IP before the realm's first import and use `http://<LAN-IP>:3000/m/zurich-transit` (or `/m/museum-guide`). Check that the laptop firewall permits ports 3000, 8000, and 8080 and that the phone and laptop share a network. `localhost` on a phone points at the phone. The realm admits both `localhost` and the configured `PUBLIC_HOST` redirect/web origin. Keycloak runs in `start-dev` with `sslRequired: none` solely for a trusted local HTTP demo; use HTTPS, hardened credentials, and production Keycloak settings before internet exposure. LAN HTTP does not provide `crypto.subtle` in most browsers, so the frontend's PKCE SHA-256 fallback is required.

Manage registry entries at `/admin`. Bundled internal endpoints are `http://zurich-transit:8000/mcp` and `http://museum-guide:8000/mcp`. The museum uses a server-side bearer token from `MUSEUM_GUIDE_TOKEN`; the registry stores only that variable name. Backend endpoints may use any HTTP(S) host reachable from the backend; only trusted administrators should manage these URLs because they can target internal services. New credentials also require backend environment configuration. Internal Docker URLs cannot be used by cloud-hosted external clients. Enter public endpoints in the admin form only when they are independently reachable and safe to disclose. The 8101/8102 host ports are for a local demo, not an internet deployment. Registry changes do not deploy MCP containers.

## Verification boundary

Verify Compose interpolation and image builds, Keycloak login and access-token audience/groups, migration preservation, admin/non-admin API access, registry/icon persistence, MCP initialization instructions, tool listing/invocation, and login -> chat -> saved history. Health checks alone cannot establish these contracts.