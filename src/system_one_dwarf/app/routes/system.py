"""``/api/system`` — estado do sistema e o processo do jogo (completa o "fazer tudo").

* ``GET  /api/system/status`` — ``{game, backend, frontend, loop}``: o jogo
  (``running``/``pid``/``rpc``/``path``), a versão do backend, o frontend (sonda
  TCP da porta do Vite) e os loops de decisão por campanha;
* ``POST /api/system/game`` ``{"action": "start"|"stop"}`` — arranca/pára **o
  processo do jogo**, com o mesmo lançamento do ``scripts/sod.sh``
  (``SDL_AUDIODRIVER=dummy SDL_VIDEODRIVER=x11 LIBGL_ALWAYS_SOFTWARE=1 ./dfhack``
  destacado com log) e o escalonamento de paragem provado (TERM→KILL por grupo,
  porque o ``dwarfort`` ignora SIGTERM).

Guarda: o gestor só gere o jogo no **caminho configurado** (não há caminho no
pedido — ver ``GameProcessManager``).  ``GameProcessError`` (ex.: lançador
ausente) responde ``503`` com ``{"detail": ...}``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ...game.driver import GameProcessError
from ..deps import get_services
from ..schemas import GameActionOut, GameActionRequest, SystemStatusOut
from ..state import AppServices

router = APIRouter(tags=["system"])


@router.get("/api/system/status", response_model=SystemStatusOut)
def system_status(services: AppServices = Depends(get_services)) -> dict:
    return services.system_status()


@router.post("/api/system/game", response_model=GameActionOut)
def game_action(
    body: GameActionRequest,
    services: AppServices = Depends(get_services),
) -> dict:
    try:
        return services.game_action(body.action)
    except GameProcessError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
