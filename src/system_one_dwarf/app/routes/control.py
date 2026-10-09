"""``POST /api/campaigns/{id}/control`` — autonomy mode + kill-switch.

Persisted in the campaign's ``meta.json`` and echoed to every WS client as the
contract event ``control_state`` (payload ``{mode, kill_switch}``).

The mode also *drives the decision loop*: ``autopilot``/``checkpoint`` start it,
``paused``/kill-switch stop it (``autopilot`` and ``checkpoint`` differ only in
whether held actions become approvals — see ``docs/api.md``).  The loop sync runs
in a threadpool: stopping waits (bounded) for the loop's worker thread, which
must never block the event loop.  The response body stays exactly
``{mode, kill_switch, ts}``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

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
    await run_in_threadpool(
        services.sync_loop,
        campaign,
        {"mode": payload["mode"], "kill_switch": payload["kill_switch"]},
    )
    await services.publish_event(
        campaign.id,
        "control_state",
        {"mode": payload["mode"], "kill_switch": payload["kill_switch"]},
    )
    return payload
