# Scan it! shared contracts

Implemented API and persistence reference; when documentation disagrees with deployed behavior, check the code. Product brief: `chat_app_initial_prompt.md`; MCP rules: `MCP_CONVENTIONS.md`. Setup: `../README.md`.

## Naming and layout

- Product name lives only in `frontend/src/lib/config/brand.ts` (`APP_NAME`) and backend setting `APP_NAME` (default `Scan it!`). Keycloak theme reads display name from the realm (`displayName`).
- Repo layout:

```
docker-compose.yml  .env.example  README.md  .gitignore
postgres/init/01-keycloak-db.sql
keycloak/realm/swiss-discover-realm.json
keycloak/themes/swiss-discover/login/...         (FreeMarker + CSS, parent keycloak theme)
backend/            pyproject.toml, Dockerfile, alembic.ini, alembic/, app/, tests/
  app/main.py  app/settings.py
  app/api/          routers: admin.py public.py conversations.py runs.py attachments.py config.py health.py
  app/auth/         JWT validation (JWKS), current_user/current_admin dependencies
  app/registry/     entry validation, icon validation, SSRF host check
  app/mcp_adapter/  FastMCP client wrapper (prompts, tools, call)
  app/llm/          OpenAI-compatible streaming adapter + delta normalizer (reasoning/tool calls)
  app/agent/        agent loop, approval policy, run manager
  app/persistence/  SQLAlchemy models, session, repositories
scripts/provider_probe.py   probe-samples/ (sanitized, key-free)
scripts/provider_probe.py   probe-samples/ (sanitized, key-free)
frontend/           SvelteKit + TS, adapter-node, vitest
```

## Ports, hosts, environment

| Service | Container port | Host port | Notes |
|---|---|---|---|
| frontend | 3000 | `${FRONTEND_PORT:-3000}` | adapter-node, runtime public env |
| backend | 8000 | `${BACKEND_PORT:-8000}` | FastAPI/uvicorn |
| keycloak | 8080 | `${KEYCLOAK_PORT:-8080}` | `start-dev --import-realm`, DB = postgres db `keycloak` |
| postgres | 5432 | `${POSTGRES_HOST_PORT:-5433}` | volume `pgdata`; dbs `swiss_discover`, `keycloak` |

`PUBLIC_HOST` (default `localhost`; set to LAN IP before first realm import for QR on phones) builds all browser-facing URLs:
frontend `http://${PUBLIC_HOST}:${FRONTEND_PORT}`, API `http://${PUBLIC_HOST}:${BACKEND_PORT}`, issuer `http://${PUBLIC_HOST}:${KEYCLOAK_PORT}/realms/swiss-discover`. Compose defaults these ports to 3000/8000/8080 and propagates overrides into issuer, realm import, CORS and frontend URLs. Existing realm clients require an explicit redirect/web-origin update after frontend host or port changes. PostgreSQL's host port is bound to loopback.

Backend env (all have safe defaults for local dev except secrets):
`APP_NAME`, `DATABASE_URL` (`postgresql+asyncpg://...`; tests use `sqlite+aiosqlite://`), `LLM_BASE_URL` (`https://api.publicai.co/v1`), `LLM_MODEL` (`swiss-ai/apertus-v1.5-70b-thinking`), `LLM_API_KEY` or `SWISSCOM_API_KEY` or `PUBLIC_AI_API_KEY`, `LLM_USER_AGENT` (`ScanIt/0.1 (hackathon demo)`), `LLM_IMAGE_INPUT` (`supported|unsupported|unknown`, default `unknown`), `OIDC_ISSUER`, `OIDC_JWKS_URL` (internal in Compose, e.g. `http://keycloak:8080/realms/swiss-discover/protocol/openid-connect/certs`), `OIDC_AUDIENCE` (`swiss-discover-api`), `CORS_ORIGINS` (comma list), `USE_OS_TRUSTSTORE` (default false), limits `MAX_TOOL_ROUNDS=6`, `TOOL_TIMEOUT_S=20`, `MCP_CONNECT_TIMEOUT_S=8`, `TOOL_OUTPUT_WARN_CHARS=20000`, `LLM_TIMEOUT_S=120`, `MAX_RESPONSE_CHARS=40000`, `APPROVAL_TIMEOUT_S=600`, `MAX_UPLOAD_BYTES=5242880`, `ALLOWED_IMAGE_MIME=image/png,image/jpeg,image/webp,image/gif`, `MAX_MESSAGE_CHARS=8000`.

