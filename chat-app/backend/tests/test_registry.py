import pytest

from app.persistence.models import MCPServer
from app.registry.service import credential_headers, validate_backend_url, validate_entry
from app.settings import Settings


def test_url_validation_and_secret_reference(monkeypatch):
    settings = Settings(_env_file=None)
    for url in ("http://example-mcp@evil/mcp", "file://example-mcp/mcp", "http://example-mcp/mcp#fragment", "http://example-mcp/mcp?token=secret"):
        with pytest.raises(ValueError):
            validate_backend_url(url, settings)
    validate_backend_url("http://example-mcp:8000/mcp", settings)
    validate_backend_url("http://10.10.52.203:8655/mcp", settings)
    validate_backend_url("https://example-mcp.evil/mcp", settings)
    entry = {
        "id": "example-mcp", "backend_url": "http://example-mcp:8000/mcp",
        "approval_policy": "ask_destructive", "auth": {"type": "bearer", "credential_env": "EXAMPLE_MCP_TOKEN"},
    }
    validate_entry(entry, settings)
    monkeypatch.setenv("EXAMPLE_MCP_TOKEN", "private-value")
    server = MCPServer(auth_type="bearer", credential_env="EXAMPLE_MCP_TOKEN")
    assert credential_headers(server) == {"Authorization": "Bearer private-value"}
    entry["auth"] = {"type": "header", "header_name": "Host", "credential_env": "EXAMPLE_MCP_TOKEN"}
    with pytest.raises(ValueError):
        validate_entry(entry, settings)


def test_transport_disallows_redirects():
    from app.mcp_adapter.client import http_client_factory

    client = http_client_factory(Settings(_env_file=None))(follow_redirects=True)
    assert client.follow_redirects is False
    assert client._trust_env is False


@pytest.mark.asyncio
async def test_admin_registry_and_icons(api):
    from io import BytesIO
    from PIL import Image
    from app.auth.jwt import current_admin
    from app.main import app

    client, factory, user = api
    assert (await client.get("/api/admin/mcps")).status_code == 401
    app.dependency_overrides[current_admin] = lambda: "admin"
    entry = {"id": "new-mcp", "title": "New MCP", "backend_url": "http://example-mcp:8000/mcp"}
    assert (await client.post("/api/admin/mcps", json={**entry, "backend_url": "file://evil/mcp"})).status_code == 422
    assert (await client.post("/api/admin/mcps", json={**entry, "backend_url": "http://10.10.52.203:8655/mcp"})).status_code == 201
    assert (await client.delete("/api/admin/mcps/new-mcp")).status_code == 204
    assert (await client.post("/api/admin/mcps", json=entry)).status_code == 201
    assert (await client.post("/api/admin/mcps", json=entry)).status_code == 409
    registry = (await client.get("/api/admin/mcps")).json()
    assert len(registry["servers"]) == 1
    assert "allowed_hosts" not in registry
    icon_path = "/api/admin/mcps/new-mcp/icon"
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M0 0h24v24H0z" fill="#123456"/></svg>'
    assert (await client.post(icon_path, files={"file": ("icon.svg", svg, "image/svg+xml")})).status_code == 200
    public = (await client.get("/api/public/mcps")).json()[0]
    assert "backend_url" not in public and "auth" not in public
    icon = await client.get(public["icon_url"])
    assert icon.status_code == 200 and "sandbox" in icon.headers["content-security-policy"]
    for content in (b"not an image", svg.replace(b"<path", b"<script"), svg.replace(b'fill="#123456"', b'fill="url(https://evil/image)"'), b"x" * (1024 * 1024 + 1)):
        assert (await client.post(icon_path, files={"file": ("icon.svg", content)})).status_code == 422
    output = BytesIO()
    Image.new("RGB", (16, 16), "red").save(output, format="PNG")
    assert (await client.post(icon_path, files={"file": ("icon.png", output.getvalue())})).status_code == 200
    assert (await client.get("/api/public/mcps/new-mcp/icon")).headers["content-type"] == "image/png"
    assert (await client.delete(icon_path)).status_code == 204
    assert (await client.get("/api/public/mcps/new-mcp/icon")).status_code == 404
    assert (await client.post("/api/admin/mcps/new-mcp", json={**entry, "enabled": False})).status_code == 200
    assert (await client.get("/api/public/mcps")).json() == []
    assert (await client.delete("/api/admin/mcps/new-mcp")).status_code == 204


@pytest.mark.asyncio
async def test_registry_preserves_conversation_history(api):
    from app.auth.jwt import current_admin
    from app.main import app
    from app.persistence.models import Conversation

    client, factory, user = api
    app.dependency_overrides[current_admin] = lambda: "admin"
    entry = {"id": "used-mcp", "title": "Used MCP", "backend_url": "http://example-mcp:8000/mcp"}
    assert (await client.post("/api/admin/mcps", json=entry)).status_code == 201
    async with factory() as session:
        session.add(Conversation(user_sub="alice", mcp_id=entry["id"], context_snapshot="context", context_sha256="f" * 64, tools_snapshot=[]))
        await session.commit()
    response = await client.delete("/api/admin/mcps/used-mcp")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "mcp_in_use"
    assert (await client.post("/api/admin/mcps/used-mcp", json={**entry, "enabled": False})).status_code == 200


@pytest.mark.asyncio
async def test_non_admin_cannot_manage_registry(api, monkeypatch):
    from app.auth.jwt import validator

    async def ordinary_claims(token, settings):
        return {"sub": "alice", "groups": []}

    monkeypatch.setattr(validator, "validate_claims", ordinary_claims)
    client, factory, user = api
    for method, path in (("GET", "/api/admin/mcps"), ("POST", "/api/admin/mcps"),
                         ("POST", "/api/admin/mcps/test-mcp"), ("DELETE", "/api/admin/mcps/test-mcp"),
                         ("POST", "/api/admin/mcps/test-mcp/icon"), ("DELETE", "/api/admin/mcps/test-mcp/icon")):
        response = await client.request(method, path, headers={"Authorization": "Bearer non-admin"})
        assert response.status_code == 403