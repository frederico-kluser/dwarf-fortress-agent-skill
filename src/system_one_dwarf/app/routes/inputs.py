"""``POST /api/campaigns/{id}/inputs`` — free information injected by the user.

The text is translated PT -> EN (prompts are always EN) and stored both in the
live store and in the campaign memory as an ``episodic``/``user_input`` record, so
the agent loop sees it on its next retrieval.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, status

from ...campaign import Campaign, CoalaError
from ...campaign.memory import TAG_USER_INPUT, safe_translate
from ..deps import get_campaign, get_services
from ..schemas import InputCreate, InputOut
from ..state import AppServices

log = logging.getLogger("system_one_dwarf.app.routes.inputs")

router = APIRouter(tags=["inputs"])


@router.post(
    "/api/campaigns/{campaign_id}/inputs",
    response_model=InputOut,
    status_code=status.HTTP_201_CREATED,
)
def add_input(
    body: InputCreate,
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
) -> dict:
    text_en = safe_translate(services.translator_for(campaign), body.text.strip(), "en")
    runtime = services.runtime(campaign.id)
    item = runtime.add_input(text_en)
    try:
        services.memory(campaign).add_episode(
            text_en,
            key=f"input:{item['id']}",
            tags=TAG_USER_INPUT,
            origin="owner",
            source="ui:inputs",
        )
    except CoalaError as exc:  # never lose the user's input over a memory hiccup
        log.warning("could not persist input %s in the campaign memory: %s", item["id"], exc)
    return item
