"""Estruturas tipadas do snapshot do jogo (camada de GAME STATE).

Todos os campos são lidos do JSON do ``df_bridge.py snapshot --json`` com tolerância a
ausências: campo em falta ou com tipo inesperado vira ``None`` (ou lista/contagem vazia),
nunca uma exceção. Nada aqui inventa valores — o que o jogo não expõe é ``None``.

Os 8 grupos de métricas (ordem do contrato) estão em :data:`METRIC_GROUPS`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Dict, Mapping, Optional, Tuple

__all__ = [
    "METRIC_GROUPS", "Adventurer", "GameClock", "GameEvent", "GameState",
    "HealthInjuries", "Inventory", "InventoryItem", "Location", "Metrics",
    "NeedsState", "Threats", "UnitsNearby", "VisibleUnit", "Wound",
    "parse_ts",
]

METRIC_GROUPS: Tuple[str, ...] = (
    "health_injuries", "needs_state", "inventory", "threats", "location",
    "game_clock", "units_nearby", "events",
)


# ---------- coerção tolerante ----------
def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _items(value: Any) -> list:
    return value if isinstance(value, list) else []


def _text(value: Any) -> Optional[str]:
    if isinstance(value, str):
        return value or None
    return None


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _int(value: Any) -> Optional[int]:
    number = _num(value)
    return int(number) if number is not None else None


def _flag(value: Any) -> bool:
    return value is True


def parse_ts(value: Any) -> Optional[datetime]:
    """ISO-8601 (com sufixo ``Z``) → ``datetime`` com tzinfo UTC; inválido → ``None``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------- grupos ----------
@dataclass(frozen=True)
class Wound:
    """Um ferimento (``unit_wound``): parte do corpo, severidade 0-100, sangra?"""

    body_part: Optional[str] = None
    severity: Optional[float] = None
    bleeding: bool = False
    severed: bool = False
    pain: Optional[float] = None

    @classmethod
    def from_dict(cls, data: Any) -> "Wound":
        d = _mapping(data)
        return cls(body_part=_text(d.get("body_part")), severity=_num(d.get("severity")),
                   bleeding=_flag(d.get("bleeding")), severed=_flag(d.get("severed")),
                   pain=_num(d.get("pain")))


@dataclass(frozen=True)
class HealthInjuries:
    """Grupo ``health_injuries``. HP não existe em adventure mode → ``None``."""

    status: Optional[str] = None
    status_conditions: Tuple[str, ...] = ()
    wounds: Tuple[Wound, ...] = ()
    wound_count: int = 0
    pain: Optional[float] = None
    hp_current: Optional[float] = None
    hp_max: Optional[float] = None
    blood_current: Optional[float] = None
    blood_max: Optional[float] = None

    @property
    def is_healthy(self) -> bool:
        return self.status == "healthy"

    @property
    def is_bleeding(self) -> bool:
        return "bleeding" in self.status_conditions

    @property
    def is_incapacitated(self) -> bool:
        return self.status in ("unconscious", "dead")

    @classmethod
    def from_dict(cls, data: Any) -> "HealthInjuries":
        d = _mapping(data)
        conditions = tuple(c for c in (_text(x) for x in _items(d.get("status_conditions"))) if c)
        wounds = tuple(Wound.from_dict(w) for w in _items(d.get("wounds")))
        return cls(status=_text(d.get("status")), status_conditions=conditions, wounds=wounds,
                   wound_count=_int(d.get("wound_count")) or len(wounds),
                   pain=_num(d.get("pain")), hp_current=_num(d.get("hp_current")),
                   hp_max=_num(d.get("hp_max")), blood_current=_num(d.get("blood_current")),
                   blood_max=_num(d.get("blood_max")))


