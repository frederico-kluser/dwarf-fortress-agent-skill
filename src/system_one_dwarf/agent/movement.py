"""Movimento como MACRO-AÇÃO: destino + urgência decididos uma vez, pathing por *legs* em código.

Porquê (ARQUITETURA.md §8 mudança 04): 415 decisões do JEV-A foram gastas só em
micro-movimento (349 em abstenção). O decisor escolhe **para onde** (destino ou
rumo) e **com que urgência** (a régua ``score`` ``Safe``/``Pressing``/
``Critical``); o código decompõe o trajeto em *legs* curtos (``goto X,Y``) e relê
o snapshot entre eles. Menos controlo por passo ↔ muito menos indecisão, custo e
tempo — com os gates intactos (``--stop-on-threat``/``--stop-on-focus-change`` +
:func:`evaluate_leg` entre legs).

Módulo PURO (sem I/O, relógio nem ``random``; mesmo input ⇒ mesma rota)::

    plan = build_route(action.params, origin, urgency, step, verb=action.verb)
    params["route"] = plan.to_params()        # JSON-safe, vai na MacroAction
    plan = RoutePlan.from_params(params)      # o executor revalida (estrito)
    for leg in plan.legs:
        run(plan.to_dsl(leg))                 # "goto X,Y": só inteiros validados
        verdict = evaluate_states(before, after, leg)  # ou evaluate_leg(...) com primitivas
        # continue|arrived|blocked|abort; blocked ⇒ plan.fallback_dsl(leg) ("move <rumo> times=N")

Decisões: (1) uma rota = uma ``execute()``, snapshot entre legs; uma aprovação
cobre uma rota; sem continuação entre passos do loop. (2) Orçamento duro: ≤
``max_legs`` legs de ≤ ``stride`` tiles (Chebyshev, a métrica do ``goto``) por
decisão; perfis em :data:`URGENCY_PROFILES`. (3) Só mesmo z (``z`` viaja nos
waypoints, o planeamento é planar). (4) No DSL só entram inteiros dentro de
``±MAX_COORD``; nomes do jogo são DADO (sem controlos, tamanho limitado, nunca
interpolados). (5) Nada levanta no caminho do loop: lixo ⇒ ``None``/``""``/
veredicto neutro; só os construtores dos dataclasses recusam (``ValueError``).
(6) A ponte dá "arrived" a ≤ 1 tile do alvo: um leg de ≤ 1 tile é *no-op* e
nunca é planeado. (7) ``avoid`` desvia waypoints e segmentos retos (melhor
esforço: o BFS do ``goto`` escolhe o caminho real; a rede de segurança é o
``--stop-on-threat``).
"""
from __future__ import annotations

import hashlib
import itertools
import json
import logging
import math
import re
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping, NamedTuple, Sequence

__all__ = [
    # constantes de afinação (o orquestrador mexe só nestas ou em ``profiles=``)
    "ARRIVAL_TOLERANCE", "AVOID_RADIUS", "AVOID_SHIFT_MAX", "BLOOD_DROP_ABORT_FRACTION",
    "COMPASS", "DEFAULT_BOUNDS", "DEFAULT_FLEE_TILES", "DEFAULT_MAX_LEGS", "DEFAULT_STRIDE",
    "DEFAULT_URGENCY", "DIRECTION_VECTORS", "LEG_ABORT", "LEG_ARRIVED", "LEG_BLOCKED",
    "LEG_CONTINUE", "LEG_STEP_FACTOR", "MAX_AVOID_POINTS", "MAX_LEGS", "MAX_NAME_CHARS",
    "MAX_STRIDE", "MAX_VIA_WAYPOINTS", "MIN_LEG_TILES", "MIN_STRIDE", "MOVEMENT_VERBS",
    "OPPORTUNITY_REASONS", "URGENCY_LEVELS", "URGENCY_PROFILES",
    # tipos
    "LegObservation", "LegVerdict", "RouteLeg", "RoutePlan", "UrgencyProfile", "Waypoint",
    # funções
    "as_xy", "build_route", "compass_bearing", "evaluate_leg", "evaluate_states",
    "next_unvisited_direction", "normalise_compass", "observe_state", "plan_waypoints",
    "route_summary", "urgency_level", "urgency_profile",
]

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ constantes
# Espelhos de ``executor.MAX_COORD``/``MAX_PLAN_STEPS``/``_DIR_VECTORS`` (copiados:
# o executor importa este módulo). ``test_movement_routes`` falha se derivarem.
MAX_COORD = 1_000_000
MAX_PLAN_STEPS = 1000
#: As 8 direções planares em sentido horário (a rotação por índice depende disto).
COMPASS: tuple[str, ...] = ("n", "ne", "e", "se", "s", "sw", "w", "nw")
#: ``(dx, dy)`` por direção (y cresce para sul, como no DF).
DIRECTION_VECTORS: dict[str, tuple[int, int]] = {
    "n": (0, -1), "ne": (1, -1), "e": (1, 0), "se": (1, 1),
    "s": (0, 1), "sw": (-1, 1), "w": (-1, 0), "nw": (-1, -1),
}
_LONG_NAMES = {"north": "n", "south": "s", "east": "e", "west": "w", "northeast": "ne",
               "northwest": "nw", "southeast": "se", "southwest": "sw"}
#: Lado da escadaria por rumo (1.º lado de ``executor._HUNT_SIDES``).
_HUNT_SIDES = {"n": "e", "s": "e", "e": "n", "w": "n", "ne": "e", "nw": "w", "se": "e", "sw": "w"}

#: Verbos do gerador de candidatos que viram rota (``navigate`` = prefixo do id).
MOVEMENT_VERBS: tuple[str, ...] = ("explore", "navigate_to", "hunt", "flee")
_VERB_ALIASES = {"navigate": "navigate_to", "travel": "navigate_to"}
#: Rotas de explorar/caçar param quando uma presa entra em vista (oportunismo).
_PREY_WATCH_VERBS = ("explore", "hunt")

URGENCY_LEVELS: tuple[str, str, str] = ("safe", "pressing", "critical")
#: Sem resposta de urgência: o meio-termo (nem saltos longos às cegas, nem 1 passo).
DEFAULT_URGENCY = "pressing"


