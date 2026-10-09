"""``/api/campaigns/{id}/objective`` — narrative + checkable subgoals.

The canonical text is **EN** (it is prompt material) and ``PUT`` translates PT →
EN before storing.  Both responses add the PT display fields
(``narrative_pt`` / ``subgoals[].text_pt``) through the campaign's translator
seam, so the UI renders what the user typed without a second source of truth.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...campaign import Campaign, CoalaError
from ..deps import campaign_error, get_campaign, get_services
from ..schemas import ObjectiveIn, ObjectiveOut
from ..state import AppServices
from ..views import objective_payload

router = APIRouter(tags=["objective"])


@router.get(
    "/api/campaigns/{campaign_id}/objective",
    response_model=ObjectiveOut,
    response_model_exclude_none=True,
)
def get_objective(
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
) -> dict:
    try:
        return objective_payload(services, campaign)
    except CoalaError as exc:
        raise campaign_error(exc)


@router.put(
    "/api/campaigns/{campaign_id}/objective",
    response_model=ObjectiveOut,
    response_model_exclude_none=True,
)
def put_objective(
    body: ObjectiveIn,
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
) -> dict:
    """Replace the whole objective (narrative + subgoals) and persist it."""
    try:
        services.objective(campaign).put(
            body.narrative, [subgoal.model_dump() for subgoal in body.subgoals]
        )
        return objective_payload(services, campaign)
    except CoalaError as exc:
        raise campaign_error(exc)