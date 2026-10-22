from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models import MCPServer

# Hostnames resolve on the shared compose network of the root docker-compose.yml.
DEFAULT_SERVERS = (
    ("emt-bus-stop", "Parada 72 · Cibeles", "Llegadas en tiempo real, líneas, horarios y avisos de la parada EMT 72 Cibeles - Casa de América (EMT Madrid MobilityLabs).", "http://emt-bus-stop:8555/mcp"),
    ("emt-tickets", "Billetes EMT (demo)", "Compra simulada de billetes EMT y reserva de plaza en el Exprés Aeropuerto. Demo: sin pagos ni billetes reales.", "http://emt-tickets:8555/mcp"),
    ("bicimad", "BiciMAD", "Bicis y anclajes libres en cualquier estación de BiciMAD y tarifas vigentes (GBFS público de BiciMAD).", "http://bicimad:8555/mcp"),
)


async def seed_default_servers(session: AsyncSession) -> None:
    """Populate a fresh registry only; later admin edits and deletions are respected."""
    if await session.scalar(select(func.count()).select_from(MCPServer)):
        return
    for server_id, title, description, backend_url in DEFAULT_SERVERS:
        session.add(MCPServer(id=server_id, title=title, description=description, backend_url=backend_url,
                              auth_type="none", enabled=True, approval_policy="ask_destructive"))
    await session.commit()
