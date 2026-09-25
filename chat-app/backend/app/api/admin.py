from typing import Literal

from fastapi import APIRouter, Depends, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.public import _health_cache, _public
from app.auth.jwt import current_admin
from app.persistence.models import MCPServer
from app.persistence.session import get_session
from app.registry.icons import MAX_ICON_BYTES, validate_icon
from app.registry.service import error, validate_entry
from app.settings import Settings, get_settings

router = APIRouter(prefix="/api/admin", dependencies=[Depends(current_admin)])


class RegistryAuth(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    type: Literal["none", "bearer", "header"] = "none"
    header_name: str = Field(default="", max_length=128)
    credential_env: str = Field(default="", max_length=128)


class RegistryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=2, max_length=63)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    backend_url: str = Field(min_length=1, max_length=2048)
    public_url: str | None = Field(default=None, max_length=2048)
    auth: RegistryAuth = Field(default_factory=RegistryAuth)
    enabled: bool = True
    approval_policy: Literal["auto", "ask_always", "ask_destructive"] = "ask_destructive"


def admin_entry(server: MCPServer) -> dict:
    return {**_public(server), "backend_url": server.backend_url, "public_url": server.public_url,
            "approval_policy": server.approval_policy,
            "auth": {"type": server.auth_type, "header_name": server.auth_header_name or "",
                     "credential_env": server.credential_env or ""}}


async def find_server(session: AsyncSession, server_id: str) -> MCPServer:
    server = await session.get(MCPServer, server_id)
    if server is None:
        raise error(404, "mcp_not_found", "MCP server not found.")
    return server


def assign_entry(server: MCPServer, entry: RegistryEntry, settings: Settings) -> None:
    try:
        validate_entry(entry.model_dump(), settings)
    except ValueError as exc:
        raise error(422, "registry_invalid", str(exc)) from exc
    for name in ("title", "description", "backend_url", "enabled", "approval_policy"):
        setattr(server, name, getattr(entry, name))
    server.public_url = entry.public_url or None
    server.auth_type = entry.auth.type
    server.auth_header_name = entry.auth.header_name if entry.auth.type == "header" else None
    server.credential_env = entry.auth.credential_env if entry.auth.type != "none" else None


@router.get("/mcps")
async def list_entries(session: AsyncSession = Depends(get_session)):
    servers = await session.scalars(select(MCPServer).order_by(MCPServer.title))
    return {"servers": [admin_entry(server) for server in servers]}


@router.post("/mcps", status_code=201)
async def create_entry(entry: RegistryEntry, session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    server = MCPServer(id=entry.id)
    assign_entry(server, entry, settings)
    session.add(server)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise error(409, "mcp_exists", "An MCP with this ID already exists.") from exc
    return admin_entry(server)


@router.post("/mcps/{server_id}")
async def update_entry(server_id: str, entry: RegistryEntry, session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    server = await find_server(session, server_id)
    if entry.id != server_id:
        raise error(422, "registry_invalid", "The MCP ID cannot be changed.")
    assign_entry(server, entry, settings)
    await session.commit()
    _health_cache.pop(server_id, None)
    return admin_entry(server)


@router.delete("/mcps/{server_id}", status_code=204)
async def delete_entry(server_id: str, session: AsyncSession = Depends(get_session)):
    server = await find_server(session, server_id)
    await session.delete(server)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise error(409, "mcp_in_use", "This MCP has conversation history. Disable it instead.") from exc
    _health_cache.pop(server_id, None)


@router.post("/mcps/{server_id}/icon")
async def upload_icon(server_id: str, file: UploadFile, session: AsyncSession = Depends(get_session)):
    server = await find_server(session, server_id)
    try:
        data = await file.read(MAX_ICON_BYTES + 1)
    finally:
        await file.close()
    server.icon_data, server.icon_mime = validate_icon(data)
    await session.commit()
    return admin_entry(server)


@router.delete("/mcps/{server_id}/icon", status_code=204)
async def delete_icon(server_id: str, session: AsyncSession = Depends(get_session)):
    server = await find_server(session, server_id)
    server.icon_data = None
    server.icon_mime = None
    await session.commit()