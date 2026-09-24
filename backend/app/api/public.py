import asyncio
from datetime import datetime, timezone
from time import monotonic

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp_adapter.client import connect
from app.persistence.session import get_session
from app.registry.service import resolve_server
from app.settings import Settings, get_settings

router = APIRouter(prefix="/api/public/mcps")
_health_cache: dict[str, tuple[float, dict]] = {}


@router.get("/{server_id}")
async def get_public_server(server_id: str, session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    server = await resolve_server(session, server_id, settings)
    return {
        "id": server.id, "title": server.title, "description": server.description,
        "enabled": server.enabled, "transport": "streamable-http",
        "public_endpoint": server.public_url,
        "requires_credentials": server.auth_type != "none",
    }


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