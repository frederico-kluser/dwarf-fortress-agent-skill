"""``/api/campaigns/{id}/objective`` — narrative + checkable subgoals."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...campaign import Campaign, CoalaError
from ..deps import campaign_error, get_campaign, get_services
from ..schemas import ObjectiveIn, ObjectiveOut
from ..state import AppServices

router = APIRouter(tags=["objective"])


@router.get("/api/campaigns/{campaign_id}/objective", response_model=ObjectiveOut)
def get_objective(
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
) -> dict:
    try:
        return services.objective(campaign).get()
    except CoalaError as exc:
        raise campaign_error(exc)


@router.put("/api/campaigns/{campaign_id}/objective", response_model=ObjectiveOut)
def put_objective(
    body: ObjectiveIn,
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
) -> dict:
    """Replace the whole objective (narrative + subgoals) and persist it."""
    try:
        return services.objective(campaign).put(
            body.narrative, [subgoal.model_dump() for subgoal in body.subgoals]
        )
    except CoalaError as exc:
        raise campaign_error(exc)
