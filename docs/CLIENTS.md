# External MCP clients

The app's **Local Chat** connects from the backend to registered Streamable HTTP MCP servers at `/mcp`. `GET /api/public/mcps/{id}` describes a server and exposes its registry `public_url` as `public_endpoint`; the backend API is **not** an MCP endpoint. A configured URL is metadata, not a relay or proof of external reachability. Set public endpoints through `/admin`; they are not populated from environment variables. Never put credentials in a URL.

| Server | Docker host endpoint (local demos only) | Authentication |
|---|---|---|
| Zurich transit | `http://localhost:8101/mcp` | none |
| Museum guide | `http://localhost:8102/mcp` | static `Authorization: Bearer <MUSEUM_GUIDE_TOKEN>` when set |

The native setup uses the same local ports. The museum token is held by the backend for **Local Chat**, never supplied to external clients or returned by the public API. External clients must arrange their own server credentials independently, and some clients do not accept static custom headers. Neither MCP server nor the FastAPI backend implements an OAuth authorization server, dynamic client registration or token exchange for MCP clients. Keycloak login secures the chat API; it does not grant an external MCP connector access. Publishing an HTTPS proxy alone does not add OAuth. Restrict any published endpoint and review its auth, network exposure and client compatibility before use.

The **Configure other chatbots** view shows a selectable endpoint, a copy button, and detailed per-client instructions containing the actual registry URL. It assumes endpoint accessibility rather than showing network warnings. Authentication compatibility remains separate. Official guides checked on 2026-09-24; provider UIs and requirements can change:

- [ChatGPT developer mode](https://developers.openai.com/api/docs/guides/developer-mode): enable Developer mode under Settings > Security and login, create an app through `chatgpt.com/plugins`, then select it from + > Developer mode in a chat. Configure a server-supported authentication method; a static bearer token is not an OAuth client secret.
- [Claude remote custom connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp): Customize > Connectors > + > Add custom connector, enter the endpoint, add and connect, then enable it through + > Connectors in a chat. Team/Enterprise owners first register it in Organization settings > Connectors > Add > Custom > Web.
- [Open WebUI MCP integration](https://docs.openwebui.com/features/extensibility/mcp/): Settings > Admin > Integrations > External Tool Servers > Add Connection. Select MCP (Streamable HTTP), enter URL and authentication, save, then enable it through + > Integrations > Tools in a chat.

To advertise a separately deployed endpoint, edit its Public endpoint in `/admin` and save. No container restart is required. The setup page neither rewrites URLs nor exports app-held credentials. See [MCP servers](MCP_SERVERS.md) for transport and [contracts](CONTRACTS.md) for the discovery response.