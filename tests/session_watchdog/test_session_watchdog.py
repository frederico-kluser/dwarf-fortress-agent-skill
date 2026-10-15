"""``GlobalSessionWatchdog``: a sessão nunca fica pendurada em silêncio (Passo 18).

Cobertura exigida pelo Passo 18:

* (a) sessão artificialmente travada detetada e escalada em ≤N minutos;
* (b) zero disparos em ``paused``/kill-switch;
* (c) escada gradual pelos estágios corretos (``replan → ask_human → kill_switch``);
* (d) janela de graça respeitada.

Mais o contrato de suporte: evento estruturado por disparo (callback →
biblioteca de padrões por API), configuração ``SOD_WATCHDOG_*`` por campanha,
determinismo e robustez a lixo.  Tudo com relógio injetado — sem esperas reais.
"""

from __future__ import annotations

import json

import pytest

from system_one_dwarf.agent.config import (
    watchdog_env_overrides,
    watchdog_meta_overrides,
    watchdog_settings,
)
from system_one_dwarf.agent.session_watchdog import (
    LADDER,
    STAGE_ASK_HUMAN,
    STAGE_KILL_SWITCH,
    STAGE_REPLAN,
    STALL_APPROVAL_WITHOUT_EXECUTION,
    STALL_DECIDING_WITHOUT_EXECUTION,
    STALL_EXECUTING_WITHOUT_STATE_CHANGE,
    STALL_SILENT_LOOP,
    GlobalSessionWatchdog,
    SessionActivity,
    SessionStallEvent,
    WatchdogConfig,
)
from system_one_dwarf.agent.stuck import append_pattern_event, state_fingerprint
from system_one_dwarf.agent.types import ControlState

from .fakes import Clock, FakeRuntime, FakeSpendLedger, activity_from, state_at, sweep


def make(*, window: float = 6.0, grace: float = 1.0, step: float = 3.0,
         stages: tuple[str, ...] = LADDER, **kwargs):
    """Watchdog com relógio controlado e recolha dos eventos emitidos."""
    clock = Clock()
    seen: list[SessionStallEvent] = []
    config = WatchdogConfig(window_min=window, grace_min=grace, stage_step_min=step,
                            stages=stages)
    kwargs.setdefault("on_event", seen.append)
    kwargs.setdefault("wall_clock", lambda: "2026-10-12T12:00:00.000Z")
    return GlobalSessionWatchdog(config, clock=clock, **kwargs), clock, seen


# ================================================== (a) sessão travada: ≤N minutos


def test_a_stuck_session_is_detected_and_escalated_in_at_most_n_minutes():
    wd, clock, seen = make(window=6.0)
    state = state_at(10, 10)
    wd.observe(state)  # baseline: o primeiro hash é progresso

    fired = sweep(wd, clock, state, minutes=6.0)

    assert len(fired) == 1
    moment, event = fired[0]
    assert event.stage == STAGE_REPLAN
    # detetada e escalada em ≤N minutos (N = window_min = 6)
    assert (moment - 1000.0) / 60.0 <= 6.0
    assert event.minutes_without_progress <= 6.0
    assert seen == [event]  # o callback estruturado recebe cada disparo


def test_a_stuck_session_reaches_the_full_ladder_within_the_documented_time():
    wd, clock, seen = make(window=6.0, step=3.0)
    state = state_at(10, 10)
    wd.observe(state)

    fired = sweep(wd, clock, state, minutes=15.0)

    assert [e.stage for _, e in fired] == [STAGE_REPLAN, STAGE_ASK_HUMAN, STAGE_KILL_SWITCH]
    minutes = [(m - 1000.0) / 60.0 for m, _ in fired]
    assert minutes == [6.0, 9.0, 12.0]  # N, N+step, N+2·step


def test_oscillating_between_two_states_is_not_progress():
    """A↔B muda o hash sempre mas não há progresso líquido — a sessão está presa."""
    wd, clock, _ = make(window=6.0)
    a, b = state_at(10, 10), state_at(11, 10)
    wd.observe(a)
    wd.observe(b)  # os dois hashes ficam conhecidos

    fired = sweep(wd, clock, lambda i: a if i % 2 else b, minutes=11.0)

    assert [e.stage for _, e in fired] == [STAGE_REPLAN, STAGE_ASK_HUMAN]


