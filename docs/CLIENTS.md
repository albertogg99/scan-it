# External MCP clients

The app's **Local Chat** connects from the backend to the two Streamable HTTP MCP servers at `/mcp`. `GET /api/public/mcps/{id}` describes a server and may expose `public_endpoint` only when its separate `*_PUBLIC_URL` is configured; the backend API is **not** an MCP endpoint. A configured URL is metadata, not a relay or proof of external reachability. Both public URLs are empty by default. Never publish a container hostname, a localhost/LAN URL to a cloud client, or a token in a URL.

| Server | Docker host endpoint (local demos only) | Authentication |
|---|---|---|
| Zurich transit | `http://localhost:8101/mcp` | none |
| Museum guide | `http://localhost:8102/mcp` | static `Authorization: Bearer <MUSEUM_GUIDE_TOKEN>` when set |

The native setup uses the same local ports. The museum token is held by the backend for **Local Chat**, never supplied to external clients or returned by the public API. External clients must arrange their own server credentials independently, and some clients do not accept static custom headers. Neither MCP server nor the FastAPI backend implements an OAuth authorization server, dynamic client registration or token exchange for MCP clients. Keycloak login secures the chat API; it does not grant an external MCP connector access. Publishing an HTTPS proxy alone does not add OAuth. Restrict any published endpoint and review its auth, network exposure and client compatibility before use.

The frontend's Configure view offers these official guides (links checked for reachability on 2026-09-24; provider UIs/requirements can change):

- [ChatGPT developer mode](https://developers.openai.com/api/docs/guides/developer-mode): check plan/workspace availability, enable developer mode and add a remote MCP connector. A cloud service needs a publicly reachable HTTPS endpoint; check its current auth requirements before connecting. The local museum static-bearer endpoint should not be described as an OAuth-ready connector.
- [Claude remote custom connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp): Customize > Connectors > Add custom connector; use a publicly reachable HTTPS MCP endpoint with supported authorization. Claude cloud cannot call laptop or Docker-internal URLs.
- [Open WebUI MCP integration](https://docs.openwebui.com/features/extensibility/mcp/): an administrator adds a Streamable HTTP MCP connection under Admin Settings > Integrations; use an address reachable **from the Open WebUI server/container**, not necessarily from your browser. Check the deployed version's authentication support; local installations can use local endpoints if network routing permits.

To advertise a separately deployed endpoint, set `ZURICH_TRANSIT_PUBLIC_URL` or `MUSEUM_GUIDE_PUBLIC_URL` in `.env` only after independently configuring public HTTPS, access policy and reachability. The Configure view suppresses the "usable" indication for cloud clients without a plausible public HTTPS URL and for any server marked `requires_credentials`; this is a UI caution, not an end-to-end security or compatibility check. See [MCP servers](MCP_SERVERS.md) for transport and [contracts](CONTRACTS.md) for the discovery response.