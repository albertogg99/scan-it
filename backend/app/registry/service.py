import os
import re
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models import MCPServer
from app.settings import Settings

SERVER_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")


def error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def validate_backend_url(url: str, settings: Settings) -> None:
    parsed = urlsplit(url)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.hostname.lower() not in settings.allowed_hosts
            or parsed.username is not None or parsed.password is not None
            or parsed.fragment or parsed.query or any(char.isspace() for char in url)):
        raise ValueError("MCP endpoint must use an exactly allowlisted HTTP(S) hostname")
    try:
        if parsed.port is not None and parsed.port < 1:
            raise ValueError("Invalid port")
    except ValueError as exc:
        raise ValueError("Invalid MCP endpoint port") from exc


def validate_entry(entry: dict, settings: Settings) -> None:
    if not SERVER_ID.fullmatch(entry["id"]):
        raise ValueError("Invalid MCP registry id")
    validate_backend_url(entry["backend_url"], settings)
    if entry.get("public_url"):
        public = urlsplit(entry["public_url"])
        if public.scheme not in ("http", "https") or not public.hostname or public.username or public.password or public.fragment or public.query:
            raise ValueError("Invalid public MCP URL")
    if entry["approval_policy"] not in ("auto", "ask_always", "ask_destructive"):
        raise ValueError("Invalid MCP approval policy")
    auth = entry["auth"]
    if auth["type"] not in ("none", "bearer", "header"):
        raise ValueError("Invalid MCP credential type")
    if auth["type"] != "none" and not ENV_NAME.fullmatch(auth.get("credential_env", "")):
        raise ValueError("MCP credentials must refer to environment variables")
    if auth["type"] == "header":
        header = auth.get("header_name", "")
        if not HEADER_NAME.fullmatch(header) or header.lower() in ("host", "authorization", "cookie", "proxy-authorization"):
            raise ValueError("Unsafe MCP credential header")


async def resolve_server(session: AsyncSession, server_id: str, settings: Settings) -> MCPServer:
    server = await session.get(MCPServer, server_id) if SERVER_ID.fullmatch(server_id) else None
    if server is None:
        raise error(404, "mcp_not_found", "MCP server not found.")
    if not server.enabled:
        raise error(403, "mcp_disabled", "MCP server is disabled.")
    try:
        validate_backend_url(server.backend_url, settings)
    except ValueError:
        raise error(502, "mcp_unavailable", "MCP server endpoint is not allowed.") from None
    return server


def credential_headers(server: MCPServer) -> dict[str, str]:
    if server.auth_type == "none":
        return {}
    if server.auth_type not in ("bearer", "header") or not server.credential_env or not ENV_NAME.fullmatch(server.credential_env):
        raise error(502, "mcp_unavailable", "MCP credential configuration is invalid.")
    secret = os.environ.get(server.credential_env)
    if not secret or "\r" in secret or "\n" in secret:
        raise error(502, "mcp_unavailable", "MCP credential is not configured.")
    if server.auth_type == "bearer":
        return {"Authorization": f"Bearer {secret}"}
    if not server.auth_header_name or not HEADER_NAME.fullmatch(server.auth_header_name) or server.auth_header_name.lower() in ("host", "authorization", "cookie", "proxy-authorization"):
        raise error(502, "mcp_unavailable", "MCP credential header is invalid.")
    return {server.auth_header_name: secret}