@dataclass(frozen=True)
class NeedsState:
    """Grupo ``needs_state``: fome/sede/sono em EN + stress 0-6 (0 = mais stressado)."""

    hunger: Optional[str] = None
    thirst: Optional[str] = None
    sleep: Optional[str] = None
    stress_level: Optional[float] = None
    hunger_timer: Optional[float] = None
    thirst_timer: Optional[float] = None
    sleepiness_timer: Optional[float] = None

    @classmethod
    def from_dict(cls, data: Any) -> "NeedsState":
        d = _mapping(data)
        return cls(hunger=_text(d.get("hunger")), thirst=_text(d.get("thirst")),
                   sleep=_text(d.get("sleep")), stress_level=_num(d.get("stress_level")),
                   hunger_timer=_num(d.get("hunger_timer")),
                   thirst_timer=_num(d.get("thirst_timer")),
                   sleepiness_timer=_num(d.get("sleepiness_timer")))


@dataclass(frozen=True)
class InventoryItem:
    """Um item carregado. ``weight_kg`` vem de ``massst`` (kg); ``None`` se não calculado."""

    name: Optional[str] = None
    quantity: Optional[float] = None
    weight_kg: Optional[float] = None
    equipped: bool = False
    slot: Optional[str] = None
    material: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Any) -> "InventoryItem":
        d = _mapping(data)
        return cls(name=_text(d.get("name")), quantity=_num(d.get("quantity")),
                   weight_kg=_num(d.get("weight_kg")), equipped=_flag(d.get("equipped")),
                   slot=_text(d.get("slot")), material=_text(d.get("material")))


@dataclass(frozen=True)
class Inventory:
    """Grupo ``inventory``. Capacidade de carga não é exposta → ``capacity_kg`` ``None``."""

    items: Tuple[InventoryItem, ...] = ()
    item_count: int = 0
    total_weight_kg: Optional[float] = None
    capacity_kg: Optional[float] = None
    equipped_weapon: Optional[str] = None
    equipped_armor: Tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Any) -> "Inventory":
        d = _mapping(data)
        items = tuple(InventoryItem.from_dict(i) for i in _items(d.get("items")))
        armor = tuple(a for a in (_text(x) for x in _items(d.get("equipped_armor"))) if a)
        return cls(items=items, item_count=_int(d.get("item_count")) or len(items),
                   total_weight_kg=_num(d.get("total_weight_kg")),
                   capacity_kg=_num(d.get("capacity_kg")),
                   equipped_weapon=_text(d.get("equipped_weapon")), equipped_armor=armor)

    def find(self, needle: str) -> Tuple[InventoryItem, ...]:
        """Itens cujo nome contém ``needle`` (case-insensitive)."""
        low = (needle or "").lower()
        return tuple(i for i in self.items if i.name and low in i.name.lower())


@dataclass(frozen=True)
class VisibleUnit:
    """Unidade vista de perto: distância em tiles e direção (N/S/E/W, ``here`` = mesmo tile)."""

    name: Optional[str] = None
    distance: Optional[float] = None
    direction: Optional[str] = None
    hostile: bool = False
    visible: bool = True
    great_danger: bool = False

    @classmethod
    def from_dict(cls, data: Any) -> "VisibleUnit":
        d = _mapping(data)
        return cls(name=_text(d.get("name")), distance=_num(d.get("distance")),
                   direction=_text(d.get("direction")), hostile=_flag(d.get("hostile")),
                   visible=d.get("visible") is not False, great_danger=_flag(d.get("great_danger")))


@dataclass(frozen=True)
class Threats:
    """Grupo ``threats``: hostis visíveis + nível derivado (none/low/medium/high)."""

    count: int = 0
    level: Optional[str] = None
    hostiles: Tuple[VisibleUnit, ...] = ()

    @property
    def nearest(self) -> Optional[VisibleUnit]:
        return min(self.hostiles, key=lambda u: u.distance if u.distance is not None else 1e9) \
            if self.hostiles else None

    @property
    def imminent(self) -> bool:
        """Há hostil a <= 5 tiles (ameaça imediata para o gate de segurança)."""
        return any((u.distance or 1e9) <= 5 for u in self.hostiles)

    @classmethod
    def from_dict(cls, data: Any) -> "Threats":
        d = _mapping(data)
        hostiles = tuple(VisibleUnit.from_dict(u) for u in _items(d.get("hostiles")))
        return cls(count=_int(d.get("count")) if d.get("count") is not None else len(hostiles),
                   level=_text(d.get("level")), hostiles=hostiles)


