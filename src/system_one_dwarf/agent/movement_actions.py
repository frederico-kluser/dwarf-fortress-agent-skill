"""Candidatos de MACRO-ação de movimento: o JEV decide **destino + urgência**.

Mudança 04 (``ARQUITETURA.md`` §8 #04): a missão ``caca-2`` gastou 415 decisões
JEV-A em micro-movimento (349 em ``abstain``). Com macro-ações o modelo escolhe
**para onde** ir (``navigate_to:<zona>``, ``explore:<rumo>``, ``flee:<rumo>``,
``hunt:search_prey``) e **com que urgência** (a régua ``Safe``/``Pressing``/
``Critical``); o código faz o caminho — o ``MacroExecutor`` constrói a rota com
:func:`.movement.build_route` e caminha-a *leg* a *leg*.

Este módulo é o GERADOR do lado do movimento (o par de
:func:`.candidates.generate_actions`, que continua a gerar as ações não-movimento
— combate, itens, conversa, necessidades). Cada ação sai com:

* ``params["macro"] = True`` — marca que o executor resolve em rota;
* ``params["urgency"]`` — o nível por omissão (:data:`.movement.DEFAULT_URGENCY`);
  a resposta ``score`` do JEV-A sobrepõe-se via :func:`apply_urgency` ou no
  ``urgency=`` de :meth:`.executor.MacroExecutor.execute`;
* ``params["route"]`` — pré-visualização JSON-safe da rota
  (:meth:`.movement.RoutePlan.to_params`, com a urgência da geração); o executor
  **reconstrói** a rota com a posição fresca do estado e a urgência decidida;
* ``description_en`` com :func:`.movement.route_summary` — a linha ``"Travel ~24
  tiles NE in 4 legs, re-assessing between legs"`` que o JEV-A (e o log) veem.

API (nada aqui é ligado ao loop ainda — o *wiring* é uma peça posterior)::

    actions = movement_actions(state, objective=objective, config=config)
    action  = apply_urgency(action, answers["urgency"])      # urgência decidida
    result  = executor.execute(action, state, urgency=answers["urgency"])

Determinístico (sem ``random``/relógio/I/O): mesmo estado ⇒ mesmos candidatos,
na mesma ordem — a decisão é auditável e reproduzível. Nunca levanta sobre
estado lixo (``TypeError`` só para ``state`` que não é :class:`GameState`).
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Mapping, Sequence

from ..game.models import GameState
from .candidates import direction_from_text, normalise_direction, opposite_direction, wants_hunt
from .movement import (
    build_route,
    observe_state,
    route_summary,
    urgency_level,
)
from .types import MacroAction

__all__ = [
    "MOVEMENT_ACTION_LIMIT",
    "MOVEMENT_VERBS",
    "apply_urgency",
    "macro_movement_params",
    "movement_actions",
    "movement_targets",
]

#: Verbos de movimento que este gerador cobre (espelho de ``movement.MOVEMENT_VERBS``).
MOVEMENT_VERBS: tuple[str, ...] = ("explore", "navigate_to", "hunt", "flee")

#: Teto por omissão dos candidatos de movimento (o ``choice`` degrada com volume).
MOVEMENT_ACTION_LIMIT = 8

_SLUG_KEEP = re.compile(r"[^a-z0-9]+")
_FLEE_PRIORITY, _HUNT_PRIORITY, _NAVIGATE_PRIORITY, _EXPLORE_PRIORITY = 90, 30, 40, 100


# ------------------------------------------------------------------ helpers


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _slug(value: Any, fallback: str) -> str:
    """Slug curto do nome do jogo (o ``id`` do candidato — nunca vai para o DSL)."""
    text = _SLUG_KEEP.sub("_", _text(value).lower()).strip("_")
    return (text or fallback)[:48]


def _unique(candidate_id: str, taken: set[str]) -> str:
    if candidate_id not in taken:
        taken.add(candidate_id)
        return candidate_id
    index = 2
    while f"{candidate_id}_{index}" in taken:
        index += 1
    final = f"{candidate_id}_{index}"
    taken.add(final)
    return final


def _prey_visible(state: GameState) -> bool:
    """Há presa (animal não-hostil visível) à vista? (classificação do ``dfb-state``)."""
    units = getattr(getattr(state.metrics, "units_nearby", None), "units", ()) or ()
    return any(getattr(unit, "kind", None) == "animal" and getattr(unit, "visible", True)
               and not getattr(unit, "hostile", False) for unit in units)


def _macro_params_with_plan(
    params: Mapping[str, Any], origin: Any, urgency: Any, step: int, *,
    verb: str, goal_name: str | None, avoid: Sequence[Any], bounds: Any,
) -> tuple[dict[str, Any], Any]:
    """``(params da macro-ação, RoutePlan | None)`` — ver :func:`macro_movement_params`."""
    out = dict(params or {})
    level = urgency_level(urgency)
    out["macro"] = True
    out["urgency"] = level
    try:
        plan = build_route(out, origin, level, step, verb=verb, goal_name=goal_name,
                           avoid=avoid, bounds=bounds)
    except Exception:  # noqa: BLE001 - candidato nunca derruba a geração
        plan = None
    if plan is not None:
        out["route"] = plan.to_params()
    return out, plan


def macro_movement_params(
    params: Mapping[str, Any], origin: Any, urgency: Any = None, step: int = 0, *,
    verb: str, goal_name: str | None = None, avoid: Sequence[Any] = (), bounds: Any = None,
) -> dict[str, Any]:
    """``params`` da macro-ação de movimento: campos do destino + ``macro``/``urgency``/``route``.

    A rota é só **pré-visualização** (``params["route"]``): o executor reconstrói-a
    com a posição fresca e a urgência decidida; a pré-visualização serve o texto
    do candidato e de reserva quando a origem é desconhecida na execução.
    Nunca levanta.
    """
    return _macro_params_with_plan(params, origin, urgency, step, verb=verb,
                                   goal_name=goal_name, avoid=avoid, bounds=bounds)[0]


def movement_targets(state: Any) -> tuple[tuple[str, dict[str, Any]], ...]:
    """Destinos relevantes do estado: ``(nome, params de destino)`` por ordem.

    Destino de viagem (``travel_destination``, só nome), alvo do objetivo
    (``raw.objective_target`` com coordenadas) e marcos (``raw.landmarks``).
    Sem destino declarado não há ``navigate_to`` — "ir para onde já estou" só
    polui o prompt (mesma regra do gerador clássico).
    """
    out: list[tuple[str, dict[str, Any]]] = []
    if not isinstance(state, GameState):
        return ()
    raw = state.raw if isinstance(state.raw, Mapping) else {}
    destination = _text(state.metrics.location.travel_destination)
    if destination:
        out.append((destination, {"destination": destination}))
    target = raw.get("objective_target")
    if isinstance(target, Mapping) and target.get("x") is not None and target.get("y") is not None:
        name = _text(target.get("name")) or "the objective"
        out.append((name, {"x": int(target["x"]), "y": int(target["y"]),
                           "z": target.get("z"), "destination": name}))
    landmarks = raw.get("landmarks")
    if isinstance(landmarks, (list, tuple)):
        for entry in landmarks:
            if not isinstance(entry, Mapping) or entry.get("x") is None or entry.get("y") is None:
                continue
            name = _text(entry.get("name")) or f"{entry['x']},{entry['y']}"
            out.append((name, {"x": int(entry["x"]), "y": int(entry["y"]),
                               "z": entry.get("z"), "destination": name}))
    return tuple(out)


# --------------------------------------------------------------- candidatos


def movement_actions(
    state: GameState,
    *,
    objective: str = "",
    urgency: Any = None,
    step: int = 0,
    limit: int = MOVEMENT_ACTION_LIMIT,
    avoid: Sequence[Any] = (),
    bounds: Any = None,
    config: Any = None,
) -> tuple[MacroAction, ...]:
    """Macro-ações de movimento do estado: destino + urgência, por ordem de triagem.

    Cobre a família de movimento que o ``MacroExecutor`` caminha em rota —
    ``flee`` (hostil visível: sobrevivência primeiro), ``hunt:search_prey``
    (missão de caça sem presa à vista), ``navigate_to`` (um por destino
    relevante) e ``explore`` (sempre: rumo do entorno ou ``n``). As restantes
    famílias (combate, itens, conversa, necessidades) continuam do gerador
    clássico — aqui nada se duplica.

    Args:
        state: snapshot tipado (``GameStateClient.snapshot()``).
        objective: objetivo da missão em EN (ativa ``hunt:search_prey`` via
            :func:`.candidates.wants_hunt`).
        urgency: urgência inicial (``score`` do JEV-A, texto ou índice — ver
            :func:`.movement.urgency_level`); ``None`` = :data:`.movement.DEFAULT_URGENCY`.
        step: passo do loop (só auditoria do ``route id``).
        limit: teto exato do total devolvido (1..32).
        avoid / bounds: repassados ao planeador (tiles a evitar / limites do mundo).
        config: ``LoopConfig``-like (usa ``flee_steps``); ``None`` = 5.

    Returns:
        Tuplo (nunca vazio se houver estado utilizável; ``explore`` entra sempre
        que haja posição) de :class:`~.types.MacroAction`, ordenado por
        ``sort_key`` (sobrevivência > ameaça > objetivo > conveniência).
    """
    if not isinstance(state, GameState):
        raise TypeError(f"movement_actions expects a GameState, got {type(state).__name__}")
    try:
        limit = max(1, min(32, int(limit)))
    except (TypeError, ValueError):
        limit = MOVEMENT_ACTION_LIMIT
    flee_steps = max(1, int(getattr(config, "flee_steps", 5) or 5))
    level = urgency_level(urgency)
    origin = observe_state(state).pos
    metrics = state.metrics
    taken: set[str] = set()
    out: list[MacroAction] = []

    def emit(*, action_id: str, name_en: str, description_en: str, target: Any, verb: str,
             category: str, priority: int, params: Mapping[str, Any]) -> None:
        goal = params.get("destination") or (target if verb == "navigate_to" else None)
        merged, plan = _macro_params_with_plan(
            params, origin, level, step, verb=verb,
            goal_name=goal if isinstance(goal, str) else None, avoid=avoid, bounds=bounds)
        summary = route_summary(plan)
        description = f"{summary}. {description_en}" if summary else description_en
        out.append(MacroAction(
            id=_unique(action_id, taken), name_en=name_en,
            description_en=_comment_free(description), target=target, verb=verb,
            category=category, priority=priority, params=merged,
        ))

    # 1. fugir (sobrevivência): afastar do hostil visível mais próximo
    threats = metrics.threats
    hostiles = [unit for unit in (getattr(threats, "hostiles", ()) or ())
                if getattr(unit, "visible", True)]
    if hostiles:
        nearest = getattr(threats, "nearest", None) or hostiles[0]
        away = opposite_direction(normalise_direction(getattr(nearest, "direction", ""))) or "s"
        name = _text(getattr(nearest, "name", None)) or "the hostile"
        emit(action_id=f"flee:{away}", name_en=f"Withdraw {away}",
             description_en=(f"Urgent: withdraw {away}, away from {name}, "
                             f"and re-assess after every leg."),
             target=away, verb="flee", category="threat", priority=_FLEE_PRIORITY,
             params={"direction": away, "steps": flee_steps, "from": name})

    # 2. caça: missão de caça sem presa à vista → procurar (escadaria do executor)
    surroundings = _text(metrics.location.surroundings) or _text(metrics.location.region)
    if wants_hunt(objective) and not _prey_visible(state):
        hunt_direction = direction_from_text(surroundings, state.focus) or "n"
        emit(action_id="hunt:search_prey", name_en="Search for an animal to hunt",
             description_en=("MISSION: no prey in sight — walk the surroundings until an "
                             "animal appears, then attack it."),
             target="prey", verb="hunt", category="objective", priority=_HUNT_PRIORITY,
             params={"direction": hunt_direction, "steps": flee_steps})

    # 3. viajar: um por destino relevante (coords quando existem → rota completa)
    for order, (name, target_params) in enumerate(movement_targets(state)):
        emit(action_id=f"navigate:{_slug(name, 'destination')}", name_en=f"Travel to {name}",
             description_en=(f"Travel to {name} and re-assess on arrival"
                             + (" (coordinates unknown: needs the travel landmark flow)"
                                if "x" not in target_params else "")),
             target=name, verb="navigate_to", category="objective",
             priority=_NAVIGATE_PRIORITY + order, params=target_params)

    # 4. explorar: sempre (objetivo por omissão) — rumo do entorno, senão "n"
    explore_direction = direction_from_text(surroundings, metrics.location.site_type,
                                            state.focus) or "n"
    emit(action_id=f"explore:{_slug(surroundings, 'surroundings')}",
         name_en="Explore the surroundings",
         description_en=("Walk the surroundings to find water, food, shelter or the objective"
                         + (f" ({surroundings})." if surroundings else ".")),
         target=surroundings or None, verb="explore", category="objective",
         priority=_EXPLORE_PRIORITY,
         params={"direction": explore_direction, "steps": flee_steps})

    return tuple(sorted(out, key=lambda action: action.sort_key)[:limit])


def apply_urgency(action: MacroAction, urgency: Any) -> MacroAction:
    """Copia da macro-ação com a urgência **decidida** pelo JEV-A (pergunta ``score``).

    Atualiza ``params["urgency"]`` e reconstrói a pré-visualização
    ``params["route"]`` com o orçamento do nível (``stride``/``max_legs`` de
    :data:`.movement.URGENCY_PROFILES`). ``None``/lixo fixa o nível por omissão.
    A rota definitiva continua a ser a do executor (posição fresca na execução).
    """
    if not isinstance(action, MacroAction):
        raise TypeError(f"apply_urgency expects a MacroAction, got {type(action).__name__}")
    params = dict(action.params or {})
    level = urgency_level(urgency)
    params["urgency"] = level
    preview = params.get("route")
    origin = preview.get("origin") if isinstance(preview, Mapping) else None
    verb = (action.verb or action.id.split(":", 1)[0]).strip().lower().split(":", 1)[0]
    plan = None
    if verb in MOVEMENT_VERBS:
        goal = params.get("destination") or (action.target if verb == "navigate_to" else None)
        try:
            plan = build_route(params, origin, level, verb=verb,
                               goal_name=goal if isinstance(goal, str) else None)
        except Exception:  # noqa: BLE001 - nunca rebenta
            plan = None
    if plan is not None:
        params["route"] = plan.to_params()
    elif not isinstance(preview, Mapping):
        params.pop("route", None)
    return replace(action, params=params)


def _comment_free(text: str) -> str:
    """O texto do candidato nunca entra no DSL, mas o ``plan_dsl`` ecoa-o em notas."""
    return " ".join(str(text).replace(";", ",").split())
