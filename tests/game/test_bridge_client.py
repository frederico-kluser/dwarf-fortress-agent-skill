"""Testes do ``GameStateClient`` com um bridge FALSO (sem jogo, sem rede, sem subprocesso real).

O contrato testado é o do ``dfhack/df_bridge.py snapshot --json``: o runner injetado
devolve o JSON que a ponte devolveria, e o backend TCP usa um módulo de ponte falso.
"""
from __future__ import annotations

import json
import subprocess
from datetime import timezone
from types import SimpleNamespace

import pytest

from system_one_dwarf.game import bridge as game_bridge
from system_one_dwarf.game.bridge import DEFAULT_BRIDGE_PATH, GameStateClient
from system_one_dwarf.game.errors import BridgeError, GameNotRunning


class FakeRunner:
    """Runner injetado: regista o comando e devolve um resultado (ou levanta)."""

    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(list(cmd))
        if self.exc is not None:
            raise self.exc
        return self.result


def error_body(message, exit_code, hint=None):
    return json.dumps({"ok": False, "error": message, "hint": hint, "exit_code": exit_code})


class FakeBridgeError(Exception):
    """Mesma forma do ``df_bridge.BridgeError`` (mensagem + exit_code + hint)."""

    def __init__(self, msg, exit_code=None, hint=None):
        super().__init__(msg)
        self.exit_code = exit_code
        self.hint = hint


def tcp_client(payload=None, error=None, **kw):
    """Cliente no backend TCP + módulo de ponte falso (via injeção ``bridge_module``)."""
    module = SimpleNamespace()
    module.BridgeError = FakeBridgeError
    module.calls = []
    module.find_df_path = lambda p: ("/fake/Dwarf Fortress", "test")

    def collect_snapshot(args, df):
        module.calls.append((args, df))
        if error is not None:
            raise error
        return payload

    module.collect_snapshot = collect_snapshot
    return GameStateClient(backend="tcp", bridge_module=module, **kw), module


# ---------- construção do comando ----------
def test_command_matches_bridge_contract(proc_factory, busy_payload):
    runner = FakeRunner(proc_factory(busy_payload))
    client = GameStateClient(python="python3", bridge_path="/repo/dfhack/df_bridge.py",
                             df_path="/df", port=5000, via="tcp", radius=12, events=5,
                             runner=runner)
    state = client.snapshot()
    assert runner.calls == [[
        "python3", "/repo/dfhack/df_bridge.py", "snapshot", "--json",
        "--radius", "12", "--events", "5", "--df-path", "/df", "--port", "5000",
        "--via", "tcp"]]
    assert state.mode == "adventure"


def test_default_bridge_path_points_at_repo_bridge():
    assert DEFAULT_BRIDGE_PATH.name == "df_bridge.py"
    assert DEFAULT_BRIDGE_PATH.is_file(), DEFAULT_BRIDGE_PATH
    assert DEFAULT_BRIDGE_PATH.parent.name == "dfhack"


# ---------- parsing tipado ----------
def test_snapshot_parses_typed_structures(proc_factory, busy_payload):
    client = GameStateClient(runner=FakeRunner(proc_factory(busy_payload)))
    state = client.snapshot()

    assert state.schema_version == 1
    assert state.ts is not None and state.ts.utcoffset() == timezone.utc.utcoffset(None)
    assert state.ts.year == 2026 and state.ts.microsecond == 512000
    assert state.mode == "adventure"
    assert state.focus == "dungeonmode/Default"
    assert state.screen_text == "You are in a forest.\nConversation"
    assert state.warnings == ()

    adv = state.adventurer
    assert (adv.name, adv.race, adv.profession) == ("Urist McTest", "Dwarf", "Adventurer")
    assert adv.pos == (10, 20, 30) and adv.kills == 7

    health = state.metrics.health_injuries
    assert health.status == "bleeding" and health.is_bleeding and not health.is_healthy
    assert health.wound_count == 1 and health.hp_current is None
    assert health.wounds[0].body_part == "right upper arm"
    assert health.wounds[0].severity == 40 and health.wounds[0].bleeding is True
    assert health.status_conditions == ("winded", "bleeding")

    assert state.metrics.needs_state.hunger == "hungry"
    assert state.metrics.needs_state.stress_level == 5

    inv = state.metrics.inventory
    assert inv.item_count == 2 and inv.total_weight_kg == 4.5
    assert inv.capacity_kg is None
    assert inv.equipped_weapon == "iron sword"
    assert inv.equipped_armor == ("leather armor",)
    assert inv.items[0].weight_kg == 1.5 and inv.items[0].material == "iron"
    assert inv.find("armor")[0].name == "leather armor"

    threats = state.metrics.threats
    assert threats.count == 1 and threats.level == "medium"
    assert threats.nearest.name == "goblin" and threats.nearest.direction == "E"
    assert state.metrics.units_nearby.count == 1

    loc = state.metrics.location
    assert loc.pos == (10, 20, 30) and loc.surroundings == "outdoors, forest"
    assert loc.site == "Boatmurdered" and loc.travel_destination is None

    clock = state.metrics.game_clock
    assert clock.date_text == "Timber 27, year 100" and clock.season == "autumn"

    assert state.recent_events[0].category == "death"
    assert state.recent_events[0].game_date == "Granite 3, year 100"
    assert state.to_dict() == busy_payload          # JSON cru preservado


