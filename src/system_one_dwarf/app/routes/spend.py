"""``GET /api/campaigns/{id}/spend`` — total spend, no cap, per provider/model."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...campaign import Campaign
from ..deps import get_campaign, get_services
from ..schemas import SpendOut
from ..state import AppServices

router = APIRouter(tags=["spend"])


@router.get("/api/campaigns/{campaign_id}/spend", response_model=SpendOut)
def get_spend(
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
) -> dict:
    return services.spend(campaign).totals()
