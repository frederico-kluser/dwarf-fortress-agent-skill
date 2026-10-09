"""``GET /api/campaigns/{id}/screen`` — screen text, **PT only**.

The original (EN) text is never returned.  Until the agent loop publishes a
``screen_translation`` event the payload is empty and marked ``source: stub``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from ...campaign import Campaign
from ..deps import get_campaign, get_runtime, tag_source
from ..runtime import CampaignRuntime
from ..schemas import ScreenOut

router = APIRouter(tags=["screen"])


@router.get("/api/campaigns/{campaign_id}/screen", response_model=ScreenOut)
def get_screen(
    response: Response,
    campaign: Campaign = Depends(get_campaign),
    runtime: CampaignRuntime = Depends(get_runtime),
) -> dict:
    payload = runtime.get_screen()
    tag_source(response, str(payload.get("source") or "stub"))
    return payload
