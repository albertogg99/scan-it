import asyncio
from datetime import datetime, timezone
from time import monotonic

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp_adapter.client import connect
from app.persistence.models import MCPServer
from app.persistence.session import get_session
from app.registry.service import resolve_server
from app.settings import Settings, get_settings

router = APIRouter(prefix="/api/public/mcps")
_health_cache: dict[str, tuple[float, dict]] = {}


def _public(server: MCPServer) -> dict:
    return {
        "id": server.id, "title": server.title, "description": server.description,
        "enabled": server.enabled, "transport": "streamable-http",
        "public_endpoint": server.public_url,
        "requires_credentials": server.auth_type != "none",
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