class UrgencyProfile(NamedTuple):
    """``(stride, max_legs, reassess_every)`` — desempacota como tuplo.

    ``stride``: tiles máx. por leg · ``max_legs``: legs máx. por decisão ·
    ``reassess_every``: legs entre releituras do snapshot dentro da rota (1 =
    depois de cada leg; 2 = dois legs podem ir no mesmo ``act batch``).
    """

    stride: int
    max_legs: int
    reassess_every: int


#: Todos os números de afinação vivem nestas constantes (ou em ``profiles=``).
#: Safe = saltos longos (60 tiles/decisão); Critical = legs curtos (6 tiles) para
#: o loop reavaliar depressa. A calibrar com dados reais.
URGENCY_PROFILES: Mapping[str, UrgencyProfile] = MappingProxyType({
    "safe": UrgencyProfile(stride=10, max_legs=6, reassess_every=2),
    "pressing": UrgencyProfile(stride=6, max_legs=4, reassess_every=1),
    "critical": UrgencyProfile(stride=3, max_legs=2, reassess_every=1),
})

DEFAULT_STRIDE = 8
DEFAULT_MAX_LEGS = 6
#: A ponte dá "arrived" a ≤ 1 tile do alvo: um leg de ≤ 1 tile não anda.
ARRIVAL_TOLERANCE = 1
MIN_LEG_TILES = ARRIVAL_TOLERANCE + 1
MIN_STRIDE, MAX_STRIDE, MAX_LEGS = MIN_LEG_TILES, 24, 12
#: O mundo do DF não tem coordenadas negativas.
DEFAULT_BOUNDS: tuple[int, int, int, int] = (0, 0, MAX_COORD, MAX_COORD)
#: Margem a manter dos tiles a evitar (limitada à distância inicial: quem foge
#: de um hostil colado tem de poder afastar-se dele).
AVOID_RADIUS = 2
AVOID_SHIFT_MAX = 3
MAX_AVOID_POINTS = 32
MAX_VIA_WAYPOINTS = 16
#: Passos de ``goto`` por tile planeado (``maxsteps=``): cabe um desvio, não um labirinto.
LEG_STEP_FACTOR = 3
#: Tiles de fuga quando a ação não traz ``steps`` (``LoopConfig.flee_steps``).
DEFAULT_FLEE_TILES = 5
MAX_NAME_CHARS = 60

LEG_CONTINUE, LEG_ARRIVED, LEG_BLOCKED, LEG_ABORT = "continue", "arrived", "blocked", "abort"
#: Queda de sangue (fração do valor inicial) que aborta a rota.
BLOOD_DROP_ABORT_FRACTION = 0.10
#: Razões de ``abort`` que são oportunidade, não falha.
OPPORTUNITY_REASONS: frozenset[str] = frozenset({"new_prey"})

_INT_TEXT = re.compile(r"[+-]?[0-9]{1,9}\Z")
_SHIFTS = tuple(sign * k for k in range(1, AVOID_SHIFT_MAX + 1) for sign in (1, -1))
_TAN_NUM, _TAN_DEN = 29, 70  # tan(22,5°): fronteira reto/diagonal na quantização a 8 rumos
_Point = tuple[int, int]
_Box = tuple[int, int, int, int]
_Hazards = Sequence[tuple[_Point, int]]


# ------------------------------------------------------------------- helpers


def _as_int(value: Any) -> int | None:
    """Inteiro estrito (regra do ``executor._safe_coord``): bool/fração/NaN/lixo ⇒ ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value.is_integer() else None
    if isinstance(value, str) and _INT_TEXT.match(value.strip()):
        return int(value.strip())
    return None


def _coord(value: Any) -> int | None:
    number = _as_int(value)
    return number if number is not None and -MAX_COORD <= number <= MAX_COORD else None


def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    number = _as_int(value)
    return default if number is None else max(low, min(high, number))


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value.strip() if isinstance(value, str) else value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _attr(value: Any, name: str) -> Any:
    """``getattr`` que nunca levanta (objetos do jogo/testes com propriedades partidas)."""
    try:
        return getattr(value, name, None)
    except Exception:  # noqa: BLE001
        return None


def _clean_text(value: Any, limit: int = MAX_NAME_CHARS) -> str:
    """Texto do jogo como DADO: sem controlos, sem ``;``/``#`` da DSL, tamanho limitado."""
    if not isinstance(value, str):
        return ""
    swap = {";": ",", "#": "-"}
    chars = [swap.get(c, c) for c in value if unicodedata.category(c)[0] != "C" or c in "\n\r\t"]
    return " ".join("".join(chars).split())[:limit].rstrip()


def as_xy(value: Any) -> _Point | None:
    """``(x, y)`` inteiros de tuplo/lista/mapeamento/objeto (``Waypoint``, ``Location``…).

    Aceita ``{"x","y"}``, ``{"pos": {"x","y"}}`` (unidades do snapshot) e
    ``(x, y[, z])``; bool, fração, NaN, texto não numérico ou fora de
    ``±MAX_COORD`` ⇒ ``None``.
    """
    if value is None or isinstance(value, (str, bytes)):
        return None
    if isinstance(value, Mapping):
        x, y = value.get("x"), value.get("y")
        if x is None and y is None and isinstance(value.get("pos"), Mapping):
            x, y = value["pos"].get("x"), value["pos"].get("y")
    elif isinstance(value, (list, tuple)):
        x, y = (value[0], value[1]) if len(value) >= 2 else (None, None)
    else:
        x, y = _attr(value, "x"), _attr(value, "y")
    px, py = _coord(x), _coord(y)
    return (px, py) if px is not None and py is not None else None


def _z_of(value: Any) -> int | None:
    if isinstance(value, Mapping):
        return _coord(value.get("z"))
    if isinstance(value, (list, tuple)):
        return _coord(value[2]) if len(value) >= 3 else None
    return _coord(_attr(value, "z"))


def _cheb(a: _Point, b: _Point) -> int:
    """Distância de Chebyshev: nº de passos a 8 direções (a métrica do ``goto``)."""
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _div_round(numerator: int, denominator: int) -> int:
    """Divisão inteira arredondada (meio afasta-se de zero): simétrica e determinística."""
    quotient, remainder = divmod(abs(numerator), denominator)
    quotient += 1 if 2 * remainder >= denominator else 0
    return quotient if numerator >= 0 else -quotient


