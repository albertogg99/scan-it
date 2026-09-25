# Scan it!: implementation brief

Build a desktop-oriented, responsive, MCP-agnostic chat experience for the Zurich AI Hackathon. A visitor scans a QR code or follows a link for a place or service (for example, a museum or tram stop), chooses to chat here or configure another client, and gets an assistant grounded in that MCP server's prompt, tools, and live or static data. "Scan it!" is a provisional product name: keep naming in one configurable place.

This document is the implementation brief, not permission to start implementation yet. Before coding, present the architecture and milestones, confirm unresolved provider/client capabilities with small experiments, and then implement one localized step at a time. Use the Strong sub task implementer agent for each clearly scoped implementation step, running independent steps in parallel only when files and contracts do not overlap. Report verification after each step.

## User journeys

1. A link or QR code opens `/m/{mcp_id}`; resolve the id using a server-side registry, show the MCP's name/description and two clear choices: **Local Chat** and **Configure Other**. Invalid, disabled, or unreachable entries have useful error states; never connect to an arbitrary URL supplied in the browser address bar.
2. **Local Chat** redirects to Keycloak login (username/password and self-registration). On return, preserve the selected MCP id and open its chat directly. A new conversation is scoped to that id; reopening history must restore the conversation's original MCP rather than silently switching it. Keep other users' conversations inaccessible.
3. **Configure Other** requires no login. Show ChatGPT, Claude, Open WebUI, and Other with client-specific, verified instructions, a copyable original upstream MCP endpoint URL, and clear caveats about transport/auth requirements. Never show stored tokens or internal-only endpoints. Do not claim a client supports an integration unless checked against its current documentation; indicate unsupported combinations plainly. A localhost or Docker-only MCP URL cannot be reached by cloud-hosted ChatGPT/Claude, so explain that a public HTTPS endpoint is needed for those clients. We are not building an MCP proxy in the MVP.
4. In chat, the assistant loads the MCP's `general_context` prompt as context at the beginning of every new conversation and can invoke that MCP's discovered tools on user request. Do not run a tool merely because a visitor opened the link. Display streamed answers, available reasoning, tool name/arguments/result/status, approval requests, failures, and cancellation cleanly. Add a per-user conversation sidebar with create, reopen, and delete.

## Chosen stack and boundaries

- Frontend: SvelteKit with TypeScript; route-, feature-, and component-level modules. Keep Svelte components and service files small and maintainable; do not put the entire app into one Svelte file. Separate entry, client setup instructions, auth/session handling, chat timeline, composer, streaming state, tool approval, and history. Accessible keyboard and mobile interactions.
- Backend: Python FastAPI. Use the OpenAI Python SDK against a configurable OpenAI-compatible `base_url`, and FastMCP Client for Streamable HTTP MCP discovery, prompts, and tool execution. Implement a small explicit agent loop rather than adding LangChain/LangGraph for this hackathon: it gives direct control of partial tool calls, approval, and provider-specific streamed reasoning. Isolate the LLM adapter, MCP adapter, registry, persistence, and stream event contracts for later replacement. No model weights or GPU service in Docker Compose.
- Authentication: Keycloak in Docker Compose, OIDC authorization-code + PKCE in the SvelteKit client, backend validates access tokens against Keycloak issuer/JWKS and derives user id from `sub`. Enable self-registration; use a custom native Keycloak theme (FreeMarker/CSS, no React build). Keep secrets and admin credentials in environment variables, never frontend code. Anonymous users can access the entry and Configure Other pages, not conversation APIs.
- Data: Postgres Docker container with persistent volume and migrations. Store registry metadata, user-scoped conversations/messages, tool call and approval states, and bounded uploaded image bytes (`bytea`) with MIME type/size limits. Do not store raw model API keys in browser storage. Define deletion behavior for messages and attachments.
- MCP configuration: admin-managed Postgres registry populated from a seed file; each record has a stable opaque id, public title/description, backend Streamable HTTP endpoint, optional public endpoint for Configure Other, server-side static header/bearer credential reference, enable flag, and tool-approval policy. Only approved registry entries can be contacted; protect against SSRF through host allowlists or explicit admin trust and prevent untrusted users from editing endpoints. No user-supplied raw MCP URL. Implement static header/bearer auth in the MVP; design a per-user OAuth credential interface for later but do not claim OAuth support yet.
- Deployment: local laptop Docker Compose demo with frontend, FastAPI, Keycloak, and Postgres. Document `.env.example`, health checks, seed/migration setup, and start/stop commands. A QR opened on another device must use a LAN-reachable hostname/IP, not `localhost`; verify Keycloak redirect URLs, allowed origins, and transport reachability accordingly. Public HTTPS deployment and third-party cloud-client connectivity are follow-up concerns, not MVP guarantees.

## Model and streaming contract

