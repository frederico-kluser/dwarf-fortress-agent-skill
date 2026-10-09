"""``/api/campaigns/{id}/memory`` — read the campaign memory, edit it in PT.

Canonical storage is EN; the editor shows PT.  ``PATCH`` accepts Portuguese and
the server translates PT -> EN before writing (``Translator`` protocol, identity
until the DeepSeek translator piece is plugged in).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...campaign import Campaign, CoalaError
from ..deps import campaign_error, get_campaign, get_services
from ..schemas import MemoryOut, MemoryPatch
from ..state import AppServices
from ..views import memory_payload

router = APIRouter(tags=["memory"])


@router.get("/api/campaigns/{campaign_id}/memory", response_model=MemoryOut)
def get_memory(
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
) -> dict:
    try:
        return memory_payload(services, campaign)
    except CoalaError as exc:
        raise campaign_error(exc)


@router.patch("/api/campaigns/{campaign_id}/memory", response_model=MemoryOut)
def patch_memory(
    body: MemoryPatch,
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
) -> dict:
    """Partial update: send only ``narrative`` and/or ``episodes``."""
    memory = services.memory(campaign)
    try:
        if body.narrative is not None:
            memory.set_narrative(body.narrative)
        for episode in body.episodes or []:
            content = (episode.content or "").strip()
            if not content:
                continue
            memory.save_episode(content, entry_id=episode.id)
        return memory_payload(services, campaign)
    except CoalaError as exc:
        raise campaign_error(exc)