def compass_bearing(dx: int, dy: int) -> str:
    """Rumo (8 setores) de um deslocamento; ``""`` para ``(0, 0)``."""
    ax, ay = abs(dx), abs(dy)
    if ax == 0 and ay == 0:
        return ""
    if ay * _TAN_DEN <= ax * _TAN_NUM:
        return "e" if dx > 0 else "w"
    if ax * _TAN_DEN <= ay * _TAN_NUM:
        return "s" if dy > 0 else "n"
    return ("s" if dy > 0 else "n") + ("e" if dx > 0 else "w")


def normalise_compass(value: Any) -> str:
    """``"NE"``/``"north-east"``/``"ne +2z"`` → rumo planar ou ``""`` (``up``/``down``/lixo)."""
    if not isinstance(value, str):
        return ""
    words = value.strip().lower().replace("-", "").replace("_", "").split()
    token = words[0] if words else ""
    return token if token in DIRECTION_VECTORS else _LONG_NAMES.get(token, "")


def _bounds(value: Any) -> _Box:
    """``(min_x, min_y, max_x, max_y)`` válido (ordenado) ou :data:`DEFAULT_BOUNDS`."""
    if isinstance(value, Mapping):
        raw: list[Any] = [value.get(k) for k in ("min_x", "min_y", "max_x", "max_y")]
    elif isinstance(value, (list, tuple)) and len(value) == 4:
        raw = list(value)
    else:
        return DEFAULT_BOUNDS
    nums = [_coord(item) for item in raw]
    if any(item is None for item in nums):
        return DEFAULT_BOUNDS
    x0, y0, x1, y1 = nums  # type: ignore[misc]
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _clamp_point(point: _Point, box: _Box) -> _Point:
    return (max(box[0], min(box[2], point[0])), max(box[1], min(box[3], point[1])))


# ------------------------------------------------------------- planeamento


def _hazards(start: _Point, avoid: Any, radius: Any) -> list[tuple[_Point, int]]:
    """``[(tile, raio efetivo)]``; o raio nunca passa da distância inicial ao tile.

    Quem parte colado a um hostil (``flee``) só viola o desvio se *voltar* para
    ele — com o raio cheio, qualquer primeiro passo contaria como violação.
    """
    if avoid is None or isinstance(avoid, (str, bytes)):
        return []
    if (isinstance(avoid, (list, tuple)) and len(avoid) == 2
            and all(_as_int(item) is not None for item in avoid)):
        avoid = [avoid]  # um único ponto (x, y)
    try:
        items = list(itertools.islice(iter(avoid), MAX_AVOID_POINTS))
    except TypeError:
        return []
    reach = _clamp_int(radius, 0, MAX_STRIDE, AVOID_RADIUS)
    points = [as_xy(item) for item in items]
    return [(p, min(reach, _cheb(start, p))) for p in points if p is not None]


def _hits(a: _Point, b: _Point, hazards: _Hazards) -> bool:
    """O segmento reto ``a → b`` (sem ``a``) passa dentro do raio de algum perigo?"""
    count = _cheb(a, b)
    for index in range(1, count + 1):
        tile = (a[0] + _div_round((b[0] - a[0]) * index, count),
                a[1] + _div_round((b[1] - a[1]) * index, count))
        if any(_cheb(tile, point) <= radius for point, radius in hazards):
            return True
    return False


def _detour(cur: _Point, step: _Point, stride: int, box: _Box, hazards: _Hazards) -> _Point | None:
    """Primeiro destino livre para o passo ``step``: a menor deviação que desvia.

    Tenta o mesmo passo deslocado na perpendicular (±1…±``AVOID_SHIFT_MAX``) e,
    se nenhum limpar, curvas de ±45°/±90° com o mesmo comprimento — o
    planeamento iterativo volta ao rumo do destino a partir do novo ponto.
    """
    length = max(abs(step[0]), abs(step[1]))
    ux, uy = (step[0] > 0) - (step[0] < 0), (step[1] > 0) - (step[1] < 0)
    candidates = [(step[0] - shift * uy, step[1] + shift * ux) for shift in _SHIFTS]
    base = COMPASS.index(compass_bearing(step[0], step[1]) or "n")
    for turn in (1, -1, 2, -2):
        vx, vy = DIRECTION_VECTORS[COMPASS[(base + turn) % len(COMPASS)]]
        candidates.append((vx * length, vy * length))
    for vx, vy in candidates:
        vx, vy = max(-stride, min(stride, vx)), max(-stride, min(stride, vy))
        cand = _clamp_point((cur[0] + vx, cur[1] + vy), box)
        if _cheb(cur, cand) >= MIN_LEG_TILES and not _hits(cur, cand, hazards):
            return cand
    return None


def _walk(start: _Point, *, goal: _Point | None, vec: _Point | None, side: _Point | None,
          stride: int, legs: int, tiles: int, box: _Box, hazards: _Hazards) -> list[_Point]:
    """Waypoints de ``start`` ao ``goal`` (ou ao longo de ``vec``), um leg de cada vez.

    Planeamento iterativo (a reta recalcula-se a partir de cada waypoint, por isso
    um desvio arrasta o resto da rota). Cada volta gasta ≥ ``MIN_LEG_TILES`` do
    orçamento ``tiles`` ou termina: o laço é limitado. ``side`` ativa a
    escadaria do ``executor._hunt_plan_steps`` (anda, desliza, anda, desliza).
    """
    points: list[_Point] = []
    cur = start
    while len(points) < legs and tiles >= MIN_LEG_TILES:
        reach = min(stride, tiles)
        if goal is not None:
            dx, dy = goal[0] - cur[0], goal[1] - cur[1]
            dist = max(abs(dx), abs(dy))
            if dist <= ARRIVAL_TOLERANCE:
                break
            reach = min(reach, dist)
            step = (_div_round(dx * reach, dist), _div_round(dy * reach, dist))
        else:
            heading = side if (side is not None and len(points) % 2 == 1) else vec
            step = (heading[0] * reach, heading[1] * reach)  # type: ignore[index]
        nxt: _Point | None = _clamp_point((cur[0] + step[0], cur[1] + step[1]), box)
        if hazards and _hits(cur, nxt, hazards):  # type: ignore[arg-type]
            nxt = _detour(cur, step, stride, box, hazards)
        if nxt is None or _cheb(cur, nxt) < MIN_LEG_TILES:
            break
        tiles -= _cheb(cur, nxt)
        points.append(nxt)
        cur = nxt
    return points