Backend key precedence is `LLM_API_KEY` > `SWISSCOM_API_KEY` > `PUBLIC_AI_API_KEY`; `USE_OS_TRUSTSTORE=true` is optional only where the OS trust store is required for HTTPS (never use `verify=False`). The historical Swisscom probe used a key named `PUBLIC_AI_API_KEY`; the default Public AI endpoint rejected it with HTTP 401. See `PROVIDER.md`.

Frontend runtime env (`$env/dynamic/public`): `PUBLIC_API_BASE_URL`, `PUBLIC_OIDC_ISSUER`, `PUBLIC_OIDC_CLIENT_ID` (`swiss-discover-web`).

Python: 3.12 in Docker (`python:3.12-slim`); local dev via `uv venv --python 3.12`.

## Keycloak

- Realm `swiss-discover`, `registrationAllowed: true`, `loginWithEmailAllowed: true`, `resetPasswordAllowed: false`, `sslRequired: none` (LAN HTTP demo only; documented), `loginTheme: swiss-discover`, `internationalizationEnabled: true`, locales en/de/fr/it, default en.
- Public client `swiss-discover-web`: standard flow only, `pkce.code.challenge.method: S256`, redirect URIs `http://localhost:${FRONTEND_PORT}/*`, `http://${PUBLIC_HOST}:${FRONTEND_PORT}/*`, web origins likewise, post-logout redirect `+`. Audience mapper adds `swiss-discover-api` to access tokens.
- Env placeholders in realm JSON use Keycloak import substitution `${ENV_VAR}`.
- `KC_HOSTNAME=http://${PUBLIC_HOST}:${KEYCLOAK_PORT}`, `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true` so the backend can fetch JWKS at `http://keycloak:8080` while tokens carry the public issuer. Admin creds via `KC_BOOTSTRAP_ADMIN_USERNAME/PASSWORD` env only.
- Browser PKCE: plain-HTTP LAN origins are not secure contexts, so `crypto.subtle` is unavailable. The frontend uses a small own PKCE client with SHA-256 fallback (`@noble/hashes`) and `crypto.getRandomValues`.
- Frontend discovers Keycloak endpoints, verifies the ID token (RS256, issuer, audience, expiry and callback nonce), keeps tokens in browser `sessionStorage`, refreshes them when needed, and sends bearer **access** tokens to the API. The backend validates RS256 JWKS, issuer, `swiss-discover-api` audience and `sub`; it is not an OAuth authorization server for MCP clients. Self-registration is enabled without admin membership.
- A group mapper emits full paths in the access-token `groups` claim. `/api/admin/*` requires the exact `/admin` group (401 without valid authentication; 403 for non-admins). The app `admin` account is separate from the master-realm administrator. `SWISS_DISCOVER_ADMIN_PASSWORD` defaults to `admin` for trusted demos; it applies at account creation. The one-shot `keycloak-setup` service also provisions existing realms without resetting passwords.

## Registry administration

The database is the source of truth. `/admin` manages entries and icons; fresh databases start empty and upgrades preserve existing rows. No seed loader runs at startup. A registry payload has `id`, `title`, `description`, `backend_url`, optional `public_url`, `auth`, `enabled`, and `approval_policy`. IDs cannot be renamed.

