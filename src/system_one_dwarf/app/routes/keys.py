"""``/api/campaigns/{id}/keys`` — store a key, read **masked** keys only.

The full key never leaves the campaign layer: the POST response echoes the masked
form, and there is no endpoint that returns key material.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ...campaign import Campaign, InvalidKey, UnknownProvider
from ..deps import get_campaign, get_services
from ..schemas import KeyOut, KeyUpsert
from ..state import AppServices

router = APIRouter(tags=["keys"])


@router.post("/api/campaigns/{campaign_id}/keys", response_model=KeyOut)
def set_key(
    body: KeyUpsert,
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
) -> dict:
    store = services.keys(campaign)
    try:
        return store.set_key(body.provider, body.key)
    except (UnknownProvider, InvalidKey) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/api/campaigns/{campaign_id}/keys", response_model=list[KeyOut])
def list_keys(
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
) -> list[dict]:
    return services.keys(campaign).list_masked()
