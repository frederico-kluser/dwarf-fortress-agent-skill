"""Configuração dos testes da camada de GAME STATE.

Sem jogo e sem rede: tudo corre sobre um bridge falso (runner injetado / módulo falso).
`src/` entra no `sys.path` porque o pacote é src-layout e pode não estar instalado.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Payload do CONTRATO: é isto que o `df_bridge.py snapshot --json` imprime (já
# normalizado — todos os campos do grupo presentes, `null` onde o Lua não deu valor).
BUSY_PAYLOAD = {
    "schema_version": 1,
    "ts": "2026-09-27T19:44:03.512Z",
    "mode": "adventure",
    "adventurer": {
        "name": "Urist McTest", "race": "Dwarf", "profession": "Adventurer",
        "pos": {"x": 10, "y": 20, "z": 30},
        "health": {"blood": 4000, "blood_max": 5000, "wounds": 1, "pain": 12},
        "needs": {"hunger_timer": 80000, "hungry": True, "thirst_timer": 30000,
                  "sleepiness_timer": 60000, "drowsy": True},
        "kills": 7, "stress_category": 5,
    },
    "metrics": {
        "health_injuries": {
            "hp_current": None, "hp_max": None, "blood_current": 4000, "blood_max": 5000,
            "wound_count": 1,
            "wounds": [{"body_part": "right upper arm", "severity": 40, "bleeding": True,
                        "severed": False, "pain": 5}],
            "pain": 12, "status": "bleeding", "status_conditions": ["winded", "bleeding"],
        },
        "needs_state": {"hunger": "hungry", "thirst": "thirsty", "sleep": "drowsy",
                        "hunger_timer": 80000, "thirst_timer": 30000,
                        "sleepiness_timer": 60000, "stress_level": 5},
        "inventory": {
            "items": [{"name": "iron sword", "quantity": 1, "weight_kg": 1.5,
                       "equipped": True, "slot": "Weapon", "material": "iron"},
                      {"name": "leather armor", "quantity": 1, "weight_kg": 3.0,
                       "equipped": True, "slot": "Worn", "material": "leather"}],
            "item_count": 2, "total_weight_kg": 4.5, "capacity_kg": None,
            "equipped_weapon": "iron sword", "equipped_armor": ["leather armor"],
        },
        "threats": {"count": 1, "level": "medium",
                    "hostiles": [{"name": "goblin", "distance": 5, "direction": "E",
                                  "hostile": True, "visible": True, "great_danger": False}]},
        "location": {"x": 10, "y": 20, "z": 30, "world": "The World of Tests",
                     "region": "forest", "site": "Boatmurdered", "site_type": "DarkFortress",
                     "surroundings": "outdoors, forest", "tile": "Grass",
                     "travel_destination": None},
        "game_clock": {"date_text": "Timber 27, year 100", "year": 100, "month": "Timber",
                       "day": 27, "season": "autumn", "tick": 300000, "hour": 0},
        "units_nearby": {"count": 1,
                         "units": [{"name": "goblin", "distance": 5, "direction": "E",
                                    "hostile": True, "visible": True}]},
        "events": [{"text": "The goblin attacks the adventurer!", "category": "combat",
                    "game_date": None}],
    },
    "screen_text": "You are in a forest.\nConversation",
    "focus": "dungeonmode/Default",
    "gamelog_events": [{"text": "(100) Granite 3, year 100: Urist has been struck down.",
                        "category": "death", "game_date": "Granite 3, year 100"}],
    "warnings": [],
}


@pytest.fixture
def busy_payload() -> dict:
    """Payload completo do contrato (cópia profunda — cada teste pode mexer à vontade)."""
    return copy.deepcopy(BUSY_PAYLOAD)


# Payload como o Lua REALMENTE o emite: em Lua `nil` desaparece da tabela, logo as
# chaves que o script não consegue ler (hp_current/hp_max, capacity_kg,
# travel_destination) NÃO EXISTEM no JSON do jogo. Quem as tem de repor como `null`
# é o `df_bridge.py` (`normalize_metrics`); a camada Python tolera a ausência.
LUA_PAYLOAD = {
    "mode": "adventure",
    "adventurer": {"name": "Urist McTest", "pos": {"x": 10, "y": 20, "z": 30}},
    "metrics": {
        "health_injuries": {"blood_current": 4000, "blood_max": 5000, "wound_count": 1,
                            "wounds": [], "pain": 12, "status": "bleeding",
                            "status_conditions": []},
        "needs_state": {"hunger": "hungry", "thirst": "thirsty", "sleep": "drowsy",
                        "stress_level": 5},
        "inventory": {"items": [], "item_count": 0, "total_weight_kg": 0.0,
                      "equipped_weapon": None, "equipped_armor": []},
        "threats": {"count": 0, "level": "none", "hostiles": []},
        "location": {"x": 10, "y": 20, "z": 30, "region": "forest",
                     "site": "Boatmurdered", "surroundings": "outdoors, forest"},
        "game_clock": {"date_text": "Timber 27, year 100", "year": 100, "month": "Timber",
                       "day": 27, "season": "autumn"},
        "units_nearby": {"count": 0, "units": []},
        "events": [],
    },
}


@pytest.fixture
def lua_payload() -> dict:
    """Payload cru do Lua (sem as chaves nil) — para provar que a ausência não parte."""
    return copy.deepcopy(LUA_PAYLOAD)


@pytest.fixture
def empty_payload() -> dict:
    """Payload mínimo: só o envelope (o jogo não devolveu métricas)."""
    return {"schema_version": 1, "ts": "2026-09-27T19:44:03.512Z", "mode": "adventure"}


@pytest.fixture
def proc_factory():
    """Constrói objetos tipo `subprocess.CompletedProcess` para o runner falso."""

    def make(payload=None, returncode=0, stderr="", stdout=None):
        text = stdout if stdout is not None else ("" if payload is None
                                                  else json.dumps(payload))
        return SimpleNamespace(returncode=returncode, stdout=text, stderr=stderr)

    return make
