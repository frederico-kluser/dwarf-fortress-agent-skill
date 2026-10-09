"""``GET /api/campaigns/{id}/metrics`` — the 8 live metric groups.

Stub contract: until the agent loop publishes ``metrics_update``, the payload is
zeroed and marked ``"source": "stub"`` (never 501, never a broken shape).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from ...campaign import Campaign
from ..deps import get_campaign, get_runtime, tag_source
from ..runtime import CampaignRuntime
from ..schemas import MetricsOut

router = APIRouter(tags=["metrics"])


@router.get("/api/campaigns/{campaign_id}/metrics", response_model=MetricsOut)
def get_metrics(
    response: Response,
    campaign: Campaign = Depends(get_campaign),
    runtime: CampaignRuntime = Depends(get_runtime),
) -> dict:
    payload = runtime.get_metrics()
    tag_source(response, str(payload.get("source") or "stub"))
    return payload
