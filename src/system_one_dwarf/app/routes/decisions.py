"""``GET /api/campaigns/{id}/decisions`` — JEV decision log.

Fed by the bus: anything published as a ``decision`` event (or pushed through
``AppServices.publish_event``) shows up here.  Empty until the agent loop runs —
the array shape is frozen by the contract, so the stub marker travels in the
``X-Source: stub`` header (there is nowhere to put it inside a JSON array).

The additive ``?limit=&before=`` pair paginates the log (``docs/api.md``):
``limit`` keeps the most recent entries (oldest-first in the answer) and
``before`` is an exclusive ISO-8601 cursor to walk history backwards.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ...campaign import Campaign
from ..deps import get_campaign, get_runtime, tag_source
from ..runtime import CampaignRuntime
from ..schemas import DecisionOut

router = APIRouter(tags=["decisions"])


@router.get("/api/campaigns/{campaign_id}/decisions", response_model=list[DecisionOut])
def list_decisions(
    response: Response,
    limit: int | None = Query(default=None, ge=1, description="máximo de registos (os mais recentes)"),
    before: str | None = Query(
        default=None, description="cursor ISO-8601: só registos estritamente anteriores"
    ),
    campaign: Campaign = Depends(get_campaign),
    runtime: CampaignRuntime = Depends(get_runtime),
) -> list[dict]:
    tag_source(response, runtime.source_of("decisions"))
    try:
        return runtime.list_decisions(limit=limit, before=before)
    except ValueError:
        raise HTTPException(status_code=422, detail="before must be an ISO-8601 timestamp")
