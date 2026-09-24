import os
import re
from pathlib import Path

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models import MCPServer
from app.persistence.session import session_factory
from app.registry.service import validate_entry
from app.settings import Settings, get_settings

VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def load_seed(path: str, settings: Settings) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8")

    def substitute(match: re.Match) -> str:
        name, default = match.groups()
        if name not in os.environ and default is None:
            raise ValueError(f"Missing seed environment variable: {name}")
        return os.environ.get(name, default or "")

    data = yaml.safe_load(VARIABLE.sub(substitute, text))
    if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
        raise ValueError("Registry seed must contain a servers list")
    for entry in data["servers"]:
        validate_entry(entry, settings)
    return data["servers"]


async def seed_registry(session: AsyncSession, entries: list[dict], settings: Settings) -> None:
    for entry in entries:
        validate_entry(entry, settings)
        server = await session.get(MCPServer, entry["id"])
        if server is None:
            server = MCPServer(id=entry["id"])
            session.add(server)
        auth = entry["auth"]
        server.title = entry["title"]
        server.description = entry["description"]
        server.backend_url = entry["backend_url"]
        server.public_url = entry.get("public_url") or None
        server.auth_type = auth["type"]
        server.auth_header_name = auth.get("header_name") if auth["type"] == "header" else None
        server.credential_env = auth.get("credential_env") if auth["type"] != "none" else None
        server.enabled = entry["enabled"]
        server.approval_policy = entry["approval_policy"]
    await session.commit()


async def main():
    settings = get_settings()
    entries = load_seed(settings.registry_seed_path, settings)
    async with session_factory() as session:
        await seed_registry(session, entries, settings)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())