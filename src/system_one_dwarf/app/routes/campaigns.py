"""``/api/campaigns`` — list, create (or attach), detail."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ...campaign import Campaign, CampaignError, InvalidSlug
from ..deps import campaign_error, get_campaign, get_services
from ..schemas import CampaignCreate, CampaignOut
from ..state import AppServices

router = APIRouter(tags=["campaigns"])


@router.get("/api/campaigns", response_model=list[CampaignOut])
def list_campaigns(services: AppServices = Depends(get_services)) -> list[dict]:
    return [campaign.to_api() for campaign in services.manager.list_campaigns()]


@router.post("/api/campaigns", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
def create_campaign(
    body: CampaignCreate,
    services: AppServices = Depends(get_services),
) -> dict:
    """Create the campaign (``is_new_game: true``) or attach to it (``false``).

    Either way a CoALA memory is guaranteed to exist for the campaign root, and a
    new game is *seeded with the objective*.
    """
    try:
        campaign, _created = services.manager.create(body.name, body.is_new_game)
    except (CampaignError, InvalidSlug) as exc:
        raise campaign_error(exc)
    except Exception as exc:  # pragma: no cover - unexpected storage failure
        raise HTTPException(status_code=500, detail=str(exc))
    return campaign.to_api()


@router.get("/api/campaigns/{campaign_id}", response_model=CampaignOut)
def get_campaign_detail(campaign: Campaign = Depends(get_campaign)) -> dict:
    return campaign.to_api()