def test_missing_fields_do_not_crash(proc_factory, empty_payload):
    client = GameStateClient(runner=FakeRunner(proc_factory(empty_payload)))
    state = client.snapshot()

    assert state.adventurer is None
    assert state.focus is None and state.screen_text == ""
    assert state.gamelog_events == () and state.warnings == ()
    assert state.metrics.threats.count == 0 and state.metrics.threats.hostiles == ()
    assert state.metrics.inventory.items == () and state.metrics.inventory.total_weight_kg is None
    assert state.metrics.location.x is None
    assert state.metrics.game_clock.date_text is None
    assert state.metrics.events == ()
    assert state.is_alive is True                   # sem status != "dead"


def test_bridge_contract_payload_keeps_the_null_fields(proc_factory, busy_payload):
    """O JSON do contrato traz SEMPRE os campos que o Lua não sabe ler, a `null`."""
    hl = busy_payload["metrics"]["health_injuries"]
    inv = busy_payload["metrics"]["inventory"]
    loc = busy_payload["metrics"]["location"]
    for group, key in ((hl, "hp_current"), (hl, "hp_max"), (inv, "capacity_kg"),
                       (loc, "travel_destination")):
        assert key in group, key                 # presente, não ausente
        assert group[key] is None, key
    state = GameStateClient(runner=FakeRunner(proc_factory(busy_payload))).snapshot()
    assert state.metrics.health_injuries.hp_current is None
    assert state.metrics.health_injuries.hp_max is None
    assert state.metrics.inventory.capacity_kg is None
    assert state.metrics.location.travel_destination is None


def test_lua_payload_without_nil_keys_parses_to_none(proc_factory, lua_payload):
    """Payload cru do Lua (chaves `nil` ausentes) → lê-se `None`, sem rebentar."""
    state = GameStateClient(runner=FakeRunner(proc_factory(lua_payload))).snapshot()
    health, inv = state.metrics.health_injuries, state.metrics.inventory
    loc = state.metrics.location
    assert health.hp_current is None and health.hp_max is None
    assert inv.capacity_kg is None and loc.travel_destination is None
    assert health.status == "bleeding" and inv.item_count == 0
    assert state.metrics.threats.level == "none" and loc.site == "Boatmurdered"
    assert state.mode == "adventure" and state.is_alive is True


def test_unknown_extra_fields_are_tolerated(proc_factory, busy_payload):
    busy_payload["metrics"]["agent"] = {"decisions_per_min": 12.4}
    busy_payload["metrics"]["surprise"] = [1, 2, 3]
    busy_payload["adventurer"]["squad"] = "the bold"
    client = GameStateClient(runner=FakeRunner(proc_factory(busy_payload)))
    state = client.snapshot()
    assert state.metrics.raw["agent"]["decisions_per_min"] == 12.4
    assert state.adventurer.raw["squad"] == "the bold"


# ---------- erros ----------
def test_exit_12_is_game_not_running(proc_factory):
    proc = proc_factory(returncode=12,
                        stdout=error_body("conexão recusada em 127.0.0.1:5000", 12,
                                          hint="rode status"))
    client = GameStateClient(runner=FakeRunner(proc))
    with pytest.raises(GameNotRunning) as exc:
        client.snapshot()
    assert exc.value.exit_code == 12
    assert "conexão recusada" in str(exc.value)
    assert exc.value.hint == "rode status"


def test_exit_10_is_game_not_running(proc_factory):
    proc = proc_factory(returncode=10, stdout=error_body("Dwarf Fortress não encontrado", 10))
    client = GameStateClient(runner=FakeRunner(proc))
    with pytest.raises(GameNotRunning) as exc:
        client.snapshot()
    assert exc.value.exit_code == 10


