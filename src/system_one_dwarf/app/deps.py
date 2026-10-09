"""FastAPI dependencies: resolve services, campaigns and contract error mapping."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, Response, status

from ..campaign import (
    Campaign,
    CampaignError,
    CampaignNotFound,
    CoalaError,
    CoalaMissing,
    InstallerMissing,
    InvalidSlug,
)
from .runtime import CampaignRuntime
from .state import AppServices

CAMPAIGN_NOT_FOUND = "campaign not found"


def get_services(request: Request) -> AppServices:
    """The app-wide services object (``app.state.services``)."""
    return request.app.state.services


def get_campaign(
    campaign_id: str,
    services: AppServices = Depends(get_services),
) -> Campaign:
    """Resolve ``{id}`` (opaque UUID4 or slug) or fail with the contract 404."""
    try:
        return services.resolve(campaign_id)
    except CampaignNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=CAMPAIGN_NOT_FOUND)


def get_runtime(
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
) -> CampaignRuntime:
    return services.runtime(campaign.id)


def campaign_error(exc: Exception) -> HTTPException:
    """Map campaign-layer failures onto contract-shaped HTTP errors.

    ``{"detail": "<mensagem>"}`` with an adequate code — 404 unknown campaign,
    422 invalid slug/provider/key, 503 a broken CoALA install (a real missing
    dependency, with the missing path spelled out), 500 anything else.
    """
    if isinstance(exc, CampaignNotFound):
        return HTTPException(status_code=404, detail=CAMPAIGN_NOT_FOUND)
    if isinstance(exc, InvalidSlug):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, CoalaMissing):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, CoalaError):
        return HTTPException(status_code=503, detail=f"campaign memory unavailable: {exc}")
    if isinstance(exc, InstallerMissing):
        return HTTPException(status_code=500, detail=str(exc))
    if isinstance(exc, CampaignError):
        return HTTPException(status_code=500, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


def tag_source(response: Response, source: str) -> None:
    """Mark stub-backed reads.

    Object payloads carry ``"source": "stub"``; list payloads cannot (the array
    shape is frozen by the contract), so they advertise it in this header.
    """
    response.headers["X-Source"] = source
