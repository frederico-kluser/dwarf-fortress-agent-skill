"""``/api/campaigns/{id}/memory`` — read the campaign memory, edit it in PT.

Canonical storage is EN; the editor shows PT.  ``PATCH`` accepts Portuguese and
the server translates PT -> EN before writing (``Translator`` protocol, identity
until the DeepSeek translator piece is plugged in).

The ``memory/*`` sub-routes expose the «memória viva» (Onda 2, Passos 7+9):
retrievable dialogue (``dialogue``), structured events (``logbook``), citable
derived facts (``extractions``), the CoALA episodic layer (``episodes``) and the
combined conversation context (``context``) — all read-only views over
``CampaignManager.bundle``; the writes happen inside the loop (``bundle.observe``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

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


# ------------------------------------------------------- memória viva (Onda 2)


@router.get("/api/campaigns/{campaign_id}/memory/context")
def get_memory_context(
    situation: str = Query("", description="Current situation (EN) used for retrieval"),
    interlocutor: str | None = Query(None, description="NPC name/key to scope the dialogue block"),
    k: int = Query(3, ge=1, le=20),
    budget: int = Query(1200, ge=100, le=8000),
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
) -> dict:
    """The conversation context block (DATA-labelled) for a situation/NPC."""
    bundle = services.manager.bundle(campaign)
    return {
        "situation": situation,
        "interlocutor": interlocutor,
        "block": bundle.context(situation, interlocutor=interlocutor, k=k, char_budget=budget),
    }


@router.get("/api/campaigns/{campaign_id}/memory/dialogue")
def get_dialogue_memory(
    query: str = Query("", description="Situation/query for the composite score"),
    interlocutor: str | None = Query(None),
    k: int = Query(5, ge=1, le=50),
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
) -> dict:
    """Transcribed speech (recency + importance + similarity) + the NPC profile card."""
    dialogue = services.manager.dialogue(campaign)
    profile = dialogue.profile(interlocutor) if interlocutor else None
    return {
        "items": dialogue.memory_items(query, interlocutor=interlocutor, k=k),
        "profile": profile.as_dict() if profile is not None else None,
    }


@router.get("/api/campaigns/{campaign_id}/memory/logbook")
def get_logbook_events(
    query: str = Query("", description="Situation/query for the composite score"),
    k: int = Query(5, ge=1, le=50),
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
) -> dict:
    """Structured append-only events (recency + importance + relevance)."""
    logbook = services.manager.logbook(campaign)
    return {"items": logbook.memory_items(query, k=k)}


@router.get("/api/campaigns/{campaign_id}/memory/extractions")
def get_extractions(
    query: str = Query("", description="Situation/query for the composite score"),
    interlocutor: str | None = Query(None),
    k: int = Query(5, ge=1, le=50),
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
) -> dict:
    """Derived facts/promises — every item cites its source message/episode."""
    extraction = services.manager.extraction(campaign)
    return {"items": extraction.memory_items(query, k=k, interlocutor=interlocutor)}


@router.get("/api/campaigns/{campaign_id}/memory/episodes")
def get_episodes(
    query: str = Query("", description="Situation/query for the composite score"),
    k: int = Query(5, ge=1, le=50),
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
) -> dict:
    """CoALA episodic layer retrieved by recency + importance + relevance."""
    episodes = services.manager.episodes(campaign)
    return {"items": episodes.memory_items(query, k=k)}