def plan_waypoints(
    origin: Any, goal: Any = None, *, direction: Any = None, distance: Any = None,
    stride: int = DEFAULT_STRIDE, max_legs: int = DEFAULT_MAX_LEGS, bounds: Any = None,
    avoid: Iterable[Any] = (), avoid_radius: int = AVOID_RADIUS,
    staircase: bool = False, side: Any = None,
) -> list[tuple[int, int]]:
    """Waypoints ``[(x, y), …]`` de uma rota reta em legs de ≤ ``stride`` tiles.

    * **destino** (``goal=(x, y)``): reta da ``origin`` ao ``goal``; ``direction``
      é ignorado e ``distance`` (opcional) limita os tiles percorridos;
    * **rumo** (``direction="ne"``): ``distance`` tiles (por omissão o orçamento
      ``stride * max_legs``); ``staircase=True`` alterna o rumo com ``side`` (por
      omissão o lado do ``executor._HUNT_SIDES``) — a escadaria que não fica
      presa contra paredes.

    Nunca excede ``stride * max_legs`` tiles; waypoints ficam dentro de ``bounds``
    (``(min_x, min_y, max_x, max_y)``; por omissão ``≥ 0``); nenhum leg passa no
    raio de ``avoid`` (lista de ``(x, y)``/unidades). Origem ``None``/inválida/
    fora de ``bounds``, destino ou rumo inválido (``up``/``down``), distância ≤ 1
    (a ponte já dá "arrived") ou nula ⇒ ``[]``. Nunca levanta.
    """
    start, box = as_xy(origin), _bounds(bounds)
    if start is None or not (box[0] <= start[0] <= box[2] and box[1] <= start[1] <= box[3]):
        return []
    stride = _clamp_int(stride, MIN_STRIDE, MAX_STRIDE, DEFAULT_STRIDE)
    legs = _clamp_int(max_legs, 1, MAX_LEGS, DEFAULT_MAX_LEGS)
    tiles = stride * legs
    if distance is not None:
        asked = _as_int(distance)
        if asked is None or asked < MIN_LEG_TILES:
            return []
        tiles = min(tiles, asked)
    common = dict(stride=stride, legs=legs, tiles=tiles, box=box,
                  hazards=_hazards(start, avoid, avoid_radius))
    if goal is not None:
        target = as_xy(goal)
        if target is None:
            return []
        return _walk(start, goal=_clamp_point(target, box), vec=None, side=None, **common)
    name = normalise_compass(direction)
    if name not in DIRECTION_VECTORS:
        return []
    slide = None
    if staircase:
        slide = DIRECTION_VECTORS.get(normalise_compass(side) or _HUNT_SIDES[name])
    return _walk(start, goal=None, vec=DIRECTION_VECTORS[name], side=slide, **common)


# -------------------------------------------------------------------- urgência


def urgency_level(urgency: Any = None) -> str:
    """``safe``/``pressing``/``critical`` a partir da resposta ``score`` do JEV-A.

    Aceita o índice (``0``/``1``/``2``; fracionário arredonda para o nível **mais
    urgente** nos empates — a escolha prudente), o texto da régua (prefixo
    ``Safe``/``Pressing``/``Critical``, sem distinguir maiúsculas), um objeto com
    ``.value`` (o ``Answer`` do cliente) ou ``None``. Fora do intervalo fixa-se
    nos extremos; NaN/bool/texto desconhecido ⇒ :data:`DEFAULT_URGENCY`.
    """
    value = urgency
    if value is not None and not isinstance(value, (str, int, float)):
        value = _attr(value, "value")
    if isinstance(value, str):
        text = value.strip().lower()
        for level in URGENCY_LEVELS:
            if text.startswith(level):
                return level
    number = _number(value)
    if number is None:
        return DEFAULT_URGENCY
    return URGENCY_LEVELS[max(0, min(len(URGENCY_LEVELS) - 1, math.floor(number + 0.5)))]


def _profile_for(level: str, profiles: Mapping[str, Any] | None) -> UrgencyProfile:
    base = URGENCY_PROFILES[level]
    raw = profiles.get(level) if isinstance(profiles, Mapping) else None
    try:
        stride, max_legs, reassess = raw if raw is not None else base
    except (TypeError, ValueError):
        stride, max_legs, reassess = base
    return UrgencyProfile(
        stride=_clamp_int(stride, MIN_STRIDE, MAX_STRIDE, base.stride),
        max_legs=_clamp_int(max_legs, 1, MAX_LEGS, base.max_legs),
        reassess_every=_clamp_int(reassess, 1, MAX_LEGS, base.reassess_every),
    )


def urgency_profile(urgency: Any = None, *, profiles: Mapping[str, Any] | None = None) -> UrgencyProfile:
    """``(stride, max_legs, reassess_every)`` do nível (ver :func:`urgency_level`).

    Safe: 10 tiles × 6 legs · Pressing: 6 × 4 · Critical: 3 × 2. ``profiles``
    sobrepõe níveis (``{"safe": (12, 5, 2)}``; valores fixados aos limites).
    """
    return _profile_for(urgency_level(urgency), profiles)


# --------------------------------------------------------------- dataclasses


@dataclass(frozen=True)
class Waypoint:
    """Ponto da rota: ``x``/``y`` inteiros (``±MAX_COORD``), ``z`` e ``name`` opcionais.

    O construtor recusa (``ValueError``) coordenadas inválidas — quem interpola
    um ``Waypoint`` no DSL nunca vê lixo. Para dados externos use :meth:`coerce`.
    """

    x: int
    y: int
    z: int | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        x, y = _coord(self.x), _coord(self.y)
        z = None if self.z is None else _coord(self.z)
        if x is None or y is None or (self.z is not None and z is None):
            raise ValueError(f"waypoint ({self.x!r}, {self.y!r}, z={self.z!r}) needs integer "
                             f"coordinates within ±{MAX_COORD}")
        for key, value in (("x", x), ("y", y), ("z", z), ("name", _clean_text(self.name) or None)):
            object.__setattr__(self, key, value)

    @property
    def xy(self) -> _Point:
        return (self.x, self.y)

    @classmethod
    def coerce(cls, value: Any) -> "Waypoint | None":
        """Tolerante: ``[x, y]``, ``(x, y, z)``, ``{"x","y","z","name"|"zone"}``, objeto."""
        if isinstance(value, Waypoint):
            return value
        point = as_xy(value)
        if point is None:
            return None
        name = None
        if isinstance(value, Mapping):
            name = value.get("name") or value.get("zone") or value.get("label")
        elif not isinstance(value, (list, tuple)):
            name = _attr(value, "name")
        return cls(point[0], point[1], z=_z_of(value), name=name if isinstance(name, str) else None)

    def to_params(self) -> dict[str, Any]:
        return {"x": self.x, "y": self.y, "z": self.z, "name": self.name}


