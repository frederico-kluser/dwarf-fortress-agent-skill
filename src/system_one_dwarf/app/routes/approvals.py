"""``/api/campaigns/{id}/approvals`` — the checkpoint approval surface.

``uxui-audit-ui.json`` finding #4: the ``checkpoint`` mode promised human
approval of every action but had no surface.  This router adds it (additive REST
endpoints, ``docs/api.md``):

* ``GET .../approvals`` — the campaign's approvals, ``pending`` by default
  (``?status=all|approved|rejected`` for the audit trail).  Empty in autopilot,
  because the loop only holds actions for permission in checkpoint mode;
* ``POST .../approvals/{id}/approve`` — releases the decision: the underlying
  question is answered through the normal question path (``yes``) and the answer
  is persisted in the campaign memory;
* ``POST .../approvals/{id}/reject`` — drops the action, records the refusal
  (``no``) and the optional ``note`` in the same place.

Status codes: ``404`` unknown approval, ``409`` already decided, both with the
contract's ``{"detail": "..."}`` body.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Response

from ...campaign import Campaign
from ..approval_flow import ApprovalAlreadyDecided, ApprovalNotFound, decide_approval
from ..deps import get_campaign, get_runtime, get_services, tag_source
from ..runtime import APPROVAL_PENDING
from ..schemas import ApprovalDecision, ApprovalOut
from ..state import AppServices

router = APIRouter(tags=["approvals"])

ApprovalFilter = Literal["pending", "approved", "rejected", "all"]


def _decide_response(exc: Exception) -> HTTPException:
    if isinstance(exc, ApprovalNotFound):
        return HTTPException(status_code=404, detail="approval not found")
    if isinstance(exc, ApprovalAlreadyDecided):
        return HTTPException(status_code=409, detail=f"approval already {exc.status}")
    return HTTPException(status_code=500, detail=str(exc))  # pragma: no cover - defensive


@router.get(
    "/api/campaigns/{campaign_id}/approvals",
    response_model=list[ApprovalOut],
    response_model_exclude_none=True,
)
def list_approvals(
    response: Response,
    status: ApprovalFilter = "pending",
    campaign: Campaign = Depends(get_campaign),
    runtime=Depends(get_runtime),
) -> list[dict]:
    """Pending approvals (default) or the whole history with ``?status=all``."""
    tag_source(response, runtime.source_of("approvals"))
    wanted: str | None = None if status == "all" else status
    return runtime.list_approvals(status=wanted)


@router.post(
    "/api/campaigns/{campaign_id}/approvals/{approval_id}/approve",
    response_model=ApprovalOut,
    response_model_exclude_none=True,
)
async def approve(
    approval_id: str,
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
    body: ApprovalDecision | None = Body(default=None),
) -> dict:
    try:
        return await decide_approval(
            services, campaign, approval_id, approve=True, note=body.note if body else None
        )
    except (ApprovalNotFound, ApprovalAlreadyDecided) as exc:
        raise _decide_response(exc)


@router.post(
    "/api/campaigns/{campaign_id}/approvals/{approval_id}/reject",
    response_model=ApprovalOut,
    response_model_exclude_none=True,
)
async def reject(
    approval_id: str,
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
    body: ApprovalDecision | None = Body(default=None),
) -> dict:
    try:
        return await decide_approval(
            services, campaign, approval_id, approve=False, note=body.note if body else None
        )
    except (ApprovalNotFound, ApprovalAlreadyDecided) as exc:
        raise _decide_response(exc)


__all__ = ["APPROVAL_PENDING", "ApprovalStatus", "router"]