# Scan it!

Monorepo: `chat-app/` (SvelteKit + FastAPI + Keycloak chat app, see
`chat-app/README.md` and `chat-app/MCP_CONVENTIONS.md`) and `mcp-servers/`
(Dynamic MCP runtime, rules below). Run commands from the relevant subfolder.

## Dynamic MCP (`mcp-servers/`)

- This is a standalone extraction of the `multi-agent` tool registry behavior.
- Use Python 3.12 and the official MCP Python SDK 2.x.
- Preserve the `register(mcp, config)` tool-module interface.
- Only modules listed in `config/tools_config.yaml` are enabled. Resolve their
  `tools/requirements/<module>.txt` files together before importing tool modules.
- Tools and configuration are loaded at startup, not through a live file watcher.
- Keep logging and dependency-installation output on stderr for stdio transport.
- Keep Docker execution non-root and tool/config mounts read-only.
- Run `python -m unittest discover -s tests -v` after loader changes.

## SDK References

- https://github.com/modelcontextprotocol/python-sdk
- https://py.sdk.modelcontextprotocol.io/
- https://modelcontextprotocol.io/docs/develop/build-server