@dataclass(frozen=True)
class RouteLeg:
    """Troço da rota: ir (``goto``) a ``waypoint``, ≤ ``stride`` tiles do leg anterior.

    ``steps_hint`` = tiles planeados (Chebyshev; base do orçamento de passos);
    ``direction`` = rumo do troço (informativo; ``""`` se desconhecido).
    """

    index: int
    waypoint: Waypoint
    steps_hint: int = 0
    direction: str = ""

    def __post_init__(self) -> None:
        index, steps = _as_int(self.index), _as_int(self.steps_hint)
        direction = self.direction.strip().lower() if isinstance(self.direction, str) else None
        if (index is None or index < 0 or not isinstance(self.waypoint, Waypoint)
                or steps is None or not 0 <= steps <= MAX_PLAN_STEPS
                or direction not in ("", *COMPASS)):
            raise ValueError(f"invalid route leg: index={self.index!r} steps={self.steps_hint!r} "
                             f"direction={self.direction!r} waypoint={self.waypoint!r}")
        for key, value in (("index", index), ("steps_hint", steps), ("direction", direction)):
            object.__setattr__(self, key, value)


@dataclass(frozen=True)
class RoutePlan:
    """Rota de uma decisão: legs ordenados + o contexto que a justifica.

    ``urgency``: nível; ``stride``: teto por leg; ``created_step``: passo do loop
    (auditoria); ``reason``: ``"<verbo>:<modo>"`` (``goal``/``waypoints``/
    ``direction``). Extras: ``reassess_every`` (ver :class:`UrgencyProfile`),
    ``origin``, ``truncated`` (o orçamento cortou a rota antes do destino) e
    ``watch_prey`` (parar quando uma presa entra em vista).
    """

    id: str
    goal_name: str | None
    legs: tuple[RouteLeg, ...]
    urgency: str = DEFAULT_URGENCY
    stride: int = DEFAULT_STRIDE
    created_step: int = 0
    reason: str = ""
    reassess_every: int = 1
    origin: Waypoint | None = None
    truncated: bool = False
    watch_prey: bool = False

    def __post_init__(self) -> None:
        legs = tuple(self.legs) if isinstance(self.legs, (list, tuple)) else ()
        route_id = _clean_text(self.id, 64)
        if not (route_id and 0 < len(legs) <= MAX_LEGS and all(isinstance(l, RouteLeg) for l in legs)
                and (self.origin is None or isinstance(self.origin, Waypoint))):
            raise ValueError(f"invalid route: needs an id, 1..{MAX_LEGS} RouteLeg and a Waypoint origin")
        values = {
            "id": route_id, "legs": legs, "goal_name": _clean_text(self.goal_name) or None,
            "urgency": urgency_level(self.urgency),
            "stride": _clamp_int(self.stride, MIN_STRIDE, MAX_STRIDE, DEFAULT_STRIDE),
            "created_step": max(0, _as_int(self.created_step) or 0),
            "reason": _clean_text(self.reason, 80),
            "reassess_every": _clamp_int(self.reassess_every, 1, MAX_LEGS, 1),
            "truncated": bool(self.truncated), "watch_prey": bool(self.watch_prey),
        }
        for key, value in values.items():
            object.__setattr__(self, key, value)

    # ----------------------------------------------------------------- consultas
    @property
    def total_tiles(self) -> int:
        """Tiles planeados (soma dos legs): nunca mais que ``stride × nº de legs``."""
        return sum(leg.steps_hint for leg in self.legs)

    @property
    def final(self) -> Waypoint:
        return self.legs[-1].waypoint

    @property
    def bearing(self) -> str:
        """Rumo do deslocamento total (origem → último waypoint, ou pela soma dos legs)."""
        if self.origin is not None:
            return compass_bearing(self.final.x - self.origin.x, self.final.y - self.origin.y)
        dx = dy = 0
        for leg in self.legs:
            vx, vy = DIRECTION_VECTORS.get(leg.direction, (0, 0))
            dx, dy = dx + vx * leg.steps_hint, dy + vy * leg.steps_hint
        return compass_bearing(dx, dy)

    def leg_budget(self, leg: RouteLeg) -> int:
        """Passos de ``goto`` permitidos ao leg (``maxsteps=``): tiles × :data:`LEG_STEP_FACTOR`."""
        return min(MAX_PLAN_STEPS, max(MIN_LEG_TILES, leg.steps_hint) * LEG_STEP_FACTOR)

    def _leg(self, leg: Any) -> RouteLeg | None:
        if isinstance(leg, int) and not isinstance(leg, bool):
            leg = self.legs[leg] if 0 <= leg < len(self.legs) else None
        return leg if isinstance(leg, RouteLeg) and isinstance(leg.waypoint, Waypoint) else None

    def to_dsl(self, leg: "RouteLeg | int", *, with_budget: bool = False) -> str:
        """``"goto X,Y"`` (ou ``"goto X,Y maxsteps=N"``) do leg, ``""`` se inválido.

        Só ``int`` revalidados dentro de ``±MAX_COORD`` são interpolados — nada do
        jogo ou de um LLM chega ao comando. Nunca levanta.
        """
        target = self._leg(leg)
        x, y = (_coord(target.waypoint.x), _coord(target.waypoint.y)) if target else (None, None)
        if target is None or x is None or y is None:
            return ""
        text = f"goto {x},{y}"
        return f"{text} maxsteps={self.leg_budget(target)}" if with_budget else text

    def fallback_dsl(self, leg: "RouteLeg | int") -> str:
        """``"move <rumo> times=N"`` do leg, ``""`` sem rumo/passos válidos.

        Plano B quando o ``goto`` não anda (o BFS só chega a tiles andáveis e o
        waypoint foi escolhido às cegas): o ``move`` avança até onde der. O rumo
        vem do vocabulário planar e ``N`` de ``steps_hint`` — nunca de texto livre.
        """
        target = self._leg(leg)
        if target is None or target.direction not in COMPASS or not 0 < target.steps_hint <= MAX_PLAN_STEPS:
            return ""
        return f"move {target.direction} times={target.steps_hint}"

    # ------------------------------------------------------------ (de)serialização
    def to_params(self) -> dict[str, Any]:
        """Mapeamento JSON-safe para ``MacroAction.params["route"]`` (ida e volta exata)."""
        return {
            "v": 1, "id": self.id, "goal": self.goal_name, "urgency": self.urgency,
            "stride": self.stride, "reassess_every": self.reassess_every,
            "created_step": self.created_step, "reason": self.reason,
            "truncated": self.truncated, "watch_prey": self.watch_prey,
            "origin": self.origin.to_params() if self.origin is not None else None,
            "legs": [{"i": leg.index, **leg.waypoint.to_params(), "steps": leg.steps_hint,
                      "dir": leg.direction} for leg in self.legs],
        }

    @classmethod
    def from_params(cls, params: Any) -> "RoutePlan | None":
        """Reconstrói a rota de ``to_params()`` — ESTRITO no que vai para o DSL; nunca levanta.

        Aceita o mapeamento da rota ou os ``params`` da ação (``{"route": {...}}``).
        ``None`` se qualquer leg for inválido (coordenada não inteira/fora de
        ``±MAX_COORD``, direção fora do vocabulário — ex. ``"n; key A_TALK"`` —,
        passos absurdos) ou exceder o ``stride`` declarado (o orçamento por
        decisão revalida-se aqui) ou se o esquema ``v`` for desconhecido.
        Contexto inválido (urgência, nomes) cai em valores neutros.
        """
        data = params
        if isinstance(data, Mapping) and not isinstance(data.get("legs"), (list, tuple)):
            data = data.get("route")
        raw_legs = data.get("legs") if isinstance(data, Mapping) else None
        if not isinstance(raw_legs, (list, tuple)) or not 0 < len(raw_legs) <= MAX_LEGS:
            return None
        if data.get("v", 1) != 1:  # esquema desconhecido: não se adivinha
            return None
        try:
            origin = Waypoint.coerce(data["origin"]) if data.get("origin") is not None else None
            if data.get("origin") is not None and origin is None:
                return None
            legs = []
            for position, entry in enumerate(raw_legs):
                waypoint = Waypoint.coerce(entry) if isinstance(entry, Mapping) else None
                direction = entry.get("dir", entry.get("direction")) if waypoint else None
                steps = _as_int(entry.get("steps", entry.get("steps_hint", 0))) if waypoint else None
                if waypoint is None or steps is None or not 0 <= steps <= MAX_PLAN_STEPS:
                    return None
                if direction is not None and (not isinstance(direction, str)
                                              or direction.strip().lower() not in ("", *COMPASS)):
                    return None
                legs.append(RouteLeg(position, waypoint, steps, direction or ""))
            plan = cls(
                id=data.get("id"), goal_name=data.get("goal", data.get("goal_name")),
                legs=tuple(legs), urgency=data.get("urgency"), stride=data.get("stride"),
                created_step=data.get("created_step", 0), reason=data.get("reason") or "",
                reassess_every=data.get("reassess_every", 1), origin=origin,
                truncated=data.get("truncated") is True, watch_prey=data.get("watch_prey") is True,
            )
        except Exception:  # noqa: BLE001 - dados externos: inválido ⇒ None
            return None
        previous = plan.origin.xy if plan.origin is not None else None
        for leg in plan.legs:  # orçamento por decisão: nenhum leg passa do stride
            if previous is not None and _cheb(previous, leg.waypoint.xy) > plan.stride:
                return None
            previous = leg.waypoint.xy
        return plan