- Default provider: Public AI OpenAI-compatible Chat Completions at `https://api.publicai.co/v1`; model `swiss-ai/apertus-v1.5-70b-thinking`. Use `PUBLIC_AI_API_KEY` or a generic `LLM_API_KEY` read only on the backend, configurable `LLM_BASE_URL` and `LLM_MODEL` for other compatible providers, and a configurable User-Agent (Public AI documents it as required). If no key is supplied, startup can work for UI/testing, but chat must give a clear configuration error; never fake model output as live inference.
- The thinking model is always selected by default, with no user-facing model/reasoning toggle in the MVP. Apertus 1.5 raw reasoning is delimited by `<|inner_prefix|>` and `<|inner_suffix|>` when thinking is enabled. Its reference vLLM setup uses `--enable-auto-tool-choice --tool-call-parser apertus`. Do not assume these raw markers are delivered by Public AI: first record sanitized, key-free sample streaming responses for the selected provider/model with plain text, reasoning, tool calls, and an image. Determine whether it emits structured `reasoning_content` / `tool_calls` deltas, raw special tokens, or neither; adapt at the backend boundary. Avoid leaking raw delimiters into visible answers. If reasoning is unavailable, show an appropriate status rather than inventing it.
- Stream backend-to-frontend typed events (SSE or an equivalently simple resumable protocol) for reasoning, answer text, tool-call assembly, approval pending/decided, tool result/error, completion, and failure. Incrementally assemble tool name/id/JSON arguments across chunk boundaries; handle multiple calls, partial or malformed arguments, provider disconnect, reconnect/cancel, and final persistence. Bound tool rounds, response size, and timeouts; return structured errors. Render reasoning only when the provider actually supplies it, in a collapsed labeled panel separate from the answer.
- Support user image upload and rendering, with MIME/size validation and per-user access control. Apertus 1.5 supports image inputs, but verify the chosen Public AI Chat Completions endpoint actually accepts the proposed OpenAI image content format. If it does not, surface an honest unsupported state and keep provider-specific image input behind an adapter rather than silently dropping images.
- The system's trusted rules and tool safety policy remain higher priority than MCP-provided context. Load `general_context` via `prompts/get`, bind any required arguments explicitly (no silent guessing), persist a snapshot or version for reproducible conversation replay, and refresh discovered tools for each new chat. If the prompt is missing/invalid, report a useful error and do not invent a substitute. See [MCP_CONVENTIONS.md](MCP_CONVENTIONS.md).

## Tool safety and persistence

- Approval is configured per MCP: `auto`, `ask_always`, or `ask_destructive`. For `ask_destructive`, use MCP tool annotations such as `destructiveHint` when supplied; if a tool lacks reliable annotations, default to asking. Never execute a pending call until a decision is recorded, and do not rerun it on stream reconnect. Show arguments before approval and results/errors afterward; ensure the model receives only completed tool results or a denial result. Limit tool output length and sanitize rendering of MCP content.
- Model and MCP connections run on the backend, never directly from the browser. Scope every conversation, uploaded image, pending approval, and stream to the authenticated user. Keep credentials out of logs, streamed events, and Configure Other instructions. Surface network failures and provider limits without discarding prior chat history.

## Tests

Include unit tests for registry resolution, missing prompts, approval/denial, chunked tool-call parsing and reasoning, and conversation isolation, plus one end-to-end path from id -> login -> prompt -> tool -> saved history.

## Interface direction

Take cues from https://publicai.co/chat without copying its branding or assets: restrained Swiss editorial grid, legible neutral sans-serif typography, warm white and near-black with a sparing Swiss-red accent, fine borders and compact corners. Offer equally legible light and dark themes. Show place/service identity immediately in the first viewport; chat is the primary working surface, not a marketing landing page. Build a coherent matching Keycloak login theme and responsive states for narrow phones. Provide DE/FR/IT/EN UI translations with English fallback; keep server-provided MCP names/content in their original language unless translated by the MCP. Avoid decorative gradients and oversized cards.

## Implementation handoff

Before implementation, present the proposed repo tree, API and stream event schemas, registry/DB schema, auth redirect flow, and a list of provider/client capabilities requiring validation. Then implement these bounded steps with an independent Strong sub task implementer per step; parallelize only after agreeing on shared contracts:

1. Compose, configuration, Keycloak realm/theme, Postgres schema/migrations and registry seed.
2. Two FastMCP demo servers with tests and `general_context` prompts.
3. FastAPI auth/registry/MCP adapters and per-user chat persistence.
4. Provider probe, LLM streaming/tool/approval orchestration and focused tests.
5. Modular SvelteKit entry, Configure Other, chat/history/approval UI, light/dark and translations.
6. End-to-end demo walkthrough, mobile QA, external-client documentation verification, and setup docs.

Keep each step scoped and test its own contract before integrating it. Ask using the question tool if a decision genuinely blocks implementation; otherwise make a stated, conservative assumption and continue. Do not commit or start implementation merely by reading this brief.

## Research basis and remaining checks

- Public AI docs: https://platform.publicai.co/docs and https://platform.publicai.co/models (OpenAI-compatible API, User-Agent requirement, listed thinking-model id).
- Apertus v1.5 model card: https://huggingface.co/swiss-ai/Apertus-v1.5-70B (thinking delimiters, image capability, reference vLLM tool parser). These are raw-model facts, not proof of how Public AI serializes streamed deltas.
- Design reference: https://publicai.co/chat . Verify up-to-date ChatGPT, Claude, and Open WebUI MCP setup flows when implementing Configure Other. Confirm provider streaming, tool calling, image transport and model availability with a real credential before promising those paths in the demo.
