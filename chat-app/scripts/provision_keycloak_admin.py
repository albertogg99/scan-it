import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def provision():
    base_url = os.environ.get("KEYCLOAK_INTERNAL_URL", "http://keycloak:8080").rstrip("/")
    realm_file = Path(os.environ.get("KEYCLOAK_REALM_FILE", "/setup/realm.json"))
    realm = json.loads(realm_file.read_text(encoding="utf-8"))
    credentials = urlencode({"grant_type": "password", "client_id": "admin-cli",
                             "username": os.environ["KEYCLOAK_ADMIN_USERNAME"],
                             "password": os.environ["KEYCLOAK_ADMIN_PASSWORD"]}).encode()
    token_request = Request(f"{base_url}/realms/master/protocol/openid-connect/token", data=credentials)
    with urlopen(token_request, timeout=30) as response:
        token = json.load(response)["access_token"]

    def api(path, method="GET", payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        url = f"{base_url}/admin/realms/{realm['realm']}" + (f"/{path}" if path else "")
        request = Request(url, data=body, method=method,
                          headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        with urlopen(request, timeout=30) as response:
            content = response.read()
            return json.loads(content) if content else None

    lifespans = {key: realm[key] for key in ("accessTokenLifespan", "ssoSessionIdleTimeout", "ssoSessionMaxLifespan") if key in realm}
    if lifespans:
        api("", "PUT", lifespans)
    groups = api("groups?search=admin&exact=true")
    group = next((group for group in groups if group["path"] == "/admin"), None)
    if group is None:
        api("groups", "POST", {"name": "admin"})
        group = next(group for group in api("groups?search=admin&exact=true") if group["path"] == "/admin")
    client = api("clients?clientId=swiss-discover-web")[0]
    public_url = os.environ.get("PUBLIC_FRONTEND_URL", "").rstrip("/")
    if public_url and (f"{public_url}/*" not in client.get("redirectUris", [])
                       or public_url not in client.get("webOrigins", [])):
        client["redirectUris"] = sorted({*client.get("redirectUris", []), f"{public_url}/*"})
        client["webOrigins"] = sorted({*client.get("webOrigins", []), public_url})
        api(f"clients/{client['id']}", "PUT", client)
    mapper = next(mapper for mapper in realm["clients"][0]["protocolMappers"] if mapper["name"] == "swiss-discover-groups")
    mapper_path = f"clients/{client['id']}/protocol-mappers/models"
    existing = next((item for item in api(mapper_path) if item["name"] == mapper["name"]), None)
    if existing:
        api(f"{mapper_path}/{existing['id']}", "PUT", {**mapper, "id": existing["id"]})
    else:
        api(mapper_path, "POST", mapper)
    users = api("users?username=admin&exact=true")
    if not users:
        user = realm["users"][0]
        user["credentials"][0]["value"] = os.environ.get("SWISS_DISCOVER_ADMIN_PASSWORD", "admin")
        api("users", "POST", user)
        users = api("users?username=admin&exact=true")
    api(f"users/{users[0]['id']}/groups/{group['id']}", "PUT")
    print("Scan it! admin group, token mapper, and account are configured. Existing passwords are unchanged.")


if __name__ == "__main__":
    try:
        provision()
    except HTTPError as exc:
        raise SystemExit(f"Keycloak admin setup failed with HTTP {exc.code}; check service credentials and realm configuration.") from None