# ================================================ (c) escada gradual e correta


def test_the_ladder_is_gradual_and_fires_each_stage_once():
    wd, clock, seen = make(window=6.0, step=3.0)
    state = state_at(10, 10)
    wd.observe(state)

    fired = sweep(wd, clock, state, minutes=30.0)

    assert [e.stage for _, e in fired] == [STAGE_REPLAN, STAGE_ASK_HUMAN, STAGE_KILL_SWITCH]
    assert [e.round for _, e in fired] == [1, 2, 3]
    assert list(seen) == [e for _, e in fired]
    assert wd.latched is True  # depois do kill_switch: mudo até progresso/acknowledge


def test_progress_resets_the_ladder_and_it_can_fire_again():
    wd, clock, _ = make(window=6.0, step=3.0)
    state = state_at(10, 10)
    wd.observe(state)
    sweep(wd, clock, state, minutes=7.0)  # já disparou replan (round=1)

    moved = state_at(20, 20)
    assert wd.observe(moved) is None  # progresso reancora a janela
    assert wd.round == 0

    fired = sweep(wd, clock, moved, minutes=6.0)
    assert [e.stage for _, e in fired] == [STAGE_REPLAN]


def test_the_stages_are_configurable_per_campaign():
    wd, clock, _ = make(window=6.0, step=3.0, stages=(STAGE_REPLAN, STAGE_KILL_SWITCH))
    state = state_at(10, 10)
    wd.observe(state)

    fired = sweep(wd, clock, state, minutes=12.0)

    assert [e.stage for _, e in fired] == [STAGE_REPLAN, STAGE_KILL_SWITCH]


def test_the_ask_human_stage_carries_a_portuguese_question():
    wd, clock, _ = make(window=6.0, step=3.0)
    state = state_at(10, 10)
    wd.observe(state)
    fired = sweep(wd, clock, state, minutes=13.0)

    assert [e.stage for _, e in fired] == [STAGE_REPLAN, STAGE_ASK_HUMAN, STAGE_KILL_SWITCH]
    question = fired[1][1].as_question("q-1")
    assert question is not None
    assert question["id"] == "q-1" and question["status"] == "pending"
    assert question["question"] == fired[1][1].message_pt and question["question"]
    assert [o["id"] for o in question["options"]] == ["continue", "replan", "stop"]
    # os outros estágios não levantam pergunta
    assert fired[0][1].as_question("q-2") is None
    assert fired[2][1].as_question("q-3") is None


def test_each_event_carries_the_stall_signature_for_the_pattern_library():
    clock = Clock()
    runtime, ledger = FakeRuntime(), FakeSpendLedger()
    counters = {"executed": 0}
    seen: list[SessionStallEvent] = []
    wd = GlobalSessionWatchdog(
        WatchdogConfig(window_min=6.0, grace_min=1.0, stage_step_min=3.0),
        clock=clock, on_event=seen.append,
        activity_source=lambda: activity_from(runtime, ledger, counters["executed"]),
        wall_clock=lambda: "2026-10-12T12:00:00.000Z",
    )
    state = state_at(10, 10)
    wd.observe(state)

    def grow(i: int) -> None:  # a sessão gasta e executa, mas o estado não muda
        counters["executed"] += 1
        runtime.decide(action="move:n")
        ledger.record(0.01)

    fired = sweep(wd, clock, state, minutes=7.0, each=grow)

    event = fired[0][1]
    assert event is seen[0]
    assert event.stall_kind == STALL_EXECUTING_WITHOUT_STATE_CHANGE  # assinatura D18
    assert event.activity.actions_executed > 0 and event.activity.spend_usd > 0
    assert event.state_hash == state_fingerprint(state)
    assert "executed=" in event.detail and event.message_pt