# ----------------------------------------------------------- construção da rota


def _destinations(params: Mapping[str, Any]) -> tuple[list[Waypoint], int, str | None]:
    """Paragens declaradas: ``waypoints`` (intermédias) e depois ``x``/``y``.

    Devolve ``(paragens, nº de waypoints válidos, nome do destino)``.
    """
    stops: list[Waypoint] = []
    raw = params.get("waypoints")
    for entry in (raw[:MAX_VIA_WAYPOINTS] if isinstance(raw, (list, tuple)) else ()):
        try:
            waypoint = Waypoint.coerce(entry)
        except (ValueError, TypeError):
            waypoint = None
        if waypoint is not None:
            stops.append(waypoint)
    via, name = len(stops), _clean_text(params.get("destination") or params.get("name")) or None
    goal = as_xy(params)
    if goal is not None:  # o destino fecha a rota (e leva o ``z``/nome); repetir o último é no-op
        stops.append(Waypoint(goal[0], goal[1], z=_coord(params.get("z")), name=name))
    return stops, via, name


def build_route(
    action_params: Any, origin: Any, urgency: Any = None, step: int = 0, *,
    verb: str | None = None, avoid: Iterable[Any] = (), bounds: Any = None,
    profiles: Mapping[str, Any] | None = None, goal_name: str | None = None,
    watch_prey: bool | None = None, avoid_radius: int = AVOID_RADIUS,
) -> RoutePlan | None:
    """Rota de movimento a partir dos ``params`` que o gerador de candidatos já produz.

    Modos (por prioridade, inferidos dos params): (1) ``waypoints`` (lista de
    ``[x, y]``/``{x, y, name}``) e/ou ``x``/``y`` — paragens por ordem, cada uma
    em legs de ≤ ``stride`` tiles; (2) ``direction`` — ``steps`` tiles no
    ``flee``, o orçamento inteiro da urgência em ``explore``/``hunt`` (o ``hunt``
    usa escadaria, não fica preso a paredes).

    ``verb`` (``action.verb``) restringe a :data:`MOVEMENT_VERBS`; outro verbo ⇒
    ``None``. Origem desconhecida, nada para onde ir (``navigate_to`` só com
    nome) ou distância ≤ 1 ⇒ ``None``: o executor segue pelo plano clássico. A
    urgência define ``stride``/``max_legs``; sem resposta vale
    :data:`DEFAULT_URGENCY`. ``watch_prey`` sobrepõe o defeito por verbo
    (``explore``/``hunt`` ligados); ``avoid_radius`` é a margem aos tiles de
    ``avoid``. Nunca levanta (erro inesperado ⇒ ``None``).
    """
    try:
        return _build_route(action_params, origin, urgency, step, verb, avoid, bounds,
                            profiles, goal_name, watch_prey, avoid_radius)
    except Exception:  # noqa: BLE001 - uma rota nunca derruba o loop
        log.debug("build_route failed for %r", action_params, exc_info=True)
        return None


