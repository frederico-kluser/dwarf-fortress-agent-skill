"""Testes das estruturas tipadas (dataclasses) da camada de GAME STATE."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from system_one_dwarf.game.models import (
    METRIC_GROUPS,
    GameEvent,
    GameState,
    HealthInjuries,
    Inventory,
    Location,
    Metrics,
    Threats,
    VisibleUnit,
    parse_ts,
)


def test_metric_groups_are_the_eight_of_the_contract():
    assert len(METRIC_GROUPS) == 8
    assert len(set(METRIC_GROUPS)) == 8
    assert METRIC_GROUPS == ("health_injuries", "needs_state", "inventory", "threats",
                             "location", "game_clock", "units_nearby", "events")
    assert Metrics.GROUP_NAMES == METRIC_GROUPS


def test_metrics_group_lookup_and_aliases(busy_payload):
    metrics = Metrics.from_dict(busy_payload["metrics"])
    assert metrics.group("threats") is metrics.threats
    assert metrics.group("health_injuries") is metrics.health_injuries
    assert metrics.health is metrics.health_injuries       # apelido do painel de docs/api.md
    assert metrics.needs is metrics.needs_state
    with pytest.raises(KeyError):
        metrics.group("agent")                             # grupo de outra camada (backend)


def test_health_predicates():
    assert HealthInjuries.from_dict({"status": "healthy"}).is_healthy
    bleeding = HealthInjuries.from_dict({"status": "bleeding",
                                         "status_conditions": ["bleeding"]})
    assert bleeding.is_bleeding and not bleeding.is_healthy
    assert HealthInjuries.from_dict({"status": "unconscious"}).is_incapacitated
    assert HealthInjuries.from_dict({"status": "dead"}).is_incapacitated
    assert not HealthInjuries.from_dict({}).is_incapacitated


def test_health_wound_count_falls_back_to_list():
    health = HealthInjuries.from_dict({"wounds": [{"body_part": "head"}, {"bleeding": True}]})
    assert health.wound_count == 2
    assert health.wounds[0].body_part == "head"
    assert health.wounds[1].bleeding is True and health.wounds[1].severity is None


def test_threats_nearest_and_imminent():
    threats = Threats.from_dict({"hostiles": [
        {"name": "goblin", "distance": 7, "direction": "N", "hostile": True},
        {"name": "troll", "distance": 3, "direction": "S", "hostile": True},
    ]})
    assert threats.count == 2                      # count ausente → len(hostiles)
    assert threats.nearest.name == "troll"
    assert threats.imminent is True
    assert Threats.from_dict({"hostiles": [{"name": "deer", "distance": 20}]}).imminent is False
    assert Threats.from_dict({}).nearest is None


def test_visible_unit_visible_defaults_true():
    assert VisibleUnit.from_dict({"name": "deer"}).visible is True
    assert VisibleUnit.from_dict({"name": "deer", "visible": False}).visible is False


def test_inventory_find_and_defaults():
    inv = Inventory.from_dict({"items": [{"name": "iron sword"}, {"name": "Iron Helm"}]})
    assert inv.item_count == 2
    assert [i.name for i in inv.find("iron")] == ["iron sword", "Iron Helm"]
    empty = Inventory.from_dict({})
    assert empty.items == () and empty.total_weight_kg is None and empty.capacity_kg is None


def test_location_and_clock_defaults():
    loc = Location.from_dict({"x": 1, "y": 2})
    assert loc.pos == (1, 2, None)
    assert loc.travel_destination is None
    assert Location.from_dict({}).surroundings is None


def test_parse_ts_forms():
    ts = parse_ts("2026-09-27T19:44:03.512Z")
    assert ts == datetime(2026, 9, 27, 19, 44, 3, 512000, tzinfo=timezone.utc)
    assert parse_ts("2026-09-27T19:44:03+00:00").utcoffset() == timedelta(0)
    assert parse_ts("not a date") is None
    assert parse_ts(None) is None and parse_ts(12345) is None


def test_game_event_accepts_plain_string():
    event = GameEvent.from_dict("Urist has been struck down.")
    assert event.text == "Urist has been struck down." and event.category is None


def test_parsing_tolerates_wrong_types():
    metrics = Metrics.from_dict({"threats": "nope", "inventory": 7, "events": {"a": 1},
                                 "location": {"x": "dez"}})
    assert metrics.threats.count == 0 and metrics.inventory.items == ()
    assert metrics.events == () and metrics.location.x is None
    state = GameState.from_dict({"mode": 5, "screen_text": None, "warnings": ["a", None, 3],
                                "gamelog_events": [{"text": "x"}]})
    assert state.mode == "adventure" and state.screen_text == ""
    assert state.warnings == ("a",) and state.gamelog_events[0].text == "x"


def test_empty_state_and_recent_events_fallback():
    state = GameState.from_dict({})
    assert state.schema_version == 1 and state.adventurer is None
    assert state.metrics.threats.count == 0
    assert state.recent_events == ()                     # nada em gamelog nem em metrics
    with_metrics_events = GameState.from_dict(
        {"metrics": {"events": [{"text": "a", "category": "combat"}]}})
    assert with_metrics_events.recent_events[0].text == "a"


def test_state_alive_and_to_dict(busy_payload):
    state = GameState.from_dict(busy_payload)
    assert state.is_alive is True
    assert state.to_dict() == busy_payload
    assert GameState.from_dict({"metrics": {"health_injuries": {"status": "dead"}}}).is_alive \
        is False


def test_visible_unit_parses_kind_race_and_pos_para_a_caca():
    unit = VisibleUnit.from_dict({"name": "donkey", "distance": 2, "direction": "N",
                                  "race": "donkey", "kind": "animal",
                                  "pos": {"x": 88, "y": 70, "z": 0}})
    assert unit.kind == "animal" and unit.race == "donkey"
    assert (unit.x, unit.y, unit.z) == (88, 70, 0)
    # payloads antigos (sem pos/kind) continuam a parsear
    old = VisibleUnit.from_dict({"name": "deer", "distance": 3})
    assert old.kind is None and old.x is None
