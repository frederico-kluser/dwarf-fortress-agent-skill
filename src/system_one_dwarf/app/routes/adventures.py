"""``/api/campaigns/{id}/adventures`` — escolher ou criar aventura **a partir da UI**.

Cinco pontos de entrada (contrato em ``docs/api.md``):

* ``GET  .../adventures``          — saves da pasta ``data/save`` + ``current``;
* ``POST .../adventures/new-world``     — ``202`` tarefa assíncrona de worldgen;
* ``POST .../adventures/new-character`` — ``202`` tarefa de criação de personagem;
* ``POST .../adventures/load``          — ``202`` tarefa de carga de um save;
* ``GET  .../adventures/status``   — progresso ao vivo da tarefa + ``current``;
* ``POST .../adventures/cancel``   — cancela a tarefa em curso (deixa de enviar teclas).

Os POSTs arrancam **uma tarefa de cada vez por campanha** (``409`` quando já há
uma em curso) e respondem ``202 {"task_id", "kind"}``; o progresso lê-se em
``/adventures/status`` e chega também como evento WS ``adventure_task``.

As leituras resolvem o ``current`` pelo último snapshot (best-effort: sem jogo
ou sem aventureiro → ``null``).  Tudo é síncrono em def (a FastAPI corre na
threadpool): as chamadas à ponte nunca bloqueiam o event loop.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from ...campaign import Campaign
from ..adventure_flow import TASK_LOAD, TASK_NEW_CHARACTER, TASK_NEW_WORLD, TaskBusyError
from ..deps import get_campaign, get_services
from ..schemas import (
    AdventureCancelOut,
    AdventureStatusOut,
    AdventuresOut,
    LoadAdventureRequest,
    NewCharacterRequest,
    NewWorldRequest,
    TaskAccepted,
)
from ..state import AppServices

log = logging.getLogger("system_one_dwarf.app.routes.adventures")

#: O contrato não fixa o código do "save desconhecido": 422 (pedido inválido).
UNKNOWN_ADVENTURE = 422

router = APIRouter(tags=["adventures"])


def _start_task(
    services: AppServices, campaign: Campaign, kind: str, params: dict
) -> dict[str, str]:
    try:
        return services.adventure_tasks().start(campaign.id, kind, params)
    except TaskBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


def _current(services: AppServices, campaign: Campaign):
    """``current`` do contrato (último snapshot) — best-effort, nunca 500."""
    driver = services.adventure_driver(campaign)
    try:
        return driver.current_state(
            adventure_id=services.adventure_tasks().last_adventure_id(campaign.id)
        )
    except Exception:  # pragma: no cover - sem jogo a leitura degrada para null
        log.warning("could not read the current adventurer of %s", campaign.id, exc_info=True)
        return None


@router.get("/api/campaigns/{campaign_id}/adventures", response_model=AdventuresOut)
def list_adventures(
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
) -> dict:
    driver = services.adventure_driver(campaign)
    try:
        adventures = driver.list_adventures()
    except Exception:  # pragma: no cover - pasta de saves ilegível degrada para []
        log.warning("could not list the saves of %s", campaign.id, exc_info=True)
        adventures = []
    return {"adventures": adventures, "current": _current(services, campaign)}


@router.post(
    "/api/campaigns/{campaign_id}/adventures/new-world",
    status_code=202,
    response_model=TaskAccepted,
)
def new_world(
    body: NewWorldRequest,
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
) -> dict[str, str]:
    return _start_task(
        services,
        campaign,
        TASK_NEW_WORLD,
        {"world_name": body.world_name, "params": body.params},
    )


@router.post(
    "/api/campaigns/{campaign_id}/adventures/new-character",
    status_code=202,
    response_model=TaskAccepted,
)
def new_character(
    body: NewCharacterRequest,
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
) -> dict[str, str]:
    return _start_task(
        services,
        campaign,
        TASK_NEW_CHARACTER,
        {
            "name": body.name,
            "race": body.race,
            "origin": body.origin,
            "randomize": body.randomize,
        },
    )


@router.post(
    "/api/campaigns/{campaign_id}/adventures/load",
    status_code=202,
    response_model=TaskAccepted,
)
def load_adventure(
    body: LoadAdventureRequest,
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
) -> dict[str, str]:
    driver = services.adventure_driver(campaign)
    try:
        known = {str(a.get("id")) for a in driver.list_adventures()}
    except Exception:  # pragma: no cover - sem pasta de saves nada é carregável
        log.warning("could not list the saves of %s", campaign.id, exc_info=True)
        known = set()
    if body.adventure_id not in known:
        raise HTTPException(
            status_code=UNKNOWN_ADVENTURE,
            detail=f"aventura desconhecida: {body.adventure_id}",
        )
    return _start_task(services, campaign, TASK_LOAD, {"adventure_id": body.adventure_id})


@router.get(
    "/api/campaigns/{campaign_id}/adventures/status",
    response_model=AdventureStatusOut,
)
def adventure_status(
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
) -> dict:
    payload = services.adventure_tasks().status(campaign.id)
    payload["current"] = _current(services, campaign)
    return payload


@router.post(
    "/api/campaigns/{campaign_id}/adventures/cancel",
    response_model=AdventureCancelOut,
)
def cancel_adventure(
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
) -> dict:
    return services.adventure_tasks().cancel(campaign.id)