def _build_route(params: Any, origin: Any, urgency: Any, step: Any, verb: Any, avoid: Any,
                 bounds: Any, profiles: Any, goal_name: Any, watch_prey: Any,
                 avoid_radius: Any) -> RoutePlan | None:
    if not isinstance(params, Mapping):
        return None
    kind = str(verb or "").strip().lower().split(":", 1)[0]
    kind = _VERB_ALIASES.get(kind, kind)
    if kind and kind not in MOVEMENT_VERBS:
        return None
    start, box = as_xy(origin), _bounds(bounds)
    if start is None or _clamp_point(start, box) != start:
        return None
    level = urgency_level(urgency)
    profile = _profile_for(level, profiles)
    stops, via, destination = _destinations(params)
    kind = kind or ("navigate_to" if stops else "explore")
    walk = dict(stride=profile.stride, box=box, hazards=_hazards(start, avoid, avoid_radius))
    legs_left, tiles_left = profile.max_legs, profile.stride * profile.max_legs
    points: list[_Point] = []
    truncated = False
    if stops:
        mode, cur = ("waypoints" if via else "goal"), start
        for stop in stops:
            target = _clamp_point(stop.xy, box)
            for point in _walk(cur, goal=target, vec=None, side=None, legs=legs_left,
                               tiles=tiles_left, **walk):
                legs_left, tiles_left = legs_left - 1, tiles_left - _cheb(cur, point)
                points.append(point)
                cur = point
            if _cheb(cur, target) > ARRIVAL_TOLERANCE:
                truncated = True
                break
    else:
        mode = "direction"
        name = normalise_compass(params.get("direction") or params.get("dir"))
        if name not in DIRECTION_VECTORS:
            return None
        if kind == "flee":  # "0 passos" = não andar: sem orçamento não há legs ⇒ ``None``
            asked = _as_int(params.get("steps"))
            tiles_left = min(tiles_left, DEFAULT_FLEE_TILES if asked is None else asked)
        slide = DIRECTION_VECTORS[_HUNT_SIDES[name]] if kind == "hunt" else None
        points = _walk(start, goal=None, vec=DIRECTION_VECTORS[name], side=slide,
                       legs=legs_left, tiles=tiles_left, **walk)
    if not points:
        return None

    final = stops[-1] if stops and not truncated else None
    legs, previous = [], start
    for position, point in enumerate(points):
        last = position == len(points) - 1
        waypoint = Waypoint(point[0], point[1], z=final.z if last and final else None,
                            name=final.name if last and final else None)
        legs.append(RouteLeg(position, waypoint, _cheb(previous, point),
                             compass_bearing(point[0] - previous[0], point[1] - previous[1])))
        previous = point
    reason, created = f"{kind}:{mode}", _clamp_int(step, 0, 10**9, 0)
    digest = hashlib.sha1(json.dumps(
        [start, points, level, profile.stride, reason], separators=(",", ":")).encode("ascii"),
        usedforsecurity=False).hexdigest()
    return RoutePlan(
        id=f"route-{created}-{digest[:8]}",
        goal_name=_clean_text(goal_name) or destination or (stops[-1].name if stops else None),
        legs=tuple(legs), urgency=level, stride=profile.stride, created_step=created,
        reason=reason,
        # fugir: reler depois de CADA leg, qualquer que seja a urgência declarada
        reassess_every=1 if kind == "flee" else profile.reassess_every,
        origin=Waypoint(start[0], start[1], z=_z_of(origin)),
        truncated=truncated,
        watch_prey=kind in _PREY_WATCH_VERBS if watch_prey is None else bool(watch_prey),
    )


def route_summary(plan: Any) -> str:
    """Linha EN curta para a descrição do candidato do JEV (``""`` sem rota válida).

    ``"Travel ~24 tiles NE in 3 legs, re-assessing between legs"``; com destino
    ``"Travel ~18 tiles E to the objective in 3 legs, …"`` (``toward`` quando o
    orçamento corta a rota antes do destino).
    """
    if not isinstance(plan, RoutePlan):
        return ""
    count = len(plan.legs)
    parts = [f"Travel ~{plan.total_tiles} tiles"]
    if plan.bearing:
        parts.append(plan.bearing.upper())
    name = _clean_text(plan.goal_name, 40)
    if name:
        parts.append(f"{'toward' if plan.truncated else 'to'} {name}")
    parts.append(f"in {count} leg{'s' if count != 1 else ''}")
    return f"{' '.join(parts)}, re-assessing {'between legs' if count > 1 else 'on arrival'}"


# -------------------------------------------------------------- avaliação de legs


@dataclass(frozen=True)
class LegVerdict:
    """Resultado de :func:`evaluate_leg`.

    ``kind``: ``continue`` (andou, ainda não chegou) · ``arrived`` (a ≤ 1 tile
    do waypoint) · ``blocked`` (posição inalterada: alimenta o StuckDetector) ·
    ``abort`` (parar já e reavaliar). ``distance``/``moved``: tiles que faltam /
    percorridos (``None`` se desconhecidos).
    """

    kind: str
    reason: str
    distance: int | None = None
    moved: int | None = None

    @property
    def ok(self) -> bool:
        """A rota pode seguir para o leg seguinte."""
        return self.kind in (LEG_CONTINUE, LEG_ARRIVED)

    @property
    def opportunity(self) -> bool:
        """Paragem por oportunidade (presa à vista), não por falha."""
        return self.kind == LEG_ABORT and self.reason in OPPORTUNITY_REASONS


def _pos3(value: Any) -> tuple[int, int, int | None] | None:
    point = as_xy(value)
    return None if point is None else (point[0], point[1], _z_of(value))


def _count(value: Any) -> int:
    return max(0, _as_int(value) or 0)


