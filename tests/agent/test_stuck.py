"""``StuckDetector``: deteção de stuck/loop/morte/dano e a escada de escalação.

A personagem ficou congelada durante uma corrida inteira sem nada o detetar —
estes testes são a regressão dessa falha: o hash do estado (posição + arredores +
unidades próximas) tem de se manter SEM progresso para o detector falar, a
escada tem de forçar movimento (exploração em rumo não tentado) e depois pedir
re-plano, e o loop de ações idênticas tem de quebrar com outro candidato
(nunca repetir a ação que falhou 3×). Morte/dano → parar/retirar.

Integração com o loop: ``stuck_alert`` publicado, ação forçada EXECUTADA mesmo
em ``checkpoint`` (anti-congelamento) e ações bloqueadas filtradas dos
candidatos. Cada evento também fica na biblioteca de padrões.
"""
from __future__ import annotations

import json

from system_one_dwarf.agent.stuck import (
    StuckDetector,
    StuckEvent,
    append_pattern_event,
    health_fingerprint,
    state_fingerprint,
)
from system_one_dwarf.agent.types import LoopConfig, MODE_CHECKPOINT, STATUS_EXECUTED, STATUS_KILLED

from .fakes_loop import game_state
from .test_loop import Rig, world


# ------------------------------------------------------------- impressões digitais


def test_fingerprint_is_stable_for_the_same_state():
    state = world(x=10, y=20, z=3)
    assert state_fingerprint(state) == state_fingerprint(state)


def test_fingerprint_changes_when_the_position_changes():
    assert state_fingerprint(world(x=10, y=20)) != state_fingerprint(world(x=11, y=20))


def test_fingerprint_changes_when_the_surroundings_change():
    assert state_fingerprint(world(surroundings="a road")) != \
        state_fingerprint(world(surroundings="dense forest"))


def test_fingerprint_changes_when_nearby_units_change():
    with_unit = world(units=[{"name": "Buck Rabbit", "distance": 3, "direction": "N",
                              "pos": {"x": 1, "y": 2, "z": 0}}])
    without = world()
    assert state_fingerprint(with_unit) != state_fingerprint(without)


def test_fingerprint_ignores_screen_churn():
    a = world(screen_text="Time: 12:00", x=5, y=5)
    b = world(screen_text="Time: 12:01", x=5, y=5)
    assert state_fingerprint(a) == state_fingerprint(b)


def test_health_fingerprint_worsens_with_wounds():
    healthy = health_fingerprint(world(status="healthy"))
    hurt = health_fingerprint(world(status="injured", wounds=[{"body_part": "arm"}]))
    assert hurt > healthy


# ------------------------------------------------------------------ detetor


def _detector(**kw):
    kw.setdefault("pattern_sink", False)
    return StuckDetector(**kw)


def test_stuck_after_three_unchanged_validations_forces_exploration():
    det = _detector()
    state = world()
    assert det.observe(state) is None
    assert det.observe(state) is None
    event = det.observe(state)
    assert event is not None
    assert event.kind == "stuck"
    assert event.escalation == "force_explore"
    assert event.direction in ("n", "e", "s", "w", "ne", "nw", "se", "sw")
    assert event.repeat == 3


def test_stuck_escalates_to_replan_when_still_stuck():
    det = _detector()
    state = world()
    first = second = None
    for _ in range(6):
        event = det.observe(state)
        if event is not None and first is None:
            first = event
        elif event is not None and second is None:
            second = event
    assert first is not None and first.escalation == "force_explore"
    assert second is not None and second.escalation == "replan"
    assert first.direction != second.direction or second.direction is None


def test_progress_resets_the_stuck_ladder():
    det = _detector()
    det.observe(world(x=1, y=1))
    det.observe(world(x=1, y=1))
    # progresso: hash muda → a contagem recomeça (senão o evento já aqui saía)
    assert det.observe(world(x=2, y=1)) is None
    assert det.observe(world(x=2, y=1)) is None
    event = det.observe(world(x=2, y=1))
    assert event is not None and event.kind == "stuck"
    assert event.stuck_round == 1  # escada recomeçou do zero


def test_action_loop_breaks_and_blocks_the_repeated_action():
    det = _detector(action_loop_after=3, stuck_after=99)
    state = world()
    det.observe(state, action_id="attack:rabbit")
    det.observe(state, action_id="attack:rabbit")
    event = det.observe(state, action_id="attack:rabbit")
    assert event is not None
    assert event.kind == "action_loop"
    assert event.escalation == "break_loop"
    assert event.blocked_action_id == "attack:rabbit"
    assert det.should_block("attack:rabbit")
    assert not det.should_block("attack:mule")
    assert "attack:rabbit" in det.blocked_actions()


def test_different_actions_never_trip_the_action_loop():
    det = _detector(action_loop_after=3, stuck_after=99)
    state = world()
    for i in range(6):
        assert det.observe(state, action_id=f"explore:{'nesw'[i % 4]}") is None


def test_damage_event_requests_retreat():
    det = _detector()
    det.observe(world(status="healthy"))
    event = det.observe(world(status="injured", wounds=[{"body_part": "leg"}]))
    assert event is not None
    assert event.kind == "damage"
    assert event.escalation == "retreat"


def test_death_stops_and_reports_once():
    det = _detector()
    dead = game_state(status="dead", wounds=[{"body_part": "head"}])
    first = det.observe(dead)
    assert first is not None
    assert first.kind == "death"
    assert first.escalation == "stop"
    assert det.observe(dead) is None  # sem spam: a morte reporta-se uma vez


