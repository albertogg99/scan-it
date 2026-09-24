import asyncio
from datetime import datetime, timezone
from time import monotonic

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp_adapter.client import connect
from app.persistence.models import MCPServer
from app.persistence.session import get_session
from app.registry.service import error, resolve_server
from app.settings import Settings, get_settings

router = APIRouter(prefix="/api/public/mcps")
_health_cache: dict[str, tuple[float, dict]] = {}


def _public(server: MCPServer) -> dict:
    return {
        "id": server.id, "title": server.title, "description": server.description,
        "enabled": server.enabled, "transport": "streamable-http",
        "public_endpoint": server.public_url,
        "requires_credentials": server.auth_type != "none",
        "icon_url": f"/api/public/mcps/{server.id}/icon?v={server.updated_at.isoformat()}" if server.icon_mime else None,
    }


@router.get("")
async def list_public_servers(session: AsyncSession = Depends(get_session)):
    servers = await session.scalars(select(MCPServer).where(MCPServer.enabled.is_(True)).order_by(MCPServer.title))
    return [_public(server) for server in servers]


@router.get("/{server_id}")
async def get_public_server(server_id: str, session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    return _public(await resolve_server(session, server_id, settings))


@router.get("/{server_id}/health")
async def server_health(server_id: str, session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    server = await resolve_server(session, server_id, settings)
    cached = _health_cache.get(server.id)
    if cached and monotonic() - cached[0] < 30:
        return cached[1]
    try:
        async with asyncio.timeout(settings.mcp_connect_timeout_s):
            async with connect(server, settings):
                reachable = True
    except Exception:
        reachable = False
    result = {"reachable": reachable, "checked_at": datetime.now(timezone.utc).isoformat()}
    _health_cache[server.id] = (monotonic(), result)
    return result


@router.get("/{server_id}/icon")
async def server_icon(server_id: str, session: AsyncSession = Depends(get_session)):
    result = (await session.execute(select(MCPServer.icon_data, MCPServer.icon_mime).where(MCPServer.id == server_id))).first()
    if not result or not result.icon_data or not result.icon_mime:
        raise error(404, "icon_not_found", "MCP icon not found.")
    return Response(content=result.icon_data, media_type=result.icon_mime, headers={
        "Content-Security-Policy": "default-src 'none'; style-src 'none'; sandbox",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache",
    })