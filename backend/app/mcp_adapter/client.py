import asyncio
import hashlib
import json
import ssl
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from fastapi import HTTPException
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from app.persistence.models import MCPServer
from app.registry.service import credential_headers, error, validate_backend_url
from app.settings import Settings


def requires_approval(policy: str, annotations: dict) -> bool:
    if policy == "auto":
        return False
    if policy == "ask_always":
        return True
    if "readOnlyHint" not in annotations and "destructiveHint" not in annotations:
        return False
    return annotations.get("readOnlyHint") is not True and annotations.get("destructiveHint") is not False


def http_client_factory(settings: Settings):
    verify = None
    if settings.use_os_truststore:
        import truststore

        verify = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    def create_client(**kwargs):
        kwargs["follow_redirects"] = False
        kwargs["trust_env"] = False
        kwargs["timeout"] = httpx.Timeout(settings.tool_timeout_s, connect=settings.mcp_connect_timeout_s)
        if verify is not None:
            kwargs["verify"] = verify
        return httpx.AsyncClient(**kwargs)

    return create_client


@asynccontextmanager
async def connect(server: MCPServer, settings: Settings):
    try:
        validate_backend_url(server.backend_url, settings)
    except ValueError:
        raise error(502, "mcp_unavailable", "MCP server endpoint is not allowed.") from None
    transport = StreamableHttpTransport(
        server.backend_url,
        headers=credential_headers(server),
        httpx_client_factory=http_client_factory(settings),
    )
    try:
        async with AsyncExitStack() as stack:
            async with asyncio.timeout(settings.mcp_connect_timeout_s):
                client = await stack.enter_async_context(Client(transport, timeout=settings.tool_timeout_s))
            yield client
    except HTTPException:
        raise
    except (Exception, TimeoutError) as exc:
        raise error(502, "mcp_unavailable", "MCP server is unavailable.") from exc


async def discover(server: MCPServer, settings: Settings) -> tuple[str, str, list[dict]]:
    async with connect(server, settings) as client:
        initialized = client.initialize_result
        context = initialized.instructions or "" if initialized else ""
        tools = await client.list_tools()
    snapshot = [
        {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": tool.inputSchema,
            "annotations": tool.annotations.model_dump(exclude_none=True, by_alias=True) if tool.annotations else {},
            "requires_approval": requires_approval(
                server.approval_policy,
                tool.annotations.model_dump(exclude_none=True, by_alias=True) if tool.annotations else {},
            ),
        }
        for tool in tools
    ]
    return context, hashlib.sha256(context.encode("utf-8")).hexdigest(), json.loads(json.dumps(snapshot))


async def call_tool(server: MCPServer, settings: Settings, name: str, arguments: dict):
    async with connect(server, settings) as client:
        return await client.call_tool(name, arguments, timeout=settings.tool_timeout_s)