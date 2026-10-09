"""Camada de GAME STATE — snapshot estruturado do jogo (adventure mode), em inglês.

Ponto de entrada::

    from system_one_dwarf.game import GameStateClient

    state = GameStateClient().snapshot()      # GameState tipado
    state.metrics.threats.level               # "none" | "low" | "medium" | "high"
    state.metrics.health_injuries.status      # "healthy" | "wounded" | "bleeding" | ...

Contrato: ``dfhack/df_bridge.py snapshot --json`` →
``{schema_version, ts, mode, adventurer, metrics{8 grupos}, screen_text, focus,
gamelog_events}`` (ver :data:`~.models.METRIC_GROUPS`).
"""
from .bridge import DEFAULT_BRIDGE_PATH, REPO_ROOT, GameStateClient, import_bridge_module, snapshot
from .errors import BridgeError, GameNotRunning, GameStateError
from .models import (
    METRIC_GROUPS,
    Adventurer,
    GameClock,
    GameEvent,
    GameState,
    HealthInjuries,
    Inventory,
    InventoryItem,
    Location,
    Metrics,
    NeedsState,
    Threats,
    UnitsNearby,
    VisibleUnit,
    Wound,
)

__all__ = [
    "METRIC_GROUPS", "Adventurer", "BridgeError", "DEFAULT_BRIDGE_PATH", "GameClock",
    "GameEvent", "GameNotRunning", "GameState", "GameStateClient", "GameStateError",
    "HealthInjuries", "Inventory", "InventoryItem", "Location", "Metrics", "NeedsState",
    "REPO_ROOT", "Threats", "UnitsNearby", "VisibleUnit", "Wound", "import_bridge_module",
    "snapshot",
]