@dataclass(frozen=True)
class Location:
    """Grupo ``location``: posição, região/sítio, resumo do entorno, destino de viagem."""

    x: Optional[int] = None
    y: Optional[int] = None
    z: Optional[int] = None
    world: Optional[str] = None
    region: Optional[str] = None
    site: Optional[str] = None
    site_type: Optional[str] = None
    surroundings: Optional[str] = None
    tile: Optional[str] = None
    travel_destination: Optional[str] = None

    @property
    def pos(self) -> Tuple[Optional[int], Optional[int], Optional[int]]:
        return (self.x, self.y, self.z)

    @classmethod
    def from_dict(cls, data: Any) -> "Location":
        d = _mapping(data)
        return cls(x=_int(d.get("x")), y=_int(d.get("y")), z=_int(d.get("z")),
                   world=_text(d.get("world")), region=_text(d.get("region")),
                   site=_text(d.get("site")), site_type=_text(d.get("site_type")),
                   surroundings=_text(d.get("surroundings")), tile=_text(d.get("tile")),
                   travel_destination=_text(d.get("travel_destination")))


@dataclass(frozen=True)
class GameClock:
    """Grupo ``game_clock``: data do jogo em texto + estação (``hour`` derivado do tick)."""

    date_text: Optional[str] = None
    year: Optional[int] = None
    month: Optional[str] = None
    day: Optional[int] = None
    season: Optional[str] = None
    tick: Optional[int] = None
    hour: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Any) -> "GameClock":
        d = _mapping(data)
        month = d.get("month")
        return cls(date_text=_text(d.get("date_text")), year=_int(d.get("year")),
                   month=month if isinstance(month, str) else None, day=_int(d.get("day")),
                   season=_text(d.get("season")), tick=_int(d.get("tick")),
                   hour=_int(d.get("hour")))


@dataclass(frozen=True)
class UnitsNearby:
    """Grupo ``units_nearby``: varredura crua (hostis e neutros) num raio pequeno."""

    count: int = 0
    units: Tuple[VisibleUnit, ...] = ()

    @classmethod
    def from_dict(cls, data: Any) -> "UnitsNearby":
        d = _mapping(data)
        units = tuple(VisibleUnit.from_dict(u) for u in _items(d.get("units")))
        return cls(count=_int(d.get("count")) if d.get("count") is not None else len(units),
                   units=units)


@dataclass(frozen=True)
class GameEvent:
    """Evento recente vindo do ``gamelog.txt`` (texto do jogo, em inglês)."""

    text: str = ""
    category: Optional[str] = None
    game_date: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Any) -> "GameEvent":
        if isinstance(data, str):
            return cls(text=data)
        d = _mapping(data)
        return cls(text=_text(d.get("text")) or "", category=_text(d.get("category")),
                   game_date=_text(d.get("game_date")))


@dataclass(frozen=True)
class Adventurer:
    """Bloco ``adventurer`` do snapshot (o que o Lua lê da unidade do jogador)."""

    name: Optional[str] = None
    race: Optional[str] = None
    profession: Optional[str] = None
    x: Optional[int] = None
    y: Optional[int] = None
    z: Optional[int] = None
    kills: Optional[int] = None
    stress_category: Optional[float] = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def pos(self) -> Tuple[Optional[int], Optional[int], Optional[int]]:
        return (self.x, self.y, self.z)

    @classmethod
    def from_dict(cls, data: Any) -> "Adventurer":
        d = _mapping(data)
        pos = _mapping(d.get("pos"))
        return cls(name=_text(d.get("name")), race=_text(d.get("race")),
                   profession=_text(d.get("profession")), x=_int(pos.get("x")),
                   y=_int(pos.get("y")), z=_int(pos.get("z")), kills=_int(d.get("kills")),
                   stress_category=_num(d.get("stress_category")), raw=dict(d))