def test_unvisited_direction_cycles_through_all_eight():
    det = _detector()
    seen = [det.unvisited_direction() for _ in range(8)]
    assert len(set(seen)) == 8
    assert det.unvisited_direction() == "n"  # esgotou → recomeça


def test_reset_preserves_blocked_actions():
    det = _detector(action_loop_after=1, stuck_after=99)
    det.observe(world(), action_id="attack:rabbit")
    det.observe(world(), action_id="attack:rabbit")
    assert det.should_block("attack:rabbit")
    det.reset()
    assert det.should_block("attack:rabbit")


# ------------------------------------------------------- biblioteca de padrões


def test_append_pattern_event_writes_to_the_library(tmp_path):
    library = tmp_path / "padroes-navegacao.json"
    library.write_text(json.dumps({"version": 1, "patterns": []}), encoding="utf-8")
    event = StuckEvent("stuck", "force_explore", "frozen", direction="n", repeat=3)
    assert append_pattern_event(library, event) is True
    data = json.loads(library.read_text(encoding="utf-8"))
    assert data["stuck_events"][-1]["kind"] == "stuck"
    assert data["stuck_events"][-1]["direction"] == "n"
    assert "patterns" in data  # nada é apagado


def test_detector_logs_every_event_to_the_library(tmp_path):
    library = tmp_path / "padroes-navegacao.json"
    library.write_text(json.dumps({"version": 1}), encoding="utf-8")
    det = StuckDetector(pattern_sink=str(library))
    state = world()
    det.observe(state)
    det.observe(state)
    det.observe(state)  # stuck → force_explore
    data = json.loads(library.read_text(encoding="utf-8"))
    kinds = [entry["kind"] for entry in data["stuck_events"]]
    assert "stuck" in kinds


def test_append_pattern_event_never_raises_on_bad_library(tmp_path):
    library = tmp_path / "quebrado.json"
    library.write_text("{not json", encoding="utf-8")
    event = StuckEvent("stuck", "replan", "x")
    assert append_pattern_event(library, event) is False


# ---------------------------------------------------------- integração loop


def test_loop_publishes_stuck_alert_and_forces_movement_in_checkpoint():
    """O congelamento é quebrado MESMO sem aprovação humana (checkpoint)."""
    rig = Rig(states=[world() for _ in range(6)],
              config=LoopConfig(screen_translation_async=False, advance_on_execute=True),
              control={"mode": MODE_CHECKPOINT, "kill_switch": False},
              stuck=StuckDetector(stuck_after=2, action_loop_after=99, pattern_sink=False))
    result = None
    for _ in range(2):  # N=2 validações iguais → stuck já na 2.ª
        result = rig.step()
    alerts = rig.sink.of("stuck_alert")
    assert alerts, "o loop tem de publicar stuck_alert"
    assert alerts[0]["event"]["kind"] == "stuck"
    assert result is not None
    assert result.status == STATUS_EXECUTED
    assert result.action is not None and result.action.verb == "explore"
    assert any(action_id.startswith("explore:") for action_id in rig.executed_ids())


def test_loop_never_repeats_a_blocked_action():
    rig = Rig(states=[world() for _ in range(8)],
              config=LoopConfig(screen_translation_async=False, advance_on_execute=True),
              stuck=StuckDetector(stuck_after=99, action_loop_after=3, pattern_sink=False))
    for _ in range(4):
        rig.step()
    assert rig.loop.stuck.should_block("attack:goblin_skulker")
    seen_before = len(rig.sink.of("action_executed"))
    rig.step()
    new_executions = rig.sink.of("action_executed")[seen_before:]
    assert all(entry["action_id"] != "attack:goblin_skulker" for entry in new_executions)


def test_loop_calls_the_llm_replan_on_the_second_stuck_round():
    calls = []

    def replan(state, event):
        calls.append(event)
        return "hunt north: the river bank has animal tracks"

    rig = Rig(states=[world() for _ in range(8)],
              config=LoopConfig(screen_translation_async=False, advance_on_execute=True),
              control={"mode": MODE_CHECKPOINT, "kill_switch": False},
              stuck=StuckDetector(stuck_after=1, action_loop_after=99, pattern_sink=False))
    rig.loop.replan_fn = replan
    for _ in range(3):
        rig.step()
    assert calls, "a escalada replan tem de chamar o LLM (replan_fn)"
    assert calls[0].escalation == "replan"
    published = rig.sink.of("replan")
    assert published and "hunt north" in published[0]["hint"]


def test_loop_stops_on_death():
    dead = game_state(status="dead", wounds=[{"body_part": "head"}], focus="dungeonmode")
    rig = Rig(states=[dead],
              config=LoopConfig(screen_translation_async=False),
              stuck=StuckDetector(pattern_sink=False))
    result = rig.step()
    assert result.status == STATUS_KILLED
    assert rig.sink.of("stuck_alert")[0]["event"]["kind"] == "death"


def test_loop_forces_retreat_when_damaged():
    hurt = world(status="injured", wounds=[{"body_part": "leg"}], x=4, y=4)
    rig = Rig(states=[world(x=4, y=4), hurt],
              config=LoopConfig(screen_translation_async=False, advance_on_execute=True),
              stuck=StuckDetector(stuck_after=99, action_loop_after=99, pattern_sink=False))
    rig.step()
    result = rig.step()
    alerts = rig.sink.of("stuck_alert")
    assert alerts and alerts[0]["event"]["kind"] == "damage"
    assert result.action is not None and result.action.verb in ("flee", "explore")