@pytest.mark.parametrize("scenario,expected", [
    ("executing", STALL_EXECUTING_WITHOUT_STATE_CHANGE),
    ("approvals", STALL_APPROVAL_WITHOUT_EXECUTION),
    ("questions", STALL_APPROVAL_WITHOUT_EXECUTION),
    ("decisions", STALL_DECIDING_WITHOUT_EXECUTION),
    ("spend", STALL_DECIDING_WITHOUT_EXECUTION),
    ("silent", STALL_SILENT_LOOP),
])
def test_the_stall_kind_distinguishes_the_silent_failures(scenario, expected):
    """A assinatura diz *que* falha silenciosa está a acontecer (D18/E26)."""
    clock = Clock()
    runtime, ledger = FakeRuntime(), FakeSpendLedger()
    executed = {"n": 0}
    wd = GlobalSessionWatchdog(
        WatchdogConfig(window_min=6.0, grace_min=1.0, stage_step_min=3.0),
        clock=clock,
        activity_source=lambda: activity_from(runtime, ledger, executed["n"]),
        wall_clock=lambda: "2026-10-12T12:00:00.000Z",
    )
    state = state_at(10, 10)
    wd.observe(state)

    def grow(i: int) -> None:
        if scenario == "executing":
            executed["n"] += 1
        elif scenario == "approvals":
            runtime.ask() if i == 0 else runtime.approve()
        elif scenario == "questions":
            runtime.ask()
        elif scenario == "decisions":
            runtime.decide()
        elif scenario == "spend":
            ledger.record(0.01)

    fired = sweep(wd, clock, state, minutes=7.0, each=grow)
    assert fired[0][1].stall_kind == expected


# ======================================================= (b) paused / kill-switch


@pytest.mark.parametrize("control", [
    {"mode": "paused", "kill_switch": False},
    {"mode": "paused", "kill_switch": True},
    {"mode": "autopilot", "kill_switch": True},
    ControlState(mode="paused", kill_switch=False),
    ControlState(mode="autopilot", kill_switch=True),
])
def test_zero_triggers_while_paused_or_kill_switch_active(control):
    wd, clock, seen = make(window=6.0, grace=0.0)
    state = state_at(10, 10)
    wd.observe(state)

    fired = sweep(wd, clock, state, minutes=60.0, control=control)  # 10× a janela

    assert fired == [] and seen == []
    assert wd.escalations == 0 and wd.suppressed > 0


def test_pause_time_never_counts_as_stall_and_resume_restarts_the_window():
    wd, clock, seen = make(window=6.0, grace=0.0)
    state = state_at(10, 10)
    wd.observe(state)
    sweep(wd, clock, state, minutes=55.0, control={"mode": "paused"})

    resumed = sweep(wd, clock, state, minutes=5.5)  # menos de N min após a retoma
    assert resumed == [] and seen == []

    fired = sweep(wd, clock, state, minutes=0.5)  # completa a janela nova
    assert [e.stage for _, e in fired] == [STAGE_REPLAN]


def test_the_kill_switch_stage_is_itself_suppressed_when_the_switch_is_already_on():
    wd, clock, _ = make(window=6.0, step=3.0)
    state = state_at(10, 10)
    wd.observe(state)
    fired = sweep(wd, clock, state, minutes=11.5,
                  control={"mode": "autopilot", "kill_switch": False})
    assert [e.stage for _, e in fired] == [STAGE_REPLAN, STAGE_ASK_HUMAN]

    quiet = sweep(wd, clock, state, minutes=10.0,
                  control={"mode": "autopilot", "kill_switch": True})
    assert quiet == []  # kill-switch ativo: nada dispara, nem o estágio final


# ================================================== (d) janela de graça


def test_the_grace_window_holds_fire_even_past_the_no_progress_window():
    wd, clock, seen = make(window=2.0, grace=30.0, step=1.0)
    state = state_at(10, 10)
    wd.observe(state)

    quiet = sweep(wd, clock, state, minutes=29.0)  # a janela N passou aos 2 min
    assert quiet == [] and seen == []

    fired = sweep(wd, clock, state, minutes=1.0)
    assert [e.stage for _, e in fired] == [STAGE_REPLAN]
    assert fired[0][1].minutes_without_progress == 30.0