@dataclass(frozen=True)
class Metrics:
    """Os 8 grupos de métricas do snapshot."""

    GROUP_NAMES: ClassVar[Tuple[str, ...]] = METRIC_GROUPS

    health_injuries: HealthInjuries = field(default_factory=HealthInjuries)
    needs_state: NeedsState = field(default_factory=NeedsState)
    inventory: Inventory = field(default_factory=Inventory)
    threats: Threats = field(default_factory=Threats)
    location: Location = field(default_factory=Location)
    game_clock: GameClock = field(default_factory=GameClock)
    units_nearby: UnitsNearby = field(default_factory=UnitsNearby)
    events: Tuple[GameEvent, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict)

    # nomes dos painéis de docs/api.md (health/needs) — mesma coisa, apelido curto
    @property
    def health(self) -> HealthInjuries:
        return self.health_injuries

    @property
    def needs(self) -> NeedsState:
        return self.needs_state

    def group(self, name: str) -> Any:
        """Devolve o grupo pelo nome do contrato; ``KeyError`` se não existir."""
        if name not in METRIC_GROUPS:
            raise KeyError(name)
        return getattr(self, name)

    @classmethod
    def from_dict(cls, data: Any) -> "Metrics":
        d = _mapping(data)
        return cls(
            health_injuries=HealthInjuries.from_dict(d.get("health_injuries")),
            needs_state=NeedsState.from_dict(d.get("needs_state")),
            inventory=Inventory.from_dict(d.get("inventory")),
            threats=Threats.from_dict(d.get("threats")),
            location=Location.from_dict(d.get("location")),
            game_clock=GameClock.from_dict(d.get("game_clock")),
            units_nearby=UnitsNearby.from_dict(d.get("units_nearby")),
            events=tuple(GameEvent.from_dict(e) for e in _items(d.get("events"))),
            raw=dict(d),
        )


@dataclass(frozen=True)
class GameState:
    """Snapshot completo do jogo (adventure mode) tal como a ponte o devolve."""

    SCHEMA_VERSION: ClassVar[int] = 1

    schema_version: int = 1
    ts: Optional[datetime] = None
    mode: str = "adventure"
    adventurer: Optional[Adventurer] = None
    metrics: Metrics = field(default_factory=Metrics)
    screen_text: str = ""
    focus: Optional[str] = None
    gamelog_events: Tuple[GameEvent, ...] = ()
    warnings: Tuple[str, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_alive(self) -> bool:
        return self.metrics.health_injuries.status != "dead"

    @property
    def recent_events(self) -> Tuple[GameEvent, ...]:
        return self.gamelog_events or self.metrics.events

    def group(self, name: str) -> Any:
        return self.metrics.group(name)

    def to_dict(self) -> Dict[str, Any]:
        """Devolve o JSON original (dict) — útil para logs/journal."""
        return dict(self.raw)

    @classmethod
    def from_dict(cls, data: Any) -> "GameState":
        d = _mapping(data)
        events = tuple(GameEvent.from_dict(e) for e in _items(d.get("gamelog_events")))
        adventurer = d.get("adventurer")
        return cls(
            schema_version=_int(d.get("schema_version")) or 1,
            ts=parse_ts(d.get("ts")),
            mode=_text(d.get("mode")) or "adventure",
            adventurer=Adventurer.from_dict(adventurer) if isinstance(adventurer, dict) else None,
            metrics=Metrics.from_dict(d.get("metrics")),
            screen_text=d.get("screen_text") if isinstance(d.get("screen_text"), str) else "",
            focus=_text(d.get("focus")),
            gamelog_events=events,
            warnings=tuple(w for w in (_text(x) for x in _items(d.get("warnings"))) if w),
            raw=dict(d),
        )
