"""Movimento como macro-ação no ``MacroExecutor`` (mudança 04, ARQUITETURA.md §8 #04).

A missão ``caca-2`` gastou 415 decisões JEV-A em micro-movimento (349 em
``abstain``): o JEV decide **destino + urgência**, o código caminha. Estes testes
fixam o contrato do corredor de rotas do :class:`MacroExecutor` e do gerador de
candidatos (:mod:`system_one_dwarf.agent.movement_actions`):

* **(a)** uma decisão = uma rota inteira, observável (``report``: destino,
  urgência, legs, veredictos por passo);
* **(b)** os guards existentes mandam: ameaça nova / focus / morte / abort da
  ponte ⇒ ``aborted`` com a razão ``guard:*``/``route:*`` e o controlo volta ao
  loop (nada mais é enviado ao jogo);
* **(c)** paragens: **3 passos sem mudança ⇒ exploração forçada** (sem aprovação);
  **2.ª ocorrência ⇒ re-plano** (``stuck:replan``, controlo devolvido);
* os gates ``--stop-on-threat``/``--stop-on-focus-change`` saem em **todos** os
  lotes e o DSL é validado pelo ``parse_batch`` REAL da ponte;
* sem marca de macro, o caminho clássico não muda.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import pytest

from system_one_dwarf.agent.executor import MacroExecutor
from system_one_dwarf.agent.movement import RoutePlan, route_summary
from system_one_dwarf.agent.movement_actions import apply_urgency, movement_actions
from system_one_dwarf.agent.stuck import StuckDetector
from system_one_dwarf.agent.types import LoopConfig, MacroAction

from .fakes_loop import BATCH_OK, FakeGame, FakeProc, game_state, hostile
from .test_executor import parse_batch

CONFIG = LoopConfig(advance_seconds=3.0)


# ------------------------------------------------------------------- fakes


@dataclass
class QueueRunner:
    """Runner da ponte: grava os ``argv`` e responde relatórios em fila (o último repete)."""

    reports: Sequence[tuple[int, Mapping[str, Any]]] = ()
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def __call__(self, argv: Sequence[str], timeout: float | None = None) -> FakeProc:
        argv = tuple(str(part) for part in argv)
        self.calls.append(argv)
        code, report = self.reports[len(self.calls) - 1] if len(self.calls) <= len(self.reports) \
            else (0, BATCH_OK)
        return FakeProc(code, json.dumps(dict(report)), "")

    def batch_calls(self) -> list[tuple[str, ...]]:
        return [argv for argv in self.calls if "batch" in argv]

    def dsl_of(self, index: int = 0) -> str:
        return self.batch_calls()[index][-1]


def snap(x: int = 50, y: int = 50, **kwargs: Any):
    base = dict(x=x, y=y, z=0, focus="dungeonmode/Default", blood_current=5000, blood_max=5000,
                surroundings="open savanna", region="Plains")
    base.update(kwargs)
    return game_state(**base)


def navigate_action(**params: Any) -> MacroAction:
    base = {"x": 80, "y": 50, "z": 0, "destination": "the ford", "macro": True,
            "urgency": "safe"}
    base.update(params)
    return MacroAction(
        id="navigate:the_ford", name_en="Travel to the ford",
        description_en="Travel to the ford and re-assess on arrival.",
        target="the ford", verb="navigate_to", category="objective", priority=41,
        params=base,
    )


def explore_action(**params: Any) -> MacroAction:
    base = {"direction": "e", "steps": 5, "macro": True, "urgency": "pressing"}
    base.update(params)
    return MacroAction(
        id="explore:savanna", name_en="Explore the surroundings",
        description_en="Walk the surroundings.", target="open savanna",
        verb="explore", category="objective", priority=100, params=base,
    )


def walk(action: MacroAction, states: Sequence[Any], *, urgency: Any = None,
         runner: QueueRunner | None = None, stuck: Any = None, **kwargs: Any):
    """Executa a macro-ação contra uma fila de snapshots; devolve ``(result, runner, game)``."""
    runner = runner or QueueRunner()
    game = FakeGame(*states)
    ex = MacroExecutor(game, kwargs.pop("config", CONFIG), runner=runner, stuck=stuck, **kwargs)
    return ex.execute(action, states[0], urgency=urgency), runner, game


# ============================================================ (a) macro-ação


def test_macro_navigate_walks_the_route_leg_by_leg_in_one_decision():
    # (50,50) → (80,50) em Safe (stride 10): 3 legs de 10 tiles
    states = [snap(50, 50), snap(60, 50), snap(60, 50), snap(70, 50), snap(70, 50), snap(80, 50)]
    result, runner, _game = walk(navigate_action(), states)

    assert result.status == "executed" and result.ok is True and result.reason == "arrived"
    dsls = [runner.dsl_of(i) for i in range(3)]
    assert dsls == ["goto 60,50 maxsteps=30", "goto 70,50 maxsteps=30", "goto 80,50 maxsteps=30"]
    for dsl in dsls:
        assert parse_batch(dsl)[0]["op"] == "goto"          # DSL de verdade, validado na ponte
    assert len(runner.batch_calls()) == 3                    # um lote por leg, nada mais
    assert result.steps_planned == 3 and result.steps_executed == 3
    assert result.executed_dsl == "; ".join(dsls)
    assert parse_batch(result.dsl)[0]["op"] == "goto"


def test_macro_decision_reports_destination_urgency_and_leg_verdicts():
    states = [snap(50, 50), snap(60, 50), snap(60, 50), snap(70, 50), snap(70, 50), snap(80, 50)]
    result, _runner, _game = walk(navigate_action(), states)
    report = result.report

    assert report["goal"] == "the ford" and report["urgency"] == "safe"
    assert report["route_summary"] == \
        "Travel ~30 tiles E to the ford in 3 legs, re-assessing between legs"
    assert report["arrived"] is True and report["truncated"] is False
    assert [entry["verdict"] for entry in report["attempts"]] == ["arrived"] * 3
    assert [entry["kind"] for entry in report["attempts"]] == ["goto"] * 3
    assert report["stuck_events"] == [] and report["forced_explorations"] == 0
    assert result.duration_ms >= 0 and result.errors == ()


def test_urgency_answer_shapes_the_route_budget():
    states = [snap(50, 50), snap(53, 50), snap(53, 50), snap(56, 50),
              snap(56, 50), snap(59, 50), snap(59, 50), snap(62, 50)]
    result, runner, _game = walk(navigate_action(urgency="safe"), states, urgency="Critical")
    # Critical: stride 3, max_legs 2 ⇒ (53,50) e (56,50) — o orçamento corta a viagem
    assert [runner.dsl_of(i) for i in range(2)] == ["goto 53,50 maxsteps=9", "goto 56,50 maxsteps=9"]
    assert result.report["urgency"] == "critical" and result.report["truncated"] is True
    assert result.status == "executed" and result.reason == "route:completed"
    assert result.report["arrived"] is False and result.steps_executed == 2


def test_macro_candidates_carry_destination_urgency_and_a_route_preview():
    state = game_state(
        x=50, y=50, z=0, surroundings="a road runs north", region="The Hills",
        hostiles=[hostile("goblin skulker", 3, "NE")],
        landmarks=[{"name": "ford", "x": 80, "y": 52, "z": 0}],
        travel_destination="Boatmurdered",
        hunger="none", thirst="none", sleep="rested", hour=12.0,
    )
    actions = movement_actions(state, objective="find an animal and kill it", step=7)
    assert [a.verb for a in actions].count("flee") == 1       # sobrevivência primeiro
    assert [a.category for a in actions][0] == "threat"

    ford = next(a for a in actions if a.id == "navigate:ford")
    assert ford.params["macro"] is True and ford.params["urgency"] == "pressing"
    plan = RoutePlan.from_params(ford.params)
    assert plan is not None and plan.goal_name == "ford"
    assert ford.description_en.startswith(route_summary(plan))   # destino+urgência observáveis
    assert "Travel ~" in ford.description_en

    named = next(a for a in actions if a.id == "navigate:boatmurdered")
    assert named.params["macro"] is True and "route" not in named.params  # sem coords: sem rota

    explore = next(a for a in actions if a.verb == "explore")
    assert explore.params["direction"] == "n"                  # "a road runs north"
    assert RoutePlan.from_params(explore.params) is not None

    # determinismo: mesmo estado ⇒ mesmos candidatos, na mesma ordem
    assert list(movement_actions(state, objective="find an animal and kill it", step=7)) == list(actions)


def test_apply_urgency_rebuilds_the_preview_with_the_decided_budget():
    state = game_state(x=50, y=50, z=0, landmarks=[{"name": "ford", "x": 80, "y": 52, "z": 0}])
    ford = next(a for a in movement_actions(state) if a.id == "navigate:ford")
    pressing = RoutePlan.from_params(ford.params)
    critical = apply_urgency(ford, 2)                          # índice da régua → Critical
    assert critical.params["urgency"] == "critical"
    plan = RoutePlan.from_params(critical.params)
    assert plan is not None and plan.stride == 3 < pressing.stride
    assert ford.params["urgency"] == "pressing"                # a original não muda (frozen)
    assert apply_urgency(ford, "Safe").params["urgency"] == "safe"
    assert apply_urgency(ford, None).params["urgency"] == "pressing"


# ========================================================== (b) guards mandam


def test_route_aborts_when_a_threat_appears_before_a_leg_and_returns_control():
    decision = snap(50, 50)
    states = [decision, snap(60, 50), snap(60, 50, hostiles=[hostile("troll", 8, "N")])]
    result, runner, _game = walk(navigate_action(), states)

    assert result.status == "aborted" and result.ok is False
    assert result.reason == "guard:threat_appeared"
    assert len(runner.batch_calls()) == 1     # leg 1 fez-se; o leg 2 nem saiu (controlo devolvido)
    assert result.steps_executed == 1
    assert result.report["attempts"][-1]["verdict"] == "abort"


def test_route_aborts_when_a_threat_appears_during_a_leg():
    decision = snap(50, 50)
    states = [decision, snap(60, 50), snap(60, 50),
              snap(70, 50, hostiles=[hostile("troll", 8, "N")])]
    result, runner, _game = walk(navigate_action(), states)

    assert result.status == "aborted" and result.reason == "guard:threat_appeared"
    assert len(runner.batch_calls()) == 2     # o leg correu; a ameaça nova abortou a rota
    assert result.report["attempts"][-1]["verdict"] == "abort"
    assert result.report["attempts"][-1]["reason"] == "new_hostile"   # vocabulário do veredicto


@pytest.mark.parametrize("after,reason", [
    (snap(70, 50, focus="dungeonmode/Help"), "guard:focus_changed"),
    (snap(70, 50, status="dead", wounds=[{"body_part": "head"}]), "guard:adventurer_dead"),
    (snap(70, 50, blood_current=3000), "route:blood_drop"),
])
def test_route_aborts_on_every_guard_between_legs(after, reason):
    states = [snap(50, 50), snap(60, 50), snap(60, 50), after]
    result, _runner, _game = walk(navigate_action(), states)
    assert result.status == "aborted" and result.reason == reason
    assert result.report["attempts"][-1]["verdict"] == "abort"


def test_route_aborts_when_a_threat_turns_into_great_danger():
    seen = hostile("bronze colossus", 9, "N")
    worse = hostile("bronze colossus", 9, "N", great_danger=True)
    states = [snap(50, 50, hostiles=[seen]), snap(60, 50, hostiles=[seen]),
              snap(60, 50, hostiles=[seen]), snap(70, 50, hostiles=[worse])]
    result, _runner, _game = walk(navigate_action(), states)
    assert result.status == "aborted" and result.reason == "guard:great_danger_appeared"


def test_bridge_stop_on_threat_mid_route_stops_and_returns_control():
    stopped = {"ok": False, "aborted_at": 0, "reason": "threat",
               "summary": {"total": 1, "executed": 1, "ok": 1, "failed": 0}, "steps": []}
    runner = QueueRunner(reports=[(0, BATCH_OK), (13, stopped)])
    states = [snap(50, 50), snap(60, 50), snap(60, 50), snap(70, 50)]
    result, runner, _game = walk(navigate_action(), states, runner=runner)

    assert result.status == "aborted" and result.reason == "threat"   # a ponte (--stop-on-threat)
    assert len(runner.batch_calls()) == 2 and result.steps_executed == 1


def test_every_route_batch_keeps_the_safety_gates():
    states = [snap(50, 50), snap(60, 50), snap(60, 50), snap(70, 50), snap(70, 50), snap(80, 50)]
    _result, runner, _game = walk(navigate_action(), states)
    for argv in runner.batch_calls():
        assert "--stop-on-threat" in argv and "--stop-on-focus-change" in argv
        assert "--allow-travel-barrier" not in argv
        assert argv[2:4] == ("act", "batch")


def test_without_the_macro_marker_the_classic_path_is_unchanged():
    classic = MacroAction(                          # candidato clássico, sem marca de macro
        id="explore:savanna", name_en="Explore the surroundings",
        description_en="Walk the surroundings.", target="open savanna",
        verb="explore", category="objective", priority=100,
        params={"direction": "n", "steps": 3},
    )
    result, runner, _game = walk(classic, [snap(50, 50), snap(50, 47)])

    assert result.status == "executed"
    assert result.executed_dsl == "move n times=3; read screen"
    assert len(runner.batch_calls()) == 1                       # um lote, como sempre


def test_macro_navigate_without_coordinates_stays_on_the_classic_plan():
    named = MacroAction(id="navigate:the_ford", name_en="Travel", description_en="Go.",
                        target="the ford", verb="navigate_to", category="objective",
                        params={"destination": "the ford", "macro": True})
    result, _runner, _game = walk(named, [snap(50, 50), snap(50, 50)])
    assert result.status == "executed" and result.needs_manual_mapping is True
    assert any("no coordinates" in note for note in result.manual_notes)


# ============================================ (c) stuck: exploração e re-plano


def test_three_stuck_steps_force_exploration_then_the_second_round_replans():
    """3 passos sem mudança ⇒ exploração forçada; 2.ª ocorrência ⇒ re-plano."""
    frozen = [snap(50, 50) for _ in range(16)]                  # o mundo não anda
    result, runner, _game = walk(explore_action(), frozen)

    assert result.status == "aborted" and result.reason == "stuck:replan"
    dsls = [runner.dsl_of(i) for i in range(len(runner.batch_calls()))]
    # 3 passos bloqueados (goto, plano B, goto) → exploração forçada, sem aprovação
    assert dsls[3] == "move n times=6"                          # rumo ainda não tentado
    # …3 mais → 2.ª ocorrência → re-plano: o controlo volta ao loop
    assert result.report["forced_explorations"] == 1
    assert [event["escalation"] for event in result.report["stuck_events"]] == [
        "force_explore", "replan"]
    assert result.report["stuck_events"][0]["repeat"] == 3      # 3 passos sem mudança
    assert all(entry["verdict"] == "blocked" for entry in result.report["attempts"][:3])
    assert result.report["legs_executed"] == 0
    # nada mais é enviado depois do re-plano
    assert len(runner.batch_calls()) == 6


def test_forced_exploration_breaks_the_freeze_and_the_route_resumes():
    states = [
        snap(50, 50), snap(50, 50),        # leg 0: goto bloqueado
        snap(50, 50), snap(50, 50),        # leg 0: plano B bloqueado
        snap(50, 50), snap(50, 50),        # leg 1: goto bloqueado → force_explore
        snap(50, 50), snap(53, 50),        # exploração forçada anda!
        snap(53, 50), snap(62, 50),        # leg 1: plano B chega ao waypoint
        snap(62, 50), snap(68, 50),        # leg 2
        snap(68, 50), snap(74, 50),        # leg 3
    ]
    result, runner, _game = walk(explore_action(), states)

    assert result.status == "executed" and result.reason == "arrived"
    assert result.report["forced_explorations"] == 1
    assert [event["escalation"] for event in result.report["stuck_events"]] == ["force_explore"]
    assert runner.dsl_of(3) == "move n times=6"
    assert result.report["legs_executed"] == 3
    assert result.report["arrived"] is True


def test_damage_during_the_route_returns_control_for_retreat():
    hurt = snap(70, 50, status="injured", wounds=[{"body_part": "leg"}], blood_current=3500)
    states = [snap(50, 50), snap(60, 50), snap(60, 50), hurt]
    result, _runner, _game = walk(navigate_action(), states)
    assert result.status == "aborted" and result.reason in ("route:blood_drop", "route:damage")


def test_route_without_a_game_observing_still_runs_one_gated_batch():
    action = navigate_action()
    ex = MacroExecutor(None, CONFIG, runner=QueueRunner(), guard=False)
    result = ex.execute(action, snap(50, 50))
    assert result.status == "executed"
    assert parse_batch(result.executed_dsl)[0]["op"] == "goto"   # rota inteira numa leva
    assert "--stop-on-threat" in ex.commands[0]


def test_resolve_route_is_strict_and_never_builds_junk_dsl():
    ex = MacroExecutor(FakeGame(snap(50, 50)), CONFIG, runner=QueueRunner())
    injected = navigate_action(x="80; key A_TALK", y=50)
    assert ex._resolve_route(injected, snap(50, 50)) is None     # lixo ⇒ caminho clássico
    assert ex.execute(navigate_action(x=10**9, y=50), snap(50, 50)).status == "refused"
    assert ex.execute(navigate_action(x=80.7, y=50), snap(50, 50)).reason == "invalid_plan"


def test_executor_uses_its_own_stuck_detector_by_default():
    ex = MacroExecutor(FakeGame(snap(50, 50)), CONFIG, runner=QueueRunner())
    assert isinstance(ex.stuck, StuckDetector)
    shared = StuckDetector(pattern_sink=False)
    other = MacroExecutor(FakeGame(snap(50, 50)), CONFIG, runner=QueueRunner(), stuck=shared)
    assert other.stuck is shared
