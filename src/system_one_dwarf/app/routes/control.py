"""``POST /api/campaigns/{id}/control`` — autonomy mode + kill-switch.

Persisted in the campaign's ``meta.json`` and echoed to every WS client as the
contract event ``control_state`` (payload ``{mode, kill_switch}``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...campaign import Campaign, CampaignError
from ..deps import campaign_error, get_campaign, get_services
from ..schemas import ControlRequest, ControlResponse
from ..state import AppServices

router = APIRouter(tags=["control"])


@router.post("/api/campaigns/{campaign_id}/control", response_model=ControlResponse)
async def set_control(
    body: ControlRequest,
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
) -> dict:
    try:
        payload = services.manager.set_control(
            campaign, mode=body.mode, kill_switch=body.kill_switch
        )
    except CampaignError as exc:
        raise campaign_error(exc)
    await services.publish_event(
        campaign.id,
        "control_state",
        {"mode": payload["mode"], "kill_switch": payload["kill_switch"]},
    )
    return payload
