"""``GET /api/campaigns/{id}/decisions`` — JEV decision log.

Fed by the bus: anything published as a ``decision`` event (or pushed through
``AppServices.publish_event``) shows up here.  Empty until the agent loop runs —
the array shape is frozen by the contract, so the stub marker travels in the
``X-Source: stub`` header (there is nowhere to put it inside a JSON array).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from ...campaign import Campaign
from ..deps import get_campaign, get_runtime, tag_source
from ..runtime import CampaignRuntime
from ..schemas import DecisionOut

router = APIRouter(tags=["decisions"])


@router.get("/api/campaigns/{campaign_id}/decisions", response_model=list[DecisionOut])
def list_decisions(
    response: Response,
    campaign: Campaign = Depends(get_campaign),
    runtime: CampaignRuntime = Depends(get_runtime),
) -> list[dict]:
    tag_source(response, runtime.source_of("decisions"))
    return runtime.list_decisions()
