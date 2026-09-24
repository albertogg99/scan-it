import pytest

from app.persistence.models import MCPServer
from app.registry.service import credential_headers, validate_backend_url, validate_entry
from app.settings import Settings


def test_exact_host_and_secret_reference(monkeypatch):
    settings = Settings(_env_file=None, mcp_allowed_hosts="museum-guide")
    for url in ("http://museum-guide.evil/mcp", "http://museum-guide@evil/mcp", "file://museum-guide/mcp", "http://museum-guide/mcp#fragment", "http://museum-guide/mcp?token=secret"):
        with pytest.raises(ValueError):
            validate_backend_url(url, settings)
    validate_backend_url("http://museum-guide:8000/mcp", settings)
    entry = {
        "id": "museum-guide", "backend_url": "http://museum-guide:8000/mcp",
        "approval_policy": "ask_destructive", "auth": {"type": "bearer", "credential_env": "MUSEUM_GUIDE_TOKEN"},
    }
    validate_entry(entry, settings)
    monkeypatch.setenv("MUSEUM_GUIDE_TOKEN", "private-value")
    server = MCPServer(auth_type="bearer", credential_env="MUSEUM_GUIDE_TOKEN")
    assert credential_headers(server) == {"Authorization": "Bearer private-value"}
    entry["auth"] = {"type": "header", "header_name": "Host", "credential_env": "MUSEUM_GUIDE_TOKEN"}
    with pytest.raises(ValueError):
        validate_entry(entry, settings)


def test_transport_disallows_redirects():
    from app.mcp_adapter.client import http_client_factory

    client = http_client_factory(Settings(_env_file=None))(follow_redirects=True)
    assert client.follow_redirects is False
    assert client._trust_env is False