def test_acknowledge_re_arms_the_grace_window():
    wd, clock, _ = make(window=2.0, grace=30.0, step=1.0, stages=(STAGE_REPLAN,))
    state = state_at(10, 10)
    wd.observe(state)
    assert len(sweep(wd, clock, state, minutes=30.0)) == 1

    wd.acknowledge()  # o humano tratou: a graça volta a correr
    assert sweep(wd, clock, state, minutes=29.0) == []
    fired = sweep(wd, clock, state, minutes=1.0)
    assert [e.stage for _, e in fired] == [STAGE_REPLAN]


def test_a_human_answer_re_arms_grace_and_counts_as_progress():
    wd, clock, _ = make(window=10.0, grace=30.0, step=5.0, stages=(STAGE_REPLAN,))
    state = state_at(10, 10)
    wd.observe(state)
    sweep(wd, clock, state, minutes=25.0)  # dentro da graça: nada

    wd.note_human_answer()
    assert wd.observe(state) is None  # humano = progresso: janela reinicia
    assert wd.seconds_since_progress() == 0.0

    assert sweep(wd, clock, state, minutes=29.0) == []
    assert [e.stage for _, e in sweep(wd, clock, state, minutes=1.0)] == [STAGE_REPLAN]


def test_grace_alone_never_fires_without_the_no_progress_window():
    """A graça é um piso, não um gatilho: com progresso recente nada dispara."""
    wd, clock, _ = make(window=30.0, grace=2.0, step=5.0, stages=(STAGE_REPLAN,))
    state = state_at(10, 10)
    for _ in range(10):
        clock.advance(60.0)
        assert wd.observe(state_at(10 + int(clock.t), 10)) is None  # progresso constante


# ================================== evento estruturado → biblioteca de padrões


def test_each_trigger_emits_a_structured_event_reusable_by_the_pattern_library(tmp_path):
    library = tmp_path / "padroes.json"
    library.write_text(json.dumps({"version": 1, "patterns": [], "stats": {},
                                   "stuck_events": []}), encoding="utf-8")
    seen: list[SessionStallEvent] = []

    def sink(event: SessionStallEvent) -> None:
        seen.append(event)
        append_pattern_event(library, event.as_stuck_event())  # ligação por API

    wd, clock, _ = make(window=6.0, step=3.0, on_event=sink)
    state = state_at(10, 10)
    wd.observe(state)
    fired = sweep(wd, clock, state, minutes=13.0)

    assert len(fired) == 3 and len(seen) == 3  # o sink recebe todos os disparos
    stored = json.loads(library.read_text(encoding="utf-8"))["stuck_events"]
    assert [e["kind"] for e in stored] == ["stuck"] * 3
    assert [e["escalation"] for e in stored] == ["replan", "break_loop", "stop"]
    assert all(e["ts"] and e["state_hash"] for e in stored)
    assert [e["stuck_round"] for e in stored] == [1, 2, 3]


def test_the_event_serialises_to_json_and_back():
    wd, clock, _ = make(window=6.0, step=3.0)
    state = state_at(10, 10)
    wd.observe(state)
    event = sweep(wd, clock, state, minutes=7.0)[0][1]

    payload = json.loads(json.dumps(event.as_dict()))
    assert payload["stage"] == STAGE_REPLAN and payload["reason"] == "no_progress"
    assert payload["activity"]["decisions"] == 0
    stuck = event.as_stuck_event()
    assert stuck.kind == "stuck" and stuck.escalation == "replan"


def test_a_failing_sink_never_drops_the_escalation_or_the_loop():
    def boom(event):
        raise RuntimeError("sink em baixo")

    wd, clock, seen = make(window=6.0, on_event=boom)
    state = state_at(10, 10)
    wd.observe(state)
    fired = sweep(wd, clock, state, minutes=7.0)

    assert [e.stage for _, e in fired] == [STAGE_REPLAN]  # devolvido apesar do sink
    assert seen == []  # (o sink local falhou; o registo interno mantém-se)


# ============================================================ configuração SOD_*