def test_exit_13_is_bridge_error(proc_factory):
    proc = proc_factory(returncode=13,
                        stdout=error_body("o comando falhou no jogo (CR_NOT_FOUND)", 13,
                                          hint="esse comando não existe"))
    client = GameStateClient(runner=FakeRunner(proc))
    with pytest.raises(BridgeError) as exc:
        client.snapshot()
    assert not isinstance(exc.value, GameNotRunning)
    assert exc.value.exit_code == 13
    assert "CR_NOT_FOUND" in str(exc.value) and "esse comando não existe" in str(exc.value)


def test_exit_13_without_json_body_uses_stderr(proc_factory):
    proc = proc_factory(returncode=13, stdout="", stderr="boom\n")
    client = GameStateClient(runner=FakeRunner(proc))
    with pytest.raises(BridgeError) as exc:
        client.snapshot()
    assert "boom" in str(exc.value)
    assert exc.value.exit_code == 13


def test_invalid_json_is_bridge_error(proc_factory):
    proc = proc_factory(returncode=0, stdout="not json at all")
    client = GameStateClient(runner=FakeRunner(proc))
    with pytest.raises(BridgeError) as exc:
        client.snapshot()
    assert "invalid JSON" in str(exc.value)
    assert not isinstance(exc.value, GameNotRunning)


def test_schema_version_mismatch_is_bridge_error(proc_factory, busy_payload):
    busy_payload["schema_version"] = 99
    client = GameStateClient(runner=FakeRunner(proc_factory(busy_payload)))
    with pytest.raises(BridgeError) as exc:
        client.snapshot()
    assert "schema_version" in str(exc.value)


def test_missing_python_binary_is_game_not_running():
    runner = FakeRunner(exc=FileNotFoundError("no such interpreter"))
    client = GameStateClient(python="/nope/python3", runner=runner)
    with pytest.raises(GameNotRunning) as exc:
        client.snapshot()
    assert exc.value.exit_code == game_bridge.EXIT_UNREACHABLE


def test_timeout_is_game_not_running():
    runner = FakeRunner(exc=subprocess.TimeoutExpired(cmd="x", timeout=30))
    client = GameStateClient(timeout=30, runner=runner)
    with pytest.raises(GameNotRunning) as exc:
        client.snapshot()
    assert "timed out" in str(exc.value)


def test_default_runner_uses_subprocess(monkeypatch, proc_factory, busy_payload):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["kwargs"] = cmd, kwargs
        return proc_factory(busy_payload)

    monkeypatch.setattr(game_bridge, "subprocess",
                        SimpleNamespace(run=fake_run,
                                        TimeoutExpired=subprocess.TimeoutExpired))
    state = GameStateClient(df_path="/df").snapshot()
    assert state.metrics.threats.level == "medium"
    assert seen["cmd"][2] == "snapshot"
    assert seen["kwargs"]["cwd"] == str(game_bridge.REPO_ROOT)
    assert seen["kwargs"]["timeout"] == 30.0


# ---------- backend TCP (módulo de ponte falso) ----------
def test_tcp_backend_calls_collect_snapshot(busy_payload):
    client, module = tcp_client(payload=busy_payload)
    state = client.snapshot()
    assert state.adventurer.name == "Urist McTest"
    (args, df), = module.calls
    assert df == "/fake/Dwarf Fortress"
    assert args.via == "tcp" and args.radius == 25 and args.events == 20


def test_tcp_backend_maps_bridge_errors():
    _, module = tcp_client()
    error = module.BridgeError("conexão recusada em 127.0.0.1:5000", exit_code=12,
                               hint="o jogo está aberto?")
    client, _ = tcp_client(error=error)
    with pytest.raises(GameNotRunning) as exc:
        client.snapshot()
    assert exc.value.exit_code == 12 and exc.value.hint == "o jogo está aberto?"


def test_tcp_backend_command_error_is_bridge_error():
    _, module = tcp_client()
    error = module.BridgeError("o comando falhou no jogo (CR_NOT_FOUND)", exit_code=13)
    client, _ = tcp_client(error=error)
    with pytest.raises(BridgeError) as exc:
        client.snapshot()
    assert not isinstance(exc.value, GameNotRunning)
    assert exc.value.exit_code == 13


def test_tcp_backend_reports_found_df_path():
    client, module = tcp_client(error=None)
    module.collect_snapshot = lambda args, df: {"schema_version": 1, "mode": "adventure",
                                                "metrics": {}, "ts": None}
    state = client.snapshot()
    assert state.mode == "adventure" and state.ts is None


def test_bad_backend_name_is_rejected():
    with pytest.raises(ValueError):
        GameStateClient(backend="carrier-pigeon")


def test_module_level_snapshot_helper(proc_factory, busy_payload):
    client = GameStateClient(runner=FakeRunner(proc_factory(busy_payload)))
    assert game_bridge.snapshot(client).metrics.location.site == "Boatmurdered"
