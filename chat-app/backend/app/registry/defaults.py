from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models import MCPServer

# Hostnames resolve on the shared compose network of the root docker-compose.yml.
DEFAULT_SERVERS = (
    ("zurich-hackathon", "Zurich Hackathon 2026", "Offline handbook, schedule and challenge briefs for Zurich Hackathon 2026.", "http://zurich-hackathon:8555/mcp"),
    ("basel-parking", "Basel Parking", "Reported parking availability in Basel from the official Basel-Stadt open dataset.", "http://basel-parking:8555/mcp"),
    ("public-transport", "Kraftwerk Public Transport", "Departures and journeys from Zurich Selnau station via the Swiss Open Journey Planner.", "http://public-transport:8555/mcp"),
)


async def seed_default_servers(session: AsyncSession) -> None:
    """Populate a fresh registry only; later admin edits and deletions are respected."""
    if await session.scalar(select(func.count()).select_from(MCPServer)):
        return
    for server_id, title, description, backend_url in DEFAULT_SERVERS:
        session.add(MCPServer(id=server_id, title=title, description=description, backend_url=backend_url,
                              auth_type="none", enabled=True, approval_policy="ask_destructive"))
    await session.commit()
