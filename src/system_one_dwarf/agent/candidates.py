"""Geração DETERMINÍSTICA de macro-ações a partir do estado do jogo.

O modelo **nunca inventa ações**: este módulo lê o :class:`~..game.models.GameState`
e produz a lista fechada de macro-ações que o JEV-A escolhe por ``choice``. Mesmo
estado ⇒ mesmas ações, mesma ordem (sem ``random``, sem tempo, sem rede), para
que a decisão seja auditável e reproduzível.

Vocabulary (``id`` = ``<verbo>:<alvo>``, alinhado com ``evals/jev-a-action.json``;
``verb`` = o nome semântico do loop):

===============  ==================  ==========================================
``verb``         ``id``              Quando aparece
===============  ==================  ==========================================
``attack``       ``attack:<hostil>`` um por hostil visível (sem hostis, nenhum)
``flee``         ``flee:<direção>``  afastar-se do hostil mais próximo
``drink_potion`` ``drink:<poção>``   poção na mochila
``eat``          ``eat:<comida>``    fome ≠ ``none`` **ou** comida na mochila
``drink``        ``drink:<água>``    sede ≠ ``none`` **ou** água na mochila
``rest``         ``rest:<local>``    sonolento **ou** noite (e sem ameaça iminente)
``pick_up``      ``pickup:<item>``  um por item visível no chão
``talk``         ``talk:<unidade>``  um por unidade não-hostil perto
``navigate_to``  ``navigate:<alvo>``destino de viagem / marco / coordenadas
``explore``      ``explore:<rumo>``  sempre (objetivo por omissão)
``wait``         ``wait:<motivo>``   sempre
``other``        ``other``           **opção de saída, sempre a última**
===============  ==================  ==========================================

Regras de viabilidade (nada é oferecido ao JEV se não for executável agora):
sem hostis não há ``attack``; ``eat``/``drink`` só com fome/sede declaradas ou
comida/água na mochila; ``rest`` só com sono ou noite; ``pick_up``/``talk`` só
com alvo visível.

Triagem e teto: as ações são ordenadas por
**sobrevivência > resposta a ameaça > objetivo > conveniência** (e, dentro do
mesmo nível, por prioridade) e cortadas em ``config.max_candidates`` (≤ 255 no
``choice``, mas o prompt degrada com volume). O teto é **exato** — o total
devolvido nunca passa o limite, em nenhuma configuração. Posições **reservadas**:
``wait`` (só quando cabem três ou mais) e, sempre por último, a opção de saída
``other`` — o JEV-A nunca fica sem escape hatch, e com o teto no mínimo legal
(``LoopConfig`` exige ≥ 3) há sempre pelo menos uma ação real.

Limitação conhecida do snapshot atual: os 8 grupos de ``df_bridge snapshot`` não
expõem itens *no chão* (``inventory`` é o que está **carregado**). ``pick_up``
aparece quando o snapshot traz uma lista de itens visíveis num campo extra
(``items_on_ground`` / ``visible_items`` / ``ground_items``) — quando a ponte
passar a expor esse grupo, nada aqui muda.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from ..game.models import GameState, InventoryItem, VisibleUnit
from .types import MacroAction

__all__ = [
    "DIRECTIONS",
    "OPPOSITE",
    "POTION_WORDS",
    "EDIBLE_WORDS",
    "DRINKABLE_WORDS",
    "EXIT_ACTION_ID",
    "WAIT_ACTION_ID",
    "direction_from_text",
    "feasible_verbs",
    "generate_actions",
    "normalise_direction",
    "opposite_direction",
    "visible_ground_items",
]

# --------------------------------------------------------------- vocabulary

EXIT_ACTION_ID = "other"
WAIT_ACTION_ID = "wait:one_turn"

DIRECTIONS: tuple[str, ...] = ("n", "s", "e", "w", "ne", "nw", "se", "sw")
OPPOSITE: dict[str, str] = {
    "n": "s", "s": "n", "e": "w", "w": "e",
    "ne": "sw", "sw": "ne", "nw": "se", "se": "nw",
    "up": "down", "down": "up",
}

#: Palavras que identificam itens na mochila (nomes vêm do jogo, em inglês).
POTION_WORDS: tuple[str, ...] = ("potion", "healing", "salve", "bandage", "elixir", "tonic")
EDIBLE_WORDS: tuple[str, ...] = (
    "meat", "food", "bread", "cheese", "fish", "mushroom", "plant", "fruit",
    "berry", "seed", "ration", "stew", "egg", "root", "leaf", "nut",
)
DRINKABLE_WORDS: tuple[str, ...] = ("water", "wine", "beer", "ale", "drink", "flask", "waterskin")

_GROUND_ITEM_KEYS = ("items_on_ground", "visible_items", "ground_items")
_HUNGRY_LEVELS = ("hungry", "starving", "very hungry", "famished")
_THIRSTY_LEVELS = ("thirsty", "dehydrated", "very thirsty", "parched")
_DROWSY_LEVELS = ("drowsy", "sleepy", "tired", "exhausted", "very drowsy")
_SAFE_THREAT_LEVELS = ("none", "low", "")


# ------------------------------------------------------------------ helpers


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _lower(value: Any) -> str:
    return _text(value).lower()


def _slug(value: Any, fallback: str = "target") -> str:
    text = _lower(value)
    out = []
    for char in text:
        if char.isalnum():
            out.append(char)
        elif char in " -_/,":
            out.append("_")
    slug = "".join(out).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug[:48] or fallback


def normalise_direction(value: Any) -> str:
    """``"NE"``/``"ne +2z"``/``"here"`` → direção do DSL (``ne``) ou ``""``.

    O ``dfb-state`` devolve a direção em maiúsculas e pode acrescentar o
    desnível em z (``"N +2z"``); o DSL ``move`` só aceita
    ``n|s|e|w|ne|nw|se|sw|up|down|wait``. ``"here"`` (mesmo tile) → ``""``.
    """
    text = _lower(value)
    if not text or text == "here":
        return ""
    text = text.split("z")[0].strip()
    text = text.split()[0] if text else ""
    if text == "here":
        return ""
    if text == "up":
        return "up"
    if text == "down":
        return "down"
    return text if text in DIRECTIONS else ""


def opposite_direction(direction: str) -> str:
    """Direção oposta (para onde fugir); ``""`` se não houver direção."""
    return OPPOSITE.get(_lower(direction), "")


def _matches(name: Any, words: Sequence[str]) -> bool:
    text = _lower(name)
    return bool(text) and any(word in text for word in words)


_COMPASS_WORDS: tuple[tuple[str, str], ...] = (
    ("northeast", "ne"), ("north-east", "ne"), ("northwest", "nw"), ("north-west", "nw"),
    ("southeast", "se"), ("south-east", "se"), ("southwest", "sw"), ("south-west", "sw"),
    ("north", "n"), ("south", "s"), ("east", "e"), ("west", "w"),
)


def direction_from_text(*texts: Any) -> str:
    """Primeira direção cardinal mencionada no texto (``""`` se nenhuma).

    O snapshot não dá um rumo para "explorar"; quando o entorno é descrito
    ("a road runs north"), o plano segue essa direção em vez de um rumo
    arbitrário.
    """
    for text in texts:
        lowered = _lower(text)
        if not lowered:
            continue
        for word, direction in _COMPASS_WORDS:
            if word in lowered:
                return direction
    return ""


def _find_items(items: Iterable[InventoryItem], words: Sequence[str]) -> list[InventoryItem]:
    out = []
    for item in items:
        if _matches(item.name, words):
            out.append(item)
    return out


def _ground_item_entries(state: GameState) -> list[tuple[str, dict[str, Any]]]:
    """``(nome, params)`` dos itens visíveis no chão, com coords quando existirem.

    Aceita uma lista de strings ou de mappings (``name``/``item``/``label`` e,
    opcionalmente, ``x``/``y``/``z``) num campo extra do snapshot. Sem esse
    campo, a lista é vazia (nunca inventada a partir do inventário, que é o que
    está **carregado**).
    """
    raw = state.raw if isinstance(state.raw, Mapping) else {}
    for key in _GROUND_ITEM_KEYS:
        value = raw.get(key)
        if not isinstance(value, (list, tuple)):
            continue
        entries: list[tuple[str, dict[str, Any]]] = []
        for entry in value:
            params: dict[str, Any] = {}
            if isinstance(entry, str):
                name = entry
            elif isinstance(entry, Mapping):
                name = entry.get("name") or entry.get("item") or entry.get("label") or ""
                for coord in ("x", "y", "z"):
                    if entry.get(coord) is not None:
                        params[coord] = entry[coord]
            else:
                name = getattr(entry, "name", None) or ""
            name = _text(name)
            if name:
                entries.append((name, params))
        if entries:
            return entries
    return []


def visible_ground_items(state: GameState) -> list[str]:
    """Nomes dos itens visíveis no chão (grupo ainda **não** exposto pelo Lua)."""
    return [name for name, _params in _ground_item_entries(state)]


def _hostiles(state: GameState) -> list[VisibleUnit]:
    threats = state.metrics.threats
    return [unit for unit in threats.hostiles if unit.visible and unit.name]


def _nearby_units(state: GameState, radius: float) -> list[VisibleUnit]:
    units = state.metrics.units_nearby.units
    out = []
    for unit in units:
        if not unit.name or unit.hostile or not unit.visible:
            continue
        if unit.distance is not None and float(unit.distance) > float(radius):
            continue
        out.append(unit)
    return out


def _is_night(state: GameState) -> bool:
    hour = state.metrics.game_clock.hour
    if isinstance(hour, (int, float)) and not isinstance(hour, bool):
        return float(hour) < 6.0 or float(hour) >= 21.0
    date_text = _lower(state.metrics.game_clock.date_text)
    return "night" in date_text or "midnight" in date_text


def _is_safe(state: GameState) -> bool:
    threats = state.metrics.threats
    if threats.imminent:
        return False
    if _hostiles(state):
        return False
    return _lower(threats.level) in _SAFE_THREAT_LEVELS or threats.count == 0


def _wounded(state: GameState) -> bool:
    health = state.metrics.health_injuries
    if health.wound_count:
        return True
    if health.is_bleeding:
        return True
    if health.blood_current is not None and health.blood_max:
        return float(health.blood_current) < 0.75 * float(health.blood_max)
    return bool(health.pain and float(health.pain) > 0)


def _unique(candidate_id: str, taken: set[str]) -> str:
    """Garante ids distintos dentro do mesmo ``choice`` (o builder recusa repetidos)."""
    if candidate_id not in taken:
        taken.add(candidate_id)
        return candidate_id
    index = 2
    while f"{candidate_id}_{index}" in taken:
        index += 1
    final = f"{candidate_id}_{index}"
    taken.add(final)
    return final


def _travel_targets(state: GameState) -> list[tuple[str, dict[str, Any]]]:
    """Alvos de ``navigate_to``: destino de viagem, alvo do objetivo, marcos."""
    out: list[tuple[str, dict[str, Any]]] = []
    destination = _text(state.metrics.location.travel_destination)
    if destination:
        out.append((destination, {"destination": destination}))

    raw = state.raw if isinstance(state.raw, Mapping) else {}
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
    # Sem destino/marco declarado não há "navigate_to": estar já na região não é
    # um destino (um candidato "viajar para onde já estou" só polui o prompt).
    return out


# ------------------------------------------------------------- geração


def generate_actions(
    state: GameState,
    config: Any = None,
    *,
    max_candidates: int | None = None,
    knowledge: Sequence[Any] = (),
) -> list[MacroAction]:
    """Macro-ações executáveis agora, por ordem de triagem, com a saída no fim.

    Args:
        state: snapshot tipado do jogo (``GameStateClient.snapshot()``).
        config: :class:`~.types.LoopConfig` (usa ``max_candidates``,
            ``flee_steps``, ``talk_radius``, ``max_items``); ``None`` usa os
            defaults do config.
        max_candidates: sobrepõe ``config.max_candidates``.
        knowledge: itens recuperados pela memória (JEV-R) — aceite por
            compatibilidade de assinatura; a *viabilidade* de uma ação vem
            sempre do estado do jogo, nunca da narrativa.

    Returns:
        Lista não vazia de :class:`~.types.MacroAction`, terminando **sempre**
        em ``wait`` + ``other``.
    """
    del knowledge  # a memória informa o prompt do JEV, não a viabilidade da ação
    if not isinstance(state, GameState):
        raise TypeError(f"generate_actions expects a GameState, got {type(state).__name__}")

    cfg = config
    limit = int(max_candidates if max_candidates is not None else getattr(cfg, "max_candidates", 12))
    if limit < 1:
        raise ValueError(f"max_candidates must be >= 1, got {max_candidates!r}")
    flee_steps = int(getattr(cfg, "flee_steps", 5))
    talk_radius = float(getattr(cfg, "talk_radius", 8.0))
    max_items = int(getattr(cfg, "max_items", 6))

    metrics = state.metrics
    needs = metrics.needs_state
    inventory = metrics.inventory
    threats = metrics.threats
    hostiles = _hostiles(state)

    hunger = _lower(needs.hunger)
    thirst = _lower(needs.thirst)
    sleep = _lower(needs.sleep)
    hungry = hunger in _HUNGRY_LEVELS or (bool(hunger) and hunger != "none")
    thirsty = thirst in _THIRSTY_LEVELS or (bool(thirst) and thirst != "none")
    drowsy = sleep in _DROWSY_LEVELS or (bool(sleep) and sleep != "rested")
    night = _is_night(state)
    safe = _is_safe(state)
    wounded = _wounded(state)

    potions = _find_items(inventory.items, POTION_WORDS)
    edibles = _find_items(inventory.items, EDIBLE_WORDS)
    drinks = _find_items(inventory.items, DRINKABLE_WORDS)

    actions: list[MacroAction] = []
    taken: set[str] = set()

    # ---------------------------------------------------- sobrevivência
    if potions and (wounded or not inventory.equipped_weapon):
        potion = potions[0]
        actions.append(MacroAction(
            id=_unique(f"drink:{_slug(potion.name, 'potion')}", taken),
            name_en=f"Drink the {potion.name}",
            description_en=(
                f"Drink {potion.name} now: wounds/bleeding need treatment before anything else."
                if wounded else
                f"Drink {potion.name}: unarmed and hurt is the worst combination to stay in."
            ),
            target=potion.name, verb="drink_potion", category="survival", priority=10,
            params={"item": potion.name, "equipped": bool(potion.equipped)},
            needs_manual_mapping=True,
        ))

    if hostiles:
        nearest = threats.nearest
        away = opposite_direction(normalise_direction(nearest.direction if nearest else ""))
        step_dir = away or "s"
        dangerous = bool(nearest and (nearest.great_danger or (nearest.distance or 99) <= 5))
        if dangerous or wounded:
            actions.append(MacroAction(
                id=_unique(f"flee:{step_dir}", taken),
                name_en=f"Flee {step_dir}",
                description_en=(
                    f"Break away {step_dir}, away from {nearest.name if nearest else 'the threat'}: "
                    f"{flee_steps} steps, then re-assess."
                ),
                target=step_dir, verb="flee", category="survival", priority=20,
                params={"direction": step_dir, "steps": flee_steps,
                        "from": nearest.name if nearest else None},
            ))

    if hungry:
        if edibles:
            food = edibles[0]
            actions.append(MacroAction(
                id=_unique(f"eat:{_slug(food.name, 'food')}", taken),
                name_en=f"Eat the {food.name}",
                description_en=f"Eat {food.name} from the pack: hunger is '{hunger or 'unknown'}'.",
                target=food.name, verb="eat", category="survival",
                priority=40 if hunger in ("starving", "very hungry", "famished") else 60,
                params={"item": food.name}, needs_manual_mapping=True,
            ))
        else:
            actions.append(MacroAction(
                id=_unique("eat:forage", taken),
                name_en="Find food and eat",
                description_en=(
                    f"Hunger is '{hunger}' and the pack has no food: find something edible "
                    "nearby (plants, corpses, a village) and eat."
                ),
                target="food", verb="eat", category="survival", priority=45,
                params={"search": True}, needs_manual_mapping=True,
            ))

    if thirsty:
        if drinks:
            water = drinks[0]
            actions.append(MacroAction(
                id=_unique(f"drink:{_slug(water.name, 'water')}", taken),
                name_en=f"Drink the {water.name}",
                description_en=f"Drink {water.name}: thirst is '{thirst or 'unknown'}'.",
                target=water.name, verb="drink",
                category="survival", priority=50 if thirst == "dehydrated" else 65,
                params={"item": water.name}, needs_manual_mapping=True,
            ))
        else:
            actions.append(MacroAction(
                id=_unique("drink:nearest_water", taken),
                name_en="Reach water and drink",
                description_en=(
                    f"Thirst is '{thirst}' and there is nothing to drink in the pack: head for "
                    "the nearest water (river, stream, pool) and drink."
                ),
                target="water", verb="drink", category="survival", priority=55,
                params={"search": True, "destination": "nearest water"},
            ))

    if (drowsy or night) and safe:
        reason = "drowsy" if drowsy else "night"
        actions.append(MacroAction(
            id=_unique("rest:until_morning" if night and not drowsy else "rest:here", taken),
            name_en="Rest" + (" until morning" if night and not drowsy else " here"),
            description_en=(
                f"Rest {reason}: no hostile is near and there is nothing that must be done "
                "this instant."
            ),
            target="here", verb="rest", category="survival" if drowsy else "convenience",
            priority=80, params={"reason": reason, "night": bool(night)},
        ))

    # ------------------------------------------------- resposta a ameaça
    for order, hostile in enumerate(sorted(
        hostiles, key=lambda unit: (unit.distance if unit.distance is not None else 1e9, unit.name or "")
    )):
        direction = normalise_direction(hostile.direction)
        distance = hostile.distance if isinstance(hostile.distance, (int, float)) else None
        approach = max(1, int(distance) - 1) if distance else 1
        actions.append(MacroAction(
            id=_unique(f"attack:{_slug(hostile.name, 'hostile')}", taken),
            name_en=f"Attack {hostile.name}",
            description_en=(
                f"Close on {hostile.name} ({'%d tiles ' % int(distance) if distance else ''}"
                f"{direction or 'adjacent'}) and attack until it is dead."
            ),
            target=hostile.name, verb="attack", category="threat",
            priority=10 + int(distance or 0) * 10 + order,
            params={"direction": direction, "distance": distance, "steps": approach,
                    "great_danger": bool(hostile.great_danger)},
            needs_manual_mapping=True,
        ))

    # ------------------------------------------------------- objetivo
    for order, (name, params) in enumerate(_travel_targets(state)[:max_items]):
        actions.append(MacroAction(
            id=_unique(f"navigate:{_slug(name, 'destination')}", taken),
            name_en=f"Travel to {name}",
            description_en=f"Navigate to {name} and re-assess on arrival.",
            target=name, verb="navigate_to", category="objective", priority=40 + order,
            params=params,
        ))

    for order, (name, item_params) in enumerate(_ground_item_entries(state)[:max_items]):
        actions.append(MacroAction(
            id=_unique(f"pickup:{_slug(name, 'item')}", taken),
            name_en=f"Pick up the {name}",
            description_en=f"Pick up {name} from the ground; it is visible and reachable.",
            target=name, verb="pick_up", category="objective", priority=70 + order,
            params={"item": name, **item_params}, needs_manual_mapping=True,
        ))

    for order, unit in enumerate(_nearby_units(state, talk_radius)[:max_items]):
        actions.append(MacroAction(
            id=_unique(f"talk:{_slug(unit.name, 'unit')}", taken),
            name_en=f"Talk to {unit.name}",
            description_en=(
                f"Talk to {unit.name} "
                f"({'%d tiles ' % int(unit.distance) if unit.distance else ''}"
                f"{normalise_direction(unit.direction) or 'adjacent'}): news, trade or directions."
            ),
            target=unit.name, verb="talk", category="objective", priority=80 + order,
            params={"direction": normalise_direction(unit.direction), "distance": unit.distance,
                    "steps": max(0, int(unit.distance) - 1) if unit.distance else 0},
            needs_manual_mapping=True,
        ))

    # -------------------------------------------------- conveniência
    surroundings = _text(metrics.location.surroundings) or _text(metrics.location.region)
    explore_target = _slug(surroundings, "surroundings") if surroundings else "surroundings"
    explore_direction = direction_from_text(surroundings, metrics.location.site_type, state.focus)
    actions.append(MacroAction(
        id=_unique(f"explore:{explore_target}", taken),
        name_en="Explore the surroundings",
        description_en=(
            "Walk the surroundings to find water, food, shelter or the objective"
            + (f" ({surroundings})." if surroundings else ".")
        ),
        target=surroundings or None, verb="explore", category="objective", priority=100,
        params={"steps": flee_steps, "direction": explore_direction},
    ))

    if potions and not wounded and inventory.equipped_weapon:
        potion = potions[0]
        actions.append(MacroAction(
            id=_unique(f"drink:{_slug(potion.name, 'potion')}", taken),
            name_en=f"Drink the {potion.name}",
            description_en=f"Drink {potion.name} pre-emptively before the next fight.",
            target=potion.name, verb="drink_potion", category="convenience", priority=300,
            params={"item": potion.name}, needs_manual_mapping=True,
        ))

    if edibles and not hungry:
        food = edibles[0]
        actions.append(MacroAction(
            id=_unique(f"eat:{_slug(food.name, 'food')}", taken),
            name_en=f"Eat the {food.name}",
            description_en=f"Eat {food.name} while it is safe to do so.",
            target=food.name, verb="eat", category="convenience", priority=310,
            params={"item": food.name}, needs_manual_mapping=True,
        ))

    if hostiles and not any(action.verb == "flee" for action in actions):
        nearest = threats.nearest
        away = opposite_direction(normalise_direction(nearest.direction if nearest else "")) or "s"
        actions.append(MacroAction(
            id=_unique(f"flee:{away}", taken),
            name_en=f"Withdraw {away}",
            description_en=f"Withdraw {away} to fight another day; the hostile is not on top of us yet.",
            target=away, verb="flee", category="threat", priority=90,
            params={"direction": away, "steps": max(1, flee_steps // 2),
                    "from": nearest.name if nearest else None},
        ))

    # ------------------------------------------------ reserva: wait + other
    # O teto é EXATO: o total devolvido **nunca** passa ``limit``, em nenhuma
    # configuração. A última posição é sempre da opção de saída (o ``choice`` do
    # JEV-A exige-a) e, quando há espaço para três, a penúltima é do ``wait`` —
    # o agente nunca fica sem ação neutra nem sem escape hatch. Com ``limit``
    # pequeno as reservas encolhem (wait sai primeiro) em vez de estourar o teto.
    wait_action = MacroAction(
        id=WAIT_ACTION_ID,
        name_en="Wait one turn",
        description_en=(
            "Pass one turn and watch: nothing urgent — let the situation develop"
            + (" (danger is on top of us)." if threats.imminent else ".")
        ),
        target="one_turn", verb="wait", category="convenience", priority=900,
        params={"kind": "short"},
    )
    exit_action = MacroAction(
        id=EXIT_ACTION_ID,
        name_en="None of these",
        description_en="None of these actions fits the situation (do nothing or reconsider).",
        target=None, verb="other", category="convenience", priority=999,
        params={},
    )
    tail: list[MacroAction] = [wait_action] if limit >= 3 else []
    tail.append(exit_action)
    budget = max(0, limit - len(tail))
    triaged = sorted(actions, key=lambda action: action.sort_key)
    out = triaged[:budget]
    for reserved in tail:
        if not any(action.id == reserved.id for action in out):
            out.append(reserved)
    return out


def feasible_verbs(state: GameState, config: Any = None) -> tuple[str, ...]:
    """Verbos oferecidos para este estado (útil em logs/auditoria e testes)."""
    return tuple(action.verb for action in generate_actions(state, config))