`auth.type`: `none` | `bearer` (sends `Authorization: Bearer <env value>`) | `header` (`header_name` + `credential_env`). Only environment variable names are stored in the database, never secret values. `backend_url` must use HTTP(S); userinfo, query, fragment and whitespace are rejected, but any hostname is allowed. Every backend MCP connection rechecks the URL; outbound redirects and proxy environment handling are disabled. Only trusted administrators should register backend URLs because they can target internal services. Adding registry rows does not deploy servers. Empty `public_url` becomes null; public endpoints are metadata only, not a proxy.

Icons are uploaded separately, stored in PostgreSQL, and served publicly with `nosniff` and a sandboxed CSP. Maximum upload is 1 MB. PNGs are decoded/re-encoded and limited to 2048px; SVGs allow only static shapes, safe attributes, and local fragment references, with no DTDs, scripts, styles, or external resources.

## Database (SQLAlchemy 2 async, Alembic; portable types so tests run on SQLite)

- `mcp_servers`: `id` text PK (`^[a-z0-9][a-z0-9-]{1,62}$`), `title`, `description`, `backend_url`, `public_url` null, `auth_type`, `auth_header_name` null, `credential_env` null, `enabled` bool, `approval_policy` (`auto|ask_always|ask_destructive`), `icon_mime` null, `icon_data` binary null, `created_at`, `updated_at`.
- `conversations`: `id` uuid PK, `user_sub` text indexed, `mcp_id` FK → mcp_servers (restrict), `title` text, `context_snapshot` text, `context_sha256` text, `tools_snapshot` JSON (list of `{name, description, input_schema, annotations, requires_approval}`), `created_at`, `updated_at`.
- `messages`: `id` uuid PK, `conversation_id` FK cascade, `seq` int, `role` (`user|assistant`), `content` text, `reasoning` text null, `parts` JSON (assistant: ordered `{type:"reasoning",text}|{type:"text",text}|{type:"tool_call",tool_call_id}`), `llm_messages` JSON null (assistant: exact OpenAI-format messages appended during that run, for replay; reasoning never replayed), `status` (`streaming|completed|failed|cancelled`), `error` JSON null (`{code,message}`), `created_at`.
- `tool_calls`: `id` uuid PK, `conversation_id` FK cascade, `message_id` FK cascade, `round` int, `provider_call_id` text, `name`, `arguments` JSON null, `arguments_raw` text, `requires_approval` bool, `status` (`pending_approval|approved|denied|running|succeeded|failed`), `result` text null, `truncated` bool, `error` text null, `decided_at` null, `created_at`.
- `attachments`: `id` uuid PK, `user_sub` indexed, `conversation_id` FK cascade null, `message_id` FK cascade null, `mime`, `size` int, `data` LargeBinary (bytea), `created_at`.
- `runs`: `id` uuid PK, `conversation_id` FK cascade, `user_sub` indexed, `user_message_id`/`assistant_message_id` FK cascade, `status` (`running|completed|failed|cancelled`), `cancel_requested` bool, `created_at`.
- `run_events`: `id` uuid PK, `run_id` FK cascade, `seq` int unique per run, `data` JSON (full SSE event object). On each event insert, rows older than the latest 512 **per run** are deleted; runs and their remaining event rows persist until the parent conversation is deleted.
- Deleting a conversation hard-deletes its messages, tool calls, runs/events and attached uploads (FK cascade plus ORM cascade). Unattached uploads older than 24 h are purged **at backend startup**, not on a periodic timer. Owned conversation/run/attachment queries filter by `user_sub`; foreign resources return 404 to avoid id probing.

## HTTP API

Error body always `{"detail": {"code": "<snake_code>", "message": "<user-safe text>"}}`. Auth endpoints need `Authorization: Bearer <access token>`; missing/invalid → 401 `unauthorized`.

