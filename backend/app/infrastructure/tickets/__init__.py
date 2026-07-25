"""TicketClient adapters (infrastructure implementations of `domain.incidents.ports.TicketClient`)."""

from app.infrastructure.tickets.ado_client import AdoTicketClient

__all__ = ["AdoTicketClient"]
