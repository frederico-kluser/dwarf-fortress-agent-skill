"""Executor: plano DSL, gates de segurança, guard e controlo de tempo.

O executor é a única peça que escreve no jogo, por isso o teste aperta os três
contratos que interessam: (a) todo o ``act batch`` sai com
``--stop-on-threat``/``--stop-on-focus-change``; (b) o plano é **DSL de verdade**
— validado pelo ``parse_batch`` do próprio ``dfhack/df_bridge.py`` — e só os
passos verificados são executados; (c) quando a situação mudou (focus, ameaça
nova, ameaça colada, aventureiro morto) o plano é recusado com ``aborted`` e
nada é enviado ao jogo.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

from system_one_dwarf.agent.executor import DEFAULT_BRIDGE_PATH, MacroExecutor
from system_one_dwarf.agent.types import LoopConfig, MacroAction

from .fakes_loop import FakeBridgeRunner, FakeGame, game_state, hostile

CONFIG = LoopConfig(advance_seconds=3.0)


def bridge_module():
    """O ``df_bridge.py`` real (só para validar o DSL). ``None`` se não existir."""
    path = pathlib.Path(DEFAULT_BRIDGE_PATH)
    if not path.is_file():
        return None
    name = "sod_test_df_bridge"
    import sys

    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def parse_batch(dsl: str):
    bridge = bridge_module()
    if bridge is None:  # pragma: no cover - o repo tem a ponte
        pytest.skip("dfhack/df_bridge.py not available")
    return bridge.parse_batch(dsl)


def attack_action(**params) -> MacroAction:
    base = {"direction": "ne", "distance": 6.0, "steps": 5, "great_danger": False}
    base.update(params)
    return MacroAction(
        id="attack:goblin_skulker", name_en="Attack goblin skulker",
        description_en="Close on the skulker and attack.", target="goblin skulker",
        verb="attack", category="threat", priority=20, params=base,
        needs_manual_mapping=True,
    )


def executor(**kwargs) -> tuple[MacroExecutor, FakeBridgeRunner]:
    runner = kwargs.pop("runner", None) or FakeBridgeRunner()
    game = kwargs.pop("game", None) or FakeGame(game_state())
    return MacroExecutor(game, kwargs.pop("config", CONFIG), runner=runner, **kwargs), runner


# ------------------------------------------------------------------- execução


def test_attack_executes_movement_dsl_with_safety_gates():
    state = game_state(hostiles=[hostile("goblin skulker", 6, "NE")], focus="Adventure")
    ex, runner = executor(game=FakeGame(state))
    result = ex.execute(attack_action(), state)

    assert result.status == "executed" and result.ok is True
    assert runner.batch_calls(), "o lote tem de ter sido enviado"
    argv = runner.batch_calls()[0]
    assert "--stop-on-threat" in argv
    assert "--stop-on-focus-change" in argv
    assert "--allow-travel-barrier" not in argv      # a barreira de viagem fica ligada
    assert argv[1].endswith("df_bridge.py") and argv[2:4] == ("act", "batch")

    # o plano verificável: aproximar + ler o ecrã (as teclas de ataque não entram)
    assert result.executed_dsl == "move ne times=5; read screen"
    assert result.needs_manual_mapping is True
    assert any("attack" in note for note in result.manual_notes)
    assert "# MANUAL:" in result.dsl                    # plano completo, com notas
    assert parse_batch(result.executed_dsl) == [
        {"op": "move", "dir": "ne", "times": 5},
        {"op": "read", "what": "screen"},
    ]
    assert parse_batch(result.dsl) == parse_batch(result.executed_dsl)  # comentários não contam


def test_execute_rejects_non_macro_action():
    ex, _runner = executor()
    with pytest.raises(TypeError):
        ex.execute("attack", game_state())


def test_plan_for_every_vocabulary_verb_is_valid_dsl():
    actions = [
        attack_action(),
        MacroAction(id="flee:s", name_en="Flee south", description_en="Run away.",
                    verb="flee", category="survival", params={"direction": "s", "steps": 4}),
        MacroAction(id="drink:healing_potion", name_en="Drink potion",
                    description_en="Drink it.", verb="drink_potion", category="survival",
                    params={"item": "healing potion"}, needs_manual_mapping=True),
        MacroAction(id="eat:dried_meat", name_en="Eat", description_en="Eat it.",
                    verb="eat", category="survival", params={"item": "dried meat"},
                    needs_manual_mapping=True),
        MacroAction(id="eat:forage", name_en="Forage", description_en="Find food.",
                    verb="eat", category="survival", params={"search": True},
                    needs_manual_mapping=True),
        MacroAction(id="drink:nearest_water", name_en="Water", description_en="Drink.",
                    verb="drink", category="survival", params={"search": True},
                    needs_manual_mapping=True),
        MacroAction(id="rest:here", name_en="Rest", description_en="Rest.",
                    verb="rest", category="survival", params={"reason": "drowsy"}),
        MacroAction(id="pickup:iron_dagger", name_en="Pick up", description_en="Grab it.",
                    verb="pick_up", category="objective", params={"item": "iron dagger",
                                                                  "x": 88, "y": 72},
                    needs_manual_mapping=True),
        MacroAction(id="talk:hunter", name_en="Talk", description_en="Talk to him.",
                    verb="talk", category="objective", params={"direction": "s", "steps": 2},
                    needs_manual_mapping=True),
        MacroAction(id="navigate:boatmurdered", name_en="Travel",
                    description_en="Go there.", verb="navigate_to", category="objective",
                    params={"x": 12, "y": 30}),
        MacroAction(id="navigate:the_hills", name_en="Travel", description_en="Go there.",
                    verb="navigate_to", category="objective", params={"destination": "The Hills"}),
        MacroAction(id="explore:the_hills", name_en="Explore", description_en="Walk.",
                    verb="explore", category="objective", params={"direction": "n", "steps": 3}),
        MacroAction(id="wait:one_turn", name_en="Wait", description_en="Wait.",
                    verb="wait", category="convenience", params={"kind": "short"}),
        MacroAction(id="other", name_en="None", description_en="Nothing.",
                    verb="other", category="convenience"),
    ]
    state = game_state()
    ex, _runner = executor(game=None, guard=False)
    for action in actions:
        dsl = ex.plan_dsl(action, state)
        assert dsl, f"{action.id} produced an empty plan"
        parse_batch(dsl)  # levanta se o DSL for inválido


def test_navigate_with_coordinates_uses_goto():
    ex, _runner = executor(game=None, guard=False)
    action = MacroAction(id="navigate:boatmurdered", name_en="Travel",
                         description_en="Go to the fortress.", verb="navigate_to",
                         category="objective", params={"x": 12, "y": 30})
    assert ex.executable_dsl(action, game_state()) == "goto 12,30; read screen"
    assert parse_batch(ex.executable_dsl(action, game_state()))[0] == {
        "op": "goto", "x": 12, "y": 30,
    }
    # destino sem coordenadas: só se lê o ecrã, o resto fica por mapear
    named = MacroAction(id="navigate:the_hills", name_en="Travel", description_en="Go.",
                        verb="navigate_to", category="objective",
                        params={"destination": "The Hills"})
    assert ex.executable_dsl(named, game_state()) == "read screen"
    assert named.needs_manual_mapping or "no coordinates" in ex.plan_dsl(named, game_state())


def test_refuses_when_no_verified_step_exists():
    ex, runner = executor(game=None, guard=False)
    action = MacroAction(id="teleport:home", name_en="Teleport", description_en="Unknown verb.",
                         verb="teleport", category="convenience")
    result = ex.execute(action, game_state())
    assert result.status == "refused" and result.ok is False
    assert result.reason == "no_verified_steps"
    assert runner.batch_calls() == []


# ---------------------------------------------------------------------- guard


def test_guard_aborts_on_focus_change_without_touching_the_game():
    decision_state = game_state(focus="Adventure")
    current = game_state(focus="Inventory")
    ex, runner = executor(game=FakeGame(current))
    result = ex.execute(attack_action(), decision_state)
    assert result.status == "aborted" and result.ok is False
    assert result.reason == "guard:focus_changed"
    assert result.steps_executed == 0
    assert runner.calls == [], "nada pode ser enviado ao jogo depois do guard recusar"


def test_guard_aborts_when_a_threat_appears():
    decision_state = game_state(focus="Adventure")
    current = game_state(focus="Adventure", hostiles=[hostile("troll", 20, "N")])
    ex, runner = executor(game=FakeGame(current))
    result = ex.execute(
        MacroAction(id="explore:the_hills", name_en="Explore", description_en="Walk.",
                    verb="explore", category="objective", params={"direction": "n", "steps": 2}),
        decision_state,
    )
    assert result.status == "aborted"
    assert result.reason == "guard:threat_appeared"
    assert runner.calls == []


def test_guard_aborts_when_the_hostile_is_replaced_by_another():
    """Regressão (guarda estreita): goblin@6 → dragon@7 tem de ser detetado.

    Mesma contagem de hostis e uma distância *maior*: só a identidade/nível da
    ameaça separa os dois mundos.
    """
    decision_state = game_state(hostiles=[hostile("goblin skulker", 6, "N")], focus="Adventure")
    current = game_state(hostiles=[hostile("dragon", 7, "N")], focus="Adventure")
    ex, runner = executor(game=FakeGame(current))
    result = ex.execute(attack_action(), decision_state)
    assert result.status == "aborted" and result.ok is False
    assert result.reason == "guard:threat_set_changed"
    assert runner.calls == [], "a troca de ameaça tem de parar ANTES do primeiro passo"


def test_guard_aborts_when_great_danger_appears():
    decision_state = game_state(hostiles=[hostile("bronze colossus", 9, "N")], focus="Adventure")
    current = game_state(hostiles=[hostile("bronze colossus", 9, "N", great_danger=True)],
                         focus="Adventure")
    ex, runner = executor(game=FakeGame(current))
    result = ex.execute(attack_action(), decision_state)
    assert result.status == "aborted"
    assert result.reason in ("guard:great_danger_appeared", "guard:threat_set_changed")
    assert runner.calls == []

    # a mesma ameaça que apenas se aproximou de longe (12 → 11) NÃO aborta:
    # isso é o dia-a-dia e os gates do batch continuam a cobri-lo
    calm_before = game_state(hostiles=[hostile("goblin", 12, "N")], focus="Adventure")
    calm_after = game_state(hostiles=[hostile("goblin", 11, "N")], focus="Adventure")
    ex2, runner2 = executor(game=FakeGame(calm_after))
    assert ex2.execute(attack_action(), calm_before).status == "executed"
    assert len(runner2.batch_calls()) == 1


def test_plan_rejects_arguments_outside_the_dsl_vocabulary():
    """Regressão: uma direction construída à mão não pode virar DSL inválido."""
    ex, runner = executor(game=FakeGame(game_state()))
    injected = attack_action(direction="n; key A_TALK")
    with pytest.raises(ValueError) as info:
        ex.plan(injected, game_state())
    assert "not in the DSL vocabulary" in str(info.value)

    result = ex.execute(injected, game_state())          # o passo não morre: recusa
    assert result.status == "refused" and result.ok is False
    assert result.reason == "invalid_plan"
    assert "not in the DSL vocabulary" in result.errors[0]
    assert runner.calls == [], "nada pode chegar à ponte com argumentos inválidos"

    bad_cases = (
        ("attack", {"direction": "north"}),
        ("attack", {"direction": "NE; key A_TALK"}),
        ("attack", {"direction": 7}),
        ("attack", {"steps": -1}),
        ("attack", {"steps": "muitos"}),
        ("attack", {"steps": 10_000}),
        ("navigate_to", {"x": "88; key A_TALK", "y": 72}),
        ("pick_up", {"x": 88.7, "y": "72"}),
    )
    for verb, params in bad_cases:
        action = MacroAction(id=f"{verb}:handmade", name_en="Hand made",
                             description_en="Built by hand.", verb=verb,
                             category="threat", params=params, needs_manual_mapping=True)
        with pytest.raises(ValueError):
            ex.plan(action, game_state())
        refused = ex.execute(action, game_state())
        assert refused.status == "refused" and refused.reason == "invalid_plan"
    assert runner.calls == []

    # o vocabulário legítimo continua a passar
    for direction in ("n", "s", "e", "w", "ne", "nw", "se", "sw", "up", "down", "wait"):
        ex.plan(attack_action(direction=direction), game_state())
    assert ex.executable_dsl(attack_action(direction="N"), game_state()) == (
        "move n times=5; read screen"
    )


def test_guard_aborts_when_the_threat_closes_in():
    decision_state = game_state(hostiles=[hostile("goblin", 9, "N")], focus="Adventure")
    current = game_state(hostiles=[hostile("goblin", 4, "N")], focus="Adventure")
    ex, runner = executor(game=FakeGame(current))
    result = ex.execute(attack_action(), decision_state)
    assert result.status == "aborted" and result.reason == "guard:threat_closed_in"
    assert runner.calls == []


def test_guard_aborts_when_the_adventurer_died():
    decision_state = game_state(status="healthy", focus="Adventure")
    current = game_state(status="dead", focus="Adventure")
    ex, runner = executor(game=FakeGame(current))
    result = ex.execute(attack_action(), decision_state)
    assert result.status == "aborted" and result.reason == "guard:adventurer_dead"


def test_guard_proceeds_when_it_cannot_read_the_state_but_warns():
    class BrokenGame:
        def snapshot(self):
            raise RuntimeError("game closed")

    ex, runner = executor(game=BrokenGame())
    result = ex.execute(attack_action(), game_state())
    assert result.status == "executed"              # os gates da ponte continuam ligados
    assert any("guard could not re-read" in error for error in result.errors)
    assert len(runner.batch_calls()) == 1


# ------------------------------------------------------- abort da própria ponte


def test_bridge_abort_is_reported_as_aborted_with_reason():
    report = {"ok": False, "via": "tcp", "dry_run": False,
              "steps": [{"i": 0, "op": "move", "ok": True, "threats": {"count": 1}}],
              "summary": {"total": 1, "executed": 1, "ok": 1, "failed": 0},
              "aborted_at": 0, "reason": "threat", "journal": "journal/20261008.jsonl"}
    runner = FakeBridgeRunner(batch_report=report, batch_returncode=13)
    ex, _ = executor(runner=runner, game=FakeGame(game_state()))
    result = ex.execute(attack_action(), game_state())
    assert result.status == "aborted" and result.ok is False
    assert result.reason == "threat"
    assert result.steps_executed == 1
    assert result.report["aborted_at"] == 0


def test_bridge_focus_change_abort_is_reported():
    report = {"ok": False, "aborted_at": 1, "reason": "focus_changed",
              "summary": {"total": 2, "executed": 2, "ok": 2, "failed": 0}, "steps": []}
    runner = FakeBridgeRunner(batch_report=report, batch_returncode=13)
    ex, _ = executor(runner=runner, game=FakeGame(game_state()))
    result = ex.execute(attack_action(), game_state())
    assert result.status == "aborted" and result.reason == "focus_changed"


def test_game_down_exit_is_an_error():
    runner = FakeBridgeRunner(batch_returncode=12)
    ex, _ = executor(runner=runner, game=FakeGame(game_state()))
    result = ex.execute(attack_action(), game_state())
    assert result.status == "error" and result.ok is False
    assert "not reachable" in (result.reason or "")


def test_dry_run_validates_the_plan_without_pressing_anything():
    runner = FakeBridgeRunner()
    ex, _ = executor(runner=runner, game=FakeGame(game_state()), dry_run=True)
    result = ex.execute(attack_action(), game_state())
    assert result.status == "dry_run" and result.ok is True
    assert "--dry-run" in runner.batch_calls()[0]


# ----------------------------------------------------------- controlo de tempo


def test_advance_time_unpauses_then_pauses():
    runner = FakeBridgeRunner()
    slept: list[float] = []
    ex, _ = executor(runner=runner, game=FakeGame(game_state()), sleeper=slept.append)
    result = ex.advance_time(2.5)
    assert result.ok is True and result.unpaused and result.paused
    assert slept == [2.5]
    assert runner.subcommands() == ["unpause", "pause"]
    assert runner.calls[0][1].endswith("df_bridge.py")
    assert result.seconds == 2.5


def test_advance_time_reports_a_failed_unpause():
    class FailingRunner(FakeBridgeRunner):
        def __call__(self, argv, timeout=None):
            argv = tuple(str(part) for part in argv)
            self.calls.append(argv)
            if "unpause" in argv:
                return type("P", (), {"returncode": 13, "stdout": "", "stderr": "boom"})()
            return type("P", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()

    ex, _ = executor(runner=FailingRunner(), game=FakeGame(game_state()), sleeper=lambda s: None)
    result = ex.advance_time(1.0)
    assert result.ok is False and result.unpaused is False and result.paused is True
    assert any("unpause failed" in error for error in result.errors)


def test_advance_time_is_a_noop_in_dry_run():
    runner = FakeBridgeRunner()
    ex, _ = executor(runner=runner, game=FakeGame(game_state()), dry_run=True)
    result = ex.advance_time(5.0)
    assert result.ok is True and result.commands == ()
    assert runner.calls == []


def test_bridge_command_carries_the_client_flags():
    game = FakeGame(game_state(), bridge_path="/opt/df/df_bridge.py", python="/usr/bin/python3")
    game.df_path = "/games/Dwarf Fortress"
    game.port = 5001
    ex, _ = executor(game=game)
    argv = ex.bridge_command("act", "batch")
    assert argv[:2] == ["/usr/bin/python3", "/opt/df/df_bridge.py"]
    assert "--json" in argv
    assert argv[argv.index("--df-path") + 1] == "/games/Dwarf Fortress"
    assert argv[argv.index("--port") + 1] == "5001"