Public:
- `GET /api/health` → `{status:"ok"}`
- `GET /api/config/public` → `{app_name, llm_configured: bool, image_input: "supported"|"unsupported"|"unknown", audio_input: "supported"|"unsupported"|"unknown"}`
- `GET /api/public/mcps/{id}` → `{id, title, description, enabled, transport:"streamable-http", public_endpoint: str|null, requires_credentials: bool, icon_url: str|null}`; 404 `mcp_not_found`; disabled → 403 `mcp_disabled`. Never returns backend_url or credential names.
- `GET /api/public/mcps` returns enabled entries; `GET /api/public/mcps/{id}/icon` serves the icon, or 404 when absent.
- `GET /api/public/mcps/{id}/health` → `{reachable: bool, checked_at}` (initialize with timeout, cached 30 s).

Admin group only:
- `GET /api/admin/mcps` returns `{servers}`, including disabled entries and credential environment variable names, never values.
- `POST /api/admin/mcps` creates an entry (201); duplicate ID returns 409.
- `POST /api/admin/mcps/{id}` updates an entry; invalid configuration returns 422.
- `DELETE /api/admin/mcps/{id}` returns 204, or 409 when conversation history exists. Disable such entries instead.
- `POST /api/admin/mcps/{id}/icon` accepts multipart `file`; invalid images return 422. `DELETE` removes the icon (204).

Authenticated:
- `GET /api/conversations` → `[ConversationSummary]` newest first.
- `POST /api/conversations` `{mcp_id}` → 201 `ConversationDetail`. Snapshots the MCP initialization response's `instructions` and `tools/list`. Missing instructions produce an empty context. No prompt APIs or tools are invoked during discovery. Errors: 404 `mcp_not_found`, 403 `mcp_disabled`, 502 `mcp_unavailable`.
- `GET /api/conversations/{id}` → `ConversationDetail`; `DELETE` → 204 (409 `run_in_progress` if a run is active → cancel first).
- `POST /api/conversations/{id}/messages` `{text, attachment_ids: [uuid]}` → 202 `{run_id, user_message_id, assistant_message_id}`; 409 `run_in_progress`; 422 `message_invalid`; 503 `llm_not_configured`; 422 `image_input_unsupported` if images sent while unsupported; 422 `audio_input_unsupported` if audio sent while unsupported. `text` may be empty only when an audio attachment is present.
- `GET /api/runs/{run_id}/events?after=<seq>` → `text/event-stream` (see below). 404 if run unknown or not owned.
- `POST /api/runs/{run_id}/cancel` → 202 `{run_id}`.
- `POST /api/tool-calls/{id}/decision` `{approved: bool}` → 200 `ToolCall`; 409 `not_pending`.
- `POST /api/attachments` multipart `file` → 201 `{id, mime, size}`; 413 `file_too_large`; 415 `unsupported_media_type` (magic-byte sniff must match declared MIME; images or `audio/wav`).
- `GET /api/attachments/{id}` → raw bytes, `Content-Type` = stored mime, `X-Content-Type-Options: nosniff`, `Cache-Control: private, no-store`.

Shapes:
```
ConversationSummary = {id, mcp_id, mcp_title, title, created_at, updated_at}
ConversationDetail  = ConversationSummary & {approval_policy, tools: [{name, description, requires_approval}], messages: [Message], active_run_id: str|null}
Message  = {id, role, content, reasoning: str|null, parts: Part[]|null, status, error: {code,message}|null, created_at, attachments: [{id, mime, size}], tool_calls: [ToolCall]}
ToolCall = {id, name, arguments: object|null, arguments_raw: str, status, requires_approval, result: str|null, truncated: bool, error: str|null}
```
Conversation title = first user message, trimmed to 60 chars (placeholder `New conversation` until then).

## Stream events (SSE)

