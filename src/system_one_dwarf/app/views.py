"""Read-model builders shared by the REST routes and the WebSocket broadcasts.

Keeping them here (instead of inside one route module) means ``memory_update``
events and ``GET .../memory`` can never drift apart.
"""

from __future__ import annotations

from typing import Any

from ..campaign import Campaign, MemoryStore
from .state import AppServices

EPISODE_LIMIT = 50


def memory_payload(services: AppServices, campaign: Campaign) -> dict[str, Any]:
    """Exactly the ``GET /api/campaigns/{id}/memory`` object."""
    memory: MemoryStore = services.memory(campaign)
    narrative = memory.narrative()
    return {
        "narrative": narrative,
        "narrative_pt": memory.narrative_pt(),
        "episodes": memory.episodes(limit=EPISODE_LIMIT),
        "working_memory": memory.working_memory(),
        "updated_at": memory.updated_at() or campaign.updated_at,
    }