def evaluate_leg(
    before_pos: Any, after_pos: Any, leg: Any, *,
    hostile_count_before: int = 0, hostile_count_after: int = 0,
    blood_before: float | None = None, blood_after: float | None = None,
    focus_before: str | None = None, focus_after: str | None = None,
    great_danger_before: bool = False, great_danger_after: bool = False,
    prey_before: int = 0, prey_after: int = 0, stop_on_new_prey: bool = False,
    alive_after: bool = True, tolerance: int = ARRIVAL_TOLERANCE,
    blood_drop_fraction: float = BLOOD_DROP_ABORT_FRACTION,
) -> LegVerdict:
    """Veredicto de um leg executado: segue, chegou, está bloqueado ou aborta.

    Segurança primeiro — ``abort`` por: aventureiro morto, mudança de *focus*,
    hostil novo, perigo grande novo, sangue a cair mais de ``blood_drop_fraction``
    (10 %) e, com ``stop_on_new_prey``, presa nova (``new_prey``: ver
    :data:`OPPORTUNITY_REASONS`). Depois, pela posição: sem posição final ⇒
    ``abort`` (``position_unknown``: não se verifica o que não se vê); a ≤
    ``tolerance`` tiles do waypoint ⇒ ``arrived``; posição inalterada ⇒
    ``blocked``; senão ``continue``. ``leg``: :class:`RouteLeg`/:class:`Waypoint`/
    ``(x, y)``. Nunca levanta.
    """
    before, after = _pos3(before_pos), _pos3(after_pos)
    target = as_xy(_attr(leg, "waypoint") or leg)
    moved = _cheb(before[:2], after[:2]) if before and after else None  # type: ignore[arg-type]
    remaining = _cheb(after[:2], target) if after and target else None  # type: ignore[arg-type]

    def verdict(kind: str, reason: str) -> LegVerdict:
        return LegVerdict(kind, reason, distance=remaining, moved=moved)

    start_blood, end_blood = _number(blood_before), _number(blood_after)
    drop = _number(blood_drop_fraction)
    if not alive_after:
        return verdict(LEG_ABORT, "adventurer_dead")
    if focus_before and focus_after and focus_before != focus_after:
        return verdict(LEG_ABORT, "focus_changed")
    if _count(hostile_count_after) > _count(hostile_count_before):
        return verdict(LEG_ABORT, "new_hostile")
    if great_danger_after and not great_danger_before:
        return verdict(LEG_ABORT, "great_danger_appeared")
    if (start_blood is not None and end_blood is not None and drop is not None
            and start_blood > 0 and (start_blood - end_blood) > start_blood * drop):
        return verdict(LEG_ABORT, "blood_drop")
    if stop_on_new_prey and _count(prey_after) > _count(prey_before):
        return verdict(LEG_ABORT, "new_prey")
    if after is None:
        return verdict(LEG_ABORT, "position_unknown")
    if remaining is not None and remaining <= _clamp_int(tolerance, 0, MAX_STRIDE, ARRIVAL_TOLERANCE):
        return verdict(LEG_ARRIVED, "arrived")
    if before is not None and before == after:
        return verdict(LEG_BLOCKED, "position_unchanged")
    return verdict(LEG_CONTINUE, "progress")


class LegObservation(NamedTuple):
    """O que :func:`evaluate_leg` precisa de um snapshot (ver :func:`observe_state`)."""

    pos: tuple[int, int, int | None] | None = None
    hostiles: int = 0
    great_danger: bool = False
    prey: int = 0
    blood: float | None = None
    focus: str | None = None
    alive: bool = True


def observe_state(state: Any) -> LegObservation:
    """Posição, hostis, presas, sangue e *focus* de um ``GameState`` (tolerante, nunca levanta).

    ``prey`` = unidades visíveis, não hostis, ``kind == "animal"`` (a
    classificação do ``dfb-state``: sapientes nunca contam como presa).
    """
    try:
        metrics = getattr(state, "metrics", None)
        hostiles = tuple(getattr(getattr(metrics, "threats", None), "hostiles", ()) or ())
        units = tuple(getattr(getattr(metrics, "units_nearby", None), "units", ()) or ())
        focus = getattr(state, "focus", None)
        return LegObservation(
            pos=_pos3(getattr(metrics, "location", None)) or _pos3(getattr(state, "adventurer", None)),
            hostiles=len(hostiles),
            great_danger=any(bool(getattr(unit, "great_danger", False)) for unit in hostiles),
            prey=sum(1 for unit in units if getattr(unit, "kind", None) == "animal"
                     and getattr(unit, "visible", True) and not getattr(unit, "hostile", False)),
            blood=_number(getattr(getattr(metrics, "health_injuries", None), "blood_current", None)),
            focus=focus if isinstance(focus, str) and focus else None,
            alive=bool(getattr(state, "is_alive", True)),
        )
    except Exception:  # noqa: BLE001 - observar nunca derruba o loop
        log.debug("observe_state failed", exc_info=True)
        return LegObservation()


def evaluate_states(before: Any, after: Any, leg: Any, *, stop_on_new_prey: bool = False,
                    **tuning: Any) -> LegVerdict:
    """:func:`evaluate_leg` entre dois ``GameState`` (``tuning``: ``tolerance``, ``blood_drop_fraction``)."""
    seen, now = observe_state(before), observe_state(after)
    return evaluate_leg(
        seen.pos, now.pos, leg,
        hostile_count_before=seen.hostiles, hostile_count_after=now.hostiles,
        blood_before=seen.blood, blood_after=now.blood,
        focus_before=seen.focus, focus_after=now.focus,
        great_danger_before=seen.great_danger, great_danger_after=now.great_danger,
        prey_before=seen.prey, prey_after=now.prey,
        stop_on_new_prey=stop_on_new_prey, alive_after=now.alive, **tuning,
    )


# ------------------------------------------------------------------ exploração


def next_unvisited_direction(visited: Any = (), current: Any = None) -> str:
    """Próximo rumo por tentar, sem aleatoriedade: o mais próximo do rumo atual primeiro.

    ``visited``: rumos já tentados (qualquer grafia de :func:`normalise_compass`;
    lixo ignorado). A partir de ``current`` a ordem é ±45°, ±90°, ±135°, 180° —
    evita inversões de marcha; sem ``current`` parte de ``n`` em sentido
    horário. Tudo visitado ⇒ roda 45° (o detetor de stuck nunca fica sem rumo).
    """
    if isinstance(visited, (str, bytes)):
        visited = (visited,)
    try:
        seen = {normalise_compass(item) for item in itertools.islice(iter(visited), 64)}
    except TypeError:
        seen = set()
    heading = normalise_compass(current)
    if not heading:
        return next((name for name in COMPASS if name not in seen), COMPASS[0])
    base = COMPASS.index(heading)
    for offset in (1, -1, 2, -2, 3, -3, 4):
        name = COMPASS[(base + offset) % len(COMPASS)]
        if name not in seen:
            return name
    return COMPASS[(base + 1) % len(COMPASS)]