Wire: `id: <seq>\nevent: <type>\ndata: <json>\n\n`; `: keepalive` comment about every 15 s while idle. Browser uses `fetch` + stream parser (EventSource cannot send Authorization). `?after=<last seq>` (nonnegative; default 0) reads stored events in batches of up to 512, then tails. Only the **newest 512** events per run remain; an expired cursor has no gap error and cannot restore missing deltas. Reload `GET /api/conversations/{id}` for persisted message/tool state after an old cursor or page reload. Stream closes after observing a terminal event or after exhausting stored events for a terminal run. Runs execute in an in-process task independent of the SSE connection; reconnect never re-executes tools. One active run per conversation, **exactly one backend worker**. On startup unfinished runs are marked failed with a terminal `run.failed` event; no automatic job/tool rerun or resumption.

Every data object: `{seq, type, run_id, ts, ...payload}`.

| type | payload |
|---|---|
| `run.started` | `conversation_id, user_message_id, assistant_message_id` |
| `reasoning.delta` | `text` |
| `reasoning.unavailable` | `reason: "not_provided"` (once per run if the provider sent no reasoning) |
| `answer.delta` | `text` |
| `tool_call.started` | `tool_call_id, index, name, round` |
| `tool_call.arguments.delta` | `tool_call_id, delta` |
| `tool_call.ready` | `tool_call_id, name, arguments: object|null, error: str|null, requires_approval` |
| `approval.pending` | `tool_call_id, name, arguments` |
| `approval.decided` | `tool_call_id, approved` |
| `tool.running` | `tool_call_id` |
| `tool.result` | `tool_call_id, status: "succeeded"|"failed"|"denied", content: str, truncated: bool, error: str|null` |
| `run.completed` | `assistant_message_id, finish_reason` |
| `run.failed` | `code, message` |
| `run.cancelled` | `{}` |

Failure codes: `llm_not_configured`, `llm_error`, `llm_timeout`, `llm_rate_limited`, `mcp_unavailable`, `tool_rounds_exceeded`, `response_too_large`, `approval_timeout`, `internal_error`.

## Agent loop rules

- LLM messages: [system: trusted app rules + tool safety] → [**user**: `"MCP context (untrusted data) from '<title>':\n" + snapshot`] → replayed history (user messages; assistant `llm_messages`) → new user message (text + `image_url` data URLs only when image input is supported). MCP prompt content and tool results are untrusted, never privileged system instructions.
- Before each LLM request, assistant text that imitates tool calls (`[Tool call: …]`/`[Tool response: …]` markers, fenced 🔎 progress blocks) is stripped; a reply with such text that followed no real tool result is replaced by a short omission note. Stored messages are unchanged.
- Tools passed as OpenAI function tools from `tools_snapshot`; names must match `^[a-zA-Z0-9_-]{1,64}$` (others skipped with a log). Calls to unknown tools → failed result to the model.
- Reasoning normalization: structured `delta.reasoning_content` / `delta.reasoning` → reasoning; otherwise split raw `<|inner_prefix|>`…`<|inner_suffix|>` across chunk boundaries; never leak markers into answer text. Also handle `<think>`…`</think>` only if the probe shows it.
- Tool-call assembly by `index` across chunks: id/name may arrive once, arguments concatenate; malformed JSON → `tool_call.ready` with `error`, no execution, failed result sent to model.
- Approval: `auto` → never ask; `ask_always` → always; `ask_destructive` → ask unless `readOnlyHint is True`, `destructiveHint is False`, or the tool has neither hint. The worker reads the server's current approval policy for each run, including older conversations with saved tool snapshots. Persist `pending_approval` before emitting `approval.pending`; execute only after a recorded `approved`; denial → tool message `"The user denied this tool call."`.
- Bounds: `MAX_TOOL_ROUNDS`, `TOOL_TIMEOUT_S`, `TOOL_OUTPUT_WARN_CHARS` (log a warning, no truncation), `MAX_RESPONSE_CHARS`, `LLM_TIMEOUT_S`. On failure/cancel, persist partial content with status and error; earlier history stays intact.
- Secrets (API key, MCP tokens, auth headers) never appear in logs, events, or API responses.