def test_watchdog_settings_read_env_and_campaign_meta():
    env = {"SOD_WATCHDOG_WINDOW_MIN": "20", "SOD_WATCHDOG_GRACE_MIN": "4",
           "SOD_WATCHDOG_STAGES": "replan,kill_switch", "SOD_WATCHDOG_ENABLED": "1",
           "SOD_MODE": "autopilot"}  # ruído: não pertence ao watchdog
    meta = {"watchdog": {"grace_min": 9}, "config": {"watchdog_window_min": 30}}

    assert watchdog_env_overrides(env) == {
        "window_min": "20", "grace_min": "4",
        "stages": "replan,kill_switch", "enabled": "1",
    }
    assert watchdog_meta_overrides(meta) == {"grace_min": 9, "window_min": 30}
    config = WatchdogConfig.from_settings(watchdog_settings(meta, env))
    assert config.window_min == 30.0  # a meta da campanha ganha ao env
    assert config.grace_min == 9.0
    assert config.stages == (STAGE_REPLAN, STAGE_KILL_SWITCH)
    assert config.enabled is True


def test_watchdog_settings_tolerate_garbage_and_absent_values():
    assert watchdog_env_overrides({}) == {}
    assert watchdog_meta_overrides(None) == {}
    assert watchdog_meta_overrides({"watchdog": "lixo"}) == {}
    assert watchdog_settings(None, None) == {}

    config = WatchdogConfig.from_settings({"window_min": "abc", "grace_min": None,
                                           "stage_step_min": "xyz", "stages": "dança",
                                           "enabled": "banana"})
    assert config.window_min == WatchdogConfig().window_min
    assert config.stage_step_min == pytest.approx(config.window_min / 2.0)  # omissão
    assert config.stages == LADDER and config.enabled is True


def test_direct_construction_rejects_unknown_stages():
    with pytest.raises(ValueError):
        WatchdogConfig(stages=("dança",))
    with pytest.raises(ValueError):
        WatchdogConfig(stages=())


def test_a_disabled_or_zero_window_watchdog_never_fires():
    for config in (WatchdogConfig(enabled=False), WatchdogConfig(window_min=0.0)):
        clock = Clock()
        fired: list[SessionStallEvent] = []
        wd = GlobalSessionWatchdog(config, clock=clock, on_event=fired.append)
        state = state_at(10, 10)
        for _ in range(40):
            clock.advance(60.0)
            assert wd.observe(state) is None
        assert fired == [] and wd.status()["enabled"] is False


# ================================================== determinismo e robustez


def test_the_same_timeline_gives_the_same_escalations():
    def run():
        wd, clock, _ = make(window=6.0, step=3.0)
        state = state_at(10, 10)
        wd.observe(state)
        return [(m, e.as_dict()) for m, e in sweep(wd, clock, state, minutes=15.0)]

    assert run() == run()


@pytest.mark.parametrize("junk", [None, 0, "lixo", [], object()])
def test_junk_activity_and_control_never_raise(junk):
    wd, clock, _ = make(window=6.0)
    state = state_at(10, 10)
    wd.observe(state, activity=junk, control=junk)
    fired = sweep(wd, clock, state, minutes=7.0, activity=junk, control=None)
    assert [e.stage for _, e in fired] == [STAGE_REPLAN]


def test_an_exploding_activity_source_is_swallowed():
    def boom():
        raise RuntimeError("store em baixo")

    wd, clock, _ = make(window=6.0, activity_source=boom)
    state = state_at(10, 10)
    wd.observe(state)
    assert [e.stage for _, e in sweep(wd, clock, state, minutes=7.0)] == [STAGE_REPLAN]


def test_a_dead_adventurer_is_left_to_the_stuck_detector():
    wd, clock, seen = make(window=6.0)
    alive, dead = state_at(10, 10), state_at(10, 10, status="dead")
    wd.observe(alive)
    assert sweep(wd, clock, dead, minutes=30.0) == [] and seen == []


def test_reset_and_status_are_json_friendly():
    wd, clock, _ = make(window=6.0, step=3.0)
    state = state_at(10, 10)
    wd.observe(state)
    sweep(wd, clock, state, minutes=10.0)
    status = wd.status()

    assert json.loads(json.dumps(status))["round"] == 2
    assert status["minutes_without_progress"] >= 0
    wd.reset(hard=True)
    assert wd.round == 0 and wd.status()["escalations"] == 2
