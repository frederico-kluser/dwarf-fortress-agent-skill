"""``SessionWatchdog`` + ``LoopHealth``: a vigilância da SESSÃO (PROBLEMAS H3).

O ``StuckDetector`` cobre o personagem (3 validações iguais).  O watchdog cobre o
que ele não vê: oscilar entre dois tiles, alternar duas ações, o JEV a abster-se
durante meia hora, o jogo a devolver snapshots vazios.  Mede **progresso
líquido** (novidade) e escala ``replan → force_explore → pause_and_ask``, cada
degrau uma vez por janela.  ``LoopHealth`` é a política pura de reinício do
worker (3 tentativas, 5 s → 15 s → 45 s).

Tudo determinístico: relógio injetado, sem esperas reais.
"""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from system_one_dwarf.agent.stuck import DIRECTIONS, StuckDetector
from system_one_dwarf.agent.watchdog import (
    ACTION_GIVE_UP,
    ACTION_NONE,
    ACTION_RESTART,
    BACKOFF_S,
    LADDER,
    LEVEL_FORCE_EXPLORE,
    LEVEL_PAUSE_AND_ASK,
    LEVEL_REPLAN,
    MAX_RESTARTS,
    NO_PROGRESS_S,
    NO_PROGRESS_STEPS,
    Escalation,
    LoopHealth,
    RestartDecision,
    SessionWatchdog,
)

from .fakes_loop import game_state


class Clock:
    """Relógio monotónico controlado pelo teste."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += float(seconds)


def at(x: int, y: int, z: int = 0, **kwargs):
    """Snapshot real (parser do ``df_bridge``) com o aventureiro em (x, y, z)."""
    return game_state(x=x, y=y, z=z, **kwargs)


def unit(name: str) -> dict:
    return {"name": name, "distance": 4, "direction": "N", "hostile": False,
            "visible": True, "kind": "animal"}


def make(steps: int = 4, seconds: float = 0.0, **kwargs) -> tuple[SessionWatchdog, Clock]:
    clock = Clock()
    kwargs.setdefault("wall_clock", lambda: "2026-10-10T12:00:00.000Z")
    return SessionWatchdog(steps, seconds, clock=clock, **kwargs), clock


def run(wd: SessionWatchdog, state, count: int, clock: Clock | None = None, dt: float = 0.0):
    """Observa ``state`` ``count`` vezes; devolve a lista de escalações (``None`` incluído)."""
    out = []
    for _ in range(count):
        if clock is not None and dt:
            clock.advance(dt)
        out.append(wd.observe(state))
    return out


def levels(events) -> list[str]:
    return [e.level for e in events if e is not None]


# ============================================================ a escada de passos


def test_the_ladder_fires_each_rung_once_at_the_documented_distances():
    wd, _ = make(steps=4)
    state = at(10, 10)

    events = run(wd, state, 12)

    # obs 1 = tile novo (progresso); 4 obs paradas → replan; +2 → force_explore; +2 → pausa
    assert [i for i, e in enumerate(events, 1) if e is not None] == [5, 7, 9]
    assert levels(events) == [LEVEL_REPLAN, LEVEL_FORCE_EXPLORE, LEVEL_PAUSE_AND_ASK]
    assert [e.round for e in events if e] == [1, 2, 3]
    assert tuple(levels(events)) == LADDER


def test_after_pause_and_ask_the_watchdog_stays_quiet_until_progress():
    wd, _ = make(steps=4)
    state = at(10, 10)

    events = run(wd, state, 60)

    assert levels(events) == list(LADDER)  # nunca re-dispara: não rodopia a perguntar
    assert wd.latched is True and wd.round == 3


def test_progress_resets_the_ladder_and_it_can_fire_again():
    wd, _ = make(steps=4)
    run(wd, at(10, 10), 5)                      # observa o replan
    assert wd.round == 1

    assert wd.observe(at(11, 10)) is None        # tile novo: progresso
    assert wd.round == 0 and wd.steps_since_progress == 0

    again = run(wd, at(11, 10), 4)
    assert levels(again) == [LEVEL_REPLAN]       # a escada recomeçou do zero


def test_a_signal_just_before_the_threshold_postpones_the_escalation():
    wd, _ = make(steps=4)
    run(wd, at(10, 10), 4)                       # steps=3, um passo antes do replan
    assert wd.observe(at(12, 12)) is None        # progresso

    assert levels(run(wd, at(12, 12), 3)) == []  # só 3 paradas
    assert levels(run(wd, at(12, 12), 1)) == [LEVEL_REPLAN]


def test_steps_since_progress_counts_only_stale_observations():
    wd, _ = make(steps=100)
    run(wd, at(1, 1), 6)

    assert wd.steps_since_progress == 5  # a 1.ª observação foi o progresso (tile novo)


# ============================================================== tempo (segundos)


def test_the_time_criterion_escalates_a_slow_loop_by_seconds():
    wd, clock = make(steps=0, seconds=100.0)
    state = at(5, 5)

    events = run(wd, state, 30, clock, dt=10.0)

    fired = [(i, e.level, e.reason) for i, e in enumerate(events, 1) if e is not None]
    # t0 = progresso; replan a t=100 (obs 11), force a t=150 (obs 16), pausa a t=200 (obs 21)
    assert fired == [
        (11, LEVEL_REPLAN, "no_progress_time"),
        (16, LEVEL_FORCE_EXPLORE, "no_progress_time"),
        (21, LEVEL_PAUSE_AND_ASK, "no_progress_time"),
    ]


def test_whichever_criterion_comes_first_wins():
    fast, _ = make(steps=5, seconds=900.0)
    first = [e for e in run(fast, at(1, 1), 8) if e]
    assert first[0].reason == "no_progress_steps"

    slow, clock = make(steps=40, seconds=900.0)
    # 100 s por passo: os 900 s chegam ao 10.º passo, muito antes dos 40
    second = [e for e in run(slow, at(1, 1), 12, clock, dt=100.0) if e]
    assert second[0].reason == "no_progress_time"
    assert second[0].steps_since_progress < 40


def test_from_settings_reads_a_config_object_a_mapping_and_garbage():
    from_object = SessionWatchdog.from_settings(
        SimpleNamespace(session_no_progress_steps=12, session_no_progress_s=300.5))
    from_mapping = SessionWatchdog.from_settings({"session_no_progress_steps": "25"})
    garbage = SessionWatchdog.from_settings({"session_no_progress_steps": "abc",
                                             "session_no_progress_s": True})
    off = SessionWatchdog.from_settings({"session_no_progress_steps": 0,
                                         "session_no_progress_s": 0})

    assert (from_object.no_progress_steps, from_object.no_progress_s) == (12, 300.5)
    assert (from_mapping.no_progress_steps, from_mapping.no_progress_s) == (25, NO_PROGRESS_S)
    assert (garbage.no_progress_steps, garbage.no_progress_s) == (NO_PROGRESS_STEPS, NO_PROGRESS_S)
    assert SessionWatchdog.from_settings(None).no_progress_steps == NO_PROGRESS_STEPS
    assert off.enabled is False


def test_default_thresholds_are_the_conservative_documented_ones():
    wd = SessionWatchdog()

    assert (NO_PROGRESS_STEPS, NO_PROGRESS_S) == (40, 900.0)
    assert (wd.no_progress_steps, wd.no_progress_s) == (40, 900.0)
    assert (MAX_RESTARTS, BACKOFF_S) == (3, (5.0, 15.0, 45.0))


def test_pause_and_ask_arrives_in_about_thirty_minutes_not_forty_five():
    wd, clock = make(steps=0, seconds=900.0)

    events = run(wd, at(1, 1), 400, clock, dt=5.0)

    pause = [e for e in events if e is not None and e.pauses][0]
    assert 1700.0 <= pause.seconds_since_progress <= 1805.0  # 900 + 2 × 450


# ================================================================ pausa/retoma


def test_a_long_gap_between_observations_is_a_pause_not_a_stall():
    wd, clock = make(steps=3, idle_gap_s=300.0)
    state = at(3, 3)
    run(wd, state, 3)                  # steps=2, a um passo do replan
    clock.advance(5000.0)              # o humano pausou a sessão durante >1 h

    assert wd.observe(state) is None   # a janela recomeçou: não acusa a sessão
    assert wd.steps_since_progress == 0
    assert levels(run(wd, state, 3)) == [LEVEL_REPLAN]


def test_without_the_idle_gap_the_same_pause_would_have_escalated():
    wd, clock = make(steps=3, idle_gap_s=0.0)  # 0 = não detetar pausas
    state = at(3, 3)
    run(wd, state, 3)
    clock.advance(5000.0)

    assert wd.observe(state).level == LEVEL_REPLAN


# ============================================================ sinais de progresso


@pytest.mark.parametrize(
    "signal",
    [
        pytest.param(lambda: at(20, 20), id="new_tile"),
        pytest.param(lambda: at(10, 10, region="The Hills"), id="new_region"),
        pytest.param(lambda: at(10, 10, adventurer={"name": "U", "kills": 1}), id="kill"),
        pytest.param(lambda: at(10, 10, units=[unit("wild boar")]), id="new_unit"),
    ],
)
def test_each_state_signal_resets_the_window(signal):
    wd, _ = make(steps=4)
    base = at(10, 10, adventurer={"name": "U", "kills": 0})
    run(wd, base, 4)                                   # steps=3
    assert wd.observe(signal()) is None
    assert wd.steps_since_progress == 0

    # a janela é nova: precisa de 4 observações paradas, não de 1
    stale = signal()
    assert levels(run(wd, stale, 3)) == []
    assert levels(run(wd, stale, 1)) == [LEVEL_REPLAN]


def test_an_executed_novel_action_is_progress():
    wd, _ = make(steps=4)
    state = at(10, 10)
    run(wd, state, 4)

    done = SimpleNamespace(action_id="eat:bread", status="executed", ok=True)
    assert wd.observe(state, executed=done) is None
    assert wd.steps_since_progress == 0


def test_repeating_the_same_action_is_not_progress_but_alternating_two_does_not_fool_it():
    wd, _ = make(steps=4)
    state = at(10, 10)
    a = SimpleNamespace(action_id="explore:n", status="executed", ok=True)
    b = SimpleNamespace(action_id="explore:s", status="executed", ok=True)

    wd.observe(state, executed=a)              # novidade (1.ª vez) + tile novo
    assert wd.observe(state, executed=b) is None   # 1.ª vez de b: progresso
    events = []
    for index in range(12):                    # A,B,A,B…: nenhuma é nova
        events.append(wd.observe(state, executed=a if index % 2 == 0 else b))

    assert levels(events) == list(LADDER)


@pytest.mark.parametrize("executed", [
    SimpleNamespace(action_id="wait:one_turn", status="executed", ok=True),
    SimpleNamespace(action_id="other", status="executed", ok=True),
    SimpleNamespace(action_id="explore:n", status="aborted", ok=False),
    SimpleNamespace(action_id="explore:n", status="dry_run", ok=True),
    SimpleNamespace(action_id="explore:n", status="refused", ok=False),
    {"action_id": "wait:short", "status": "executed", "ok": True},
    None,
])
def test_waiting_failed_or_dry_actions_never_count_as_progress(executed):
    wd, _ = make(steps=4)
    state = at(10, 10)
    wd.observe(state)                          # baseline (tile novo)

    events = [wd.observe(state, executed=executed) for _ in range(4)]

    assert levels(events) == [LEVEL_REPLAN]


def test_the_action_id_can_be_passed_explicitly():
    wd, _ = make(steps=4)
    state = at(10, 10)
    run(wd, state, 4)

    assert wd.observe(state, executed=SimpleNamespace(status="executed", ok=True),
                      action_id="drink:nearest_water") is None
    assert wd.steps_since_progress == 0


def test_a_human_answer_is_progress_by_count_or_by_note():
    wd, _ = make(steps=4)
    state = at(10, 10)
    wd.observe(state, human_answers=0)          # baseline do contador
    run(wd, state, 3)
    assert wd.observe(state, human_answers=1) is None      # o contador subiu
    assert wd.steps_since_progress == 0

    run(wd, state, 3)
    wd.note_human_answer()                      # evento fora da thread do loop
    assert wd.observe(state) is None
    assert wd.steps_since_progress == 0

    assert levels(run(wd, state, 4)) == [LEVEL_REPLAN]
    # o mesmo total acumulado (1) não é uma resposta nova: a janela continua a correr
    assert wd.observe(state, human_answers=1) is None
    assert wd.steps_since_progress == 5


def test_oscillating_between_two_tiles_is_not_progress():
    wd, _ = make(steps=6)
    here, there = at(10, 10), at(11, 10)

    events = [wd.observe(here if i % 2 == 0 else there) for i in range(30)]

    assert levels(events) == list(LADDER)  # o StuckDetector nunca vê isto: o hash muda sempre


def test_the_stuck_detector_is_blind_to_the_oscillation_the_watchdog_catches():
    detector = StuckDetector(pattern_sink=False)
    wd, _ = make(steps=6)
    here, there = at(10, 10), at(11, 10)

    stuck_events, escalations = [], []
    for index in range(20):
        state = here if index % 2 == 0 else there
        stuck_events.append(detector.observe(state))
        escalations.append(wd.observe(state))

    assert all(event is None for event in stuck_events)
    assert levels(escalations)[0] == LEVEL_REPLAN


def test_a_town_parade_of_new_npcs_cannot_mask_a_parked_character_forever():
    wd, _ = make(steps=4, max_unit_only_streak=3)
    wd.observe(at(10, 10))
    events = []
    for index in range(40):
        events.append(wd.observe(at(10, 10, units=[unit(f"villager {index}")])))

    assert LEVEL_REPLAN in levels(events)  # só as 3 primeiras criaturas novas contam


def test_a_unit_streak_is_forgiven_by_a_real_signal():
    wd, _ = make(steps=4, max_unit_only_streak=2)
    wd.observe(at(10, 10))
    wd.observe(at(10, 10, units=[unit("a")]))
    wd.observe(at(10, 10, units=[unit("b")]))
    wd.observe(at(11, 10, units=[unit("b")]))     # tile novo: zera a série
    # duas criaturas novas contam outra vez
    assert wd.observe(at(11, 10, units=[unit("c")])) is None
    assert wd.steps_since_progress == 0


# ===================================================================== payloads


def test_each_escalation_carries_what_the_loop_needs():
    wd, _ = make(steps=4)
    events = [e for e in run(wd, at(10, 10), 12) if e]
    replan, force, pause = events

    assert isinstance(replan, Escalation)
    assert replan.level == replan.escalation == LEVEL_REPLAN   # mesmo vocabulário do StuckEvent
    assert replan.direction is None and replan.options == ()
    assert force.level == LEVEL_FORCE_EXPLORE and force.direction in DIRECTIONS
    assert pause.pauses and replan.pauses is False

    # pause_and_ask: a pergunta única ao humano, em PT, com opções fechadas
    assert "parada" in pause.message_pt and "passos" in pause.message_pt
    assert [o["id"] for o in pause.options] == ["continue", "explore", "replan", "stop"]
    assert all(o["text"] for o in pause.options)
    assert pause.last_progress == ("new_tile",)
    assert pause.ts == "2026-10-10T12:00:00.000Z"
    json.dumps(pause.as_dict())  # JSON-friendly: vai para um evento


def test_force_explore_directions_cycle_across_windows():
    wd, _ = make(steps=2, rung_fraction=0.5)
    seen = []
    for window in range(len(DIRECTIONS) + 1):
        wd.observe(at(window, 0))                      # tile novo recomeça a janela
        for event in run(wd, at(window, 0), 6):
            if event is not None and event.level == LEVEL_FORCE_EXPLORE:
                seen.append(event.direction)

    assert seen[: len(DIRECTIONS)] == list(DIRECTIONS)
    assert seen[len(DIRECTIONS)] == DIRECTIONS[0]


def test_replan_and_force_explore_convert_to_the_stuck_detector_vocabulary():
    wd, _ = make(steps=4)
    replan, force, pause = [e for e in run(wd, at(10, 10), 12) if e]

    as_replan, as_force = replan.as_stuck_event(), force.as_stuck_event()

    assert as_replan.escalation == "replan" and as_replan.kind == "stuck"
    assert as_force.escalation == "force_explore" and as_force.direction == force.direction
    assert "session watchdog" in as_force.detail
    assert pause.as_stuck_event() is None


def test_pause_and_ask_builds_the_one_question_for_the_human_queue():
    from system_one_dwarf.app.runtime import CampaignRuntime

    wd, _ = make(steps=4)
    replan, force, pause = [e for e in run(wd, at(10, 10), 12) if e]

    question = pause.as_question("q-7-1")

    assert replan.as_question("q-7-1") is None and force.as_question("q-7-1") is None
    assert question["id"] == "q-7-1" and question["status"] == "pending"
    assert question["question"] == pause.message_pt
    assert [o["id"] for o in question["options"]] == ["continue", "explore", "replan", "stop"]
    assert question["source_data"]["reason"] == "session_stalled"
    assert "action_id" not in question                      # uma dúvida, nunca uma aprovação
    json.dumps(question)

    # a fila do app aceita-a tal e qual: proveniência preservada, sem aprovação espelhada
    runtime = CampaignRuntime("c-1")
    stored = runtime.add_question(question)
    assert (stored["band"], stored["role"]) == ("abstain", "JEV-D")
    assert runtime.add_approval(stored) is None and runtime.list_approvals(status=None) == []


def test_the_message_names_the_minutes_without_progress():
    wd, clock = make(steps=0, seconds=600.0)

    events = run(wd, at(1, 1), 70, clock, dt=10.0)
    replan = [e for e in events if e][0]

    assert "~10 min" in replan.message_pt
    assert "no session progress" in replan.detail


# ================================================================== robustez


def test_a_dead_adventurer_is_left_to_the_stuck_detector():
    wd, _ = make(steps=2)
    dead = at(10, 10, status="dead")

    assert levels(run(wd, dead, 20)) == []


@pytest.mark.parametrize("state", [None, object(), {}, {"metrics": None}, "text", 7,
                                   SimpleNamespace(metrics=SimpleNamespace(location=None))])
def test_junk_states_never_raise(state):
    wd, _ = make(steps=3)

    events = run(wd, state, 12)  # nunca levanta; sem posição não há progresso → escala

    assert levels(events) == list(LADDER)


def test_an_exploding_state_object_is_swallowed():
    class Boom:
        @property
        def metrics(self):
            raise RuntimeError("snapshot exploded")

    wd, _ = make(steps=3)

    assert [wd.observe(Boom()) for _ in range(5)] == [None] * 5


def test_disabled_when_both_criteria_are_off():
    wd, _ = make(steps=0, seconds=0.0)

    assert wd.enabled is False
    assert levels(run(wd, at(1, 1), 200)) == []


def test_memory_is_bounded_by_the_caps():
    wd, _ = make(steps=10_000, visited_cap=50)

    for index in range(3000):
        wd.observe(at(index, index // 7))

    assert len(wd._visited) <= 50


def test_the_history_is_bounded_and_status_is_json():
    wd, _ = make(steps=2)
    for window in range(20):
        wd.observe(at(window, 0))
        run(wd, at(window, 0), 8)

    assert len(wd.history) <= 20
    status = wd.status()
    assert json.loads(json.dumps(status))["escalations"] == wd.escalations
    assert status["enabled"] is True and status["last_level"] == LEVEL_PAUSE_AND_ASK


def test_acknowledge_disarms_the_final_rung_and_the_ladder_can_fire_again():
    wd, _ = make(steps=4)
    run(wd, at(10, 10), 12)
    assert wd.latched

    wd.acknowledge()                    # o humano respondeu à pergunta
    assert wd.latched is False and wd.round == 0

    assert levels(run(wd, at(10, 10), 4)) == [LEVEL_REPLAN]


def test_reset_hard_forgets_what_the_session_has_seen():
    wd, _ = make(steps=100)
    wd.observe(at(1, 1))
    assert wd.observe(at(1, 1)) is None and wd.steps_since_progress == 1

    wd.reset(hard=True)
    wd.observe(at(1, 1))                # outra vez um tile "novo"
    assert wd.steps_since_progress == 0


def test_note_human_answer_is_thread_safe():
    wd, _ = make(steps=100)
    wd.observe(at(1, 1))
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        for _ in range(50):
            wd.note_human_answer()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert wd.observe(at(1, 1)) is None and wd.steps_since_progress == 0


def test_the_same_inputs_give_the_same_escalations():
    def trace():
        wd, clock = make(steps=5, seconds=120.0)
        out = []
        for index in range(60):
            clock.advance(7.0)
            event = wd.observe(at(index % 3, 0))
            out.append(None if event is None else (event.level, event.reason, event.direction,
                                                   event.steps_since_progress))
        return out

    assert trace() == trace()


# ================================================================== LoopHealth


class TestLoopHealth:
    @staticmethod
    def health(**kwargs) -> tuple[LoopHealth, Clock]:
        clock = Clock(0.0)
        return LoopHealth(clock=clock, **kwargs), clock

    def test_the_restart_policy_table(self):
        health, _ = self.health()

        table = []
        for _ in range(MAX_RESTARTS + 2):
            decision = health.on_died("boom")
            table.append((decision.action, decision.delay_s, decision.attempt))

        assert table == [
            (ACTION_RESTART, 5.0, 1),
            (ACTION_RESTART, 15.0, 2),
            (ACTION_RESTART, 45.0, 3),
            (ACTION_GIVE_UP, 0.0, 3),
            (ACTION_GIVE_UP, 0.0, 3),   # esgotado: continua a desistir, nunca volta a tentar
        ]
        assert health.exhausted is True

    def test_a_requested_stop_never_restarts_and_resets_the_count(self):
        health, _ = self.health()
        health.on_died("boom")
        health.on_died("boom")

        stop = health.on_died(None, requested=True)

        assert (stop.action, stop.reason) == (ACTION_NONE, "stop_requested")
        assert stop.restarts is False and health.restarts == 0
        assert health.on_died("boom").attempt == 1  # recomeça da 1.ª tentativa

    def test_a_loop_that_ran_long_enough_restarts_the_count(self):
        health, clock = self.health(healthy_after_s=300.0)
        for _ in range(2):
            health.on_started()
            clock.advance(10)
            health.on_died("early crash")
        assert health.restarts == 2

        health.on_started()
        clock.advance(301)              # correu bem mais de 5 min
        decision = health.on_died("late crash")

        assert (decision.action, decision.attempt, decision.delay_s) == (ACTION_RESTART, 1, 5.0)

    def test_a_crash_loop_is_not_forgiven(self):
        health, clock = self.health(healthy_after_s=300.0)
        actions = []
        for _ in range(5):
            health.on_started()
            clock.advance(299)          # quase saudável, mas nunca chega
            actions.append(health.on_died("crash").action)

        assert actions == [ACTION_RESTART] * 3 + [ACTION_GIVE_UP] * 2

    def test_manual_reset_after_give_up_allows_new_restarts(self):
        health, _ = self.health()
        for _ in range(4):
            health.on_died("boom")
        assert health.exhausted

        health.reset()                  # o humano retomou pelo painel

        assert health.exhausted is False
        assert health.on_died("boom").action == ACTION_RESTART

    def test_custom_budget_and_saturating_backoff(self):
        health, _ = self.health(max_restarts=5, backoff_s=(1.0, 2.0))

        delays = [health.on_died("x").delay_s for _ in range(5)]

        assert delays == [1.0, 2.0, 2.0, 2.0, 2.0]   # satura no último degrau
        assert health.on_died("x").action == ACTION_GIVE_UP

    def test_from_settings_reads_the_restart_budget(self):
        assert LoopHealth.from_settings(SimpleNamespace(loop_max_restarts=5)).max_restarts == 5
        assert LoopHealth.from_settings({"loop_max_restarts": "2"}).max_restarts == 2
        assert LoopHealth.from_settings(None).max_restarts == MAX_RESTARTS
        assert LoopHealth.from_settings({"loop_max_restarts": "x"}).max_restarts == MAX_RESTARTS

    def test_zero_restarts_gives_up_at_the_first_death(self):
        health, _ = self.health(max_restarts=0)

        decision = health.on_died("boom")

        assert decision.action == ACTION_GIVE_UP
        assert "1 vez" in decision.message_pt and "vezes" not in decision.message_pt

    def test_delay_for_is_one_based_and_total(self):
        health, _ = self.health()

        assert [health.delay_for(n) for n in (0, 1, 2, 3, 4, 99)] == [5.0, 5.0, 15.0, 45.0, 45.0, 45.0]

    def test_error_text_is_sanitised_and_bounded(self):
        health, _ = self.health()

        decision = health.on_died("bad\x00thing\x1b[31m" + "x" * 1000)

        assert "\x00" not in decision.message_pt and "\x1b" not in decision.message_pt
        assert len(health.last_error) <= 300

    def test_messages_are_portuguese_and_say_what_happens_next(self):
        health, _ = self.health()

        first = health.on_died("RuntimeError: jogo caiu")
        for _ in range(3):
            last = health.on_died("RuntimeError: jogo caiu")

        assert "a reiniciar em 5 s" in first.message_pt and "tentativa 1/3" in first.message_pt
        assert "não será reiniciado sozinho" in last.message_pt

    def test_status_and_decision_are_json(self):
        health, _ = self.health()
        decision = health.on_died("boom")

        assert isinstance(decision, RestartDecision)
        assert json.loads(json.dumps(decision.as_dict()))["delay_s"] == 5.0
        status = health.status()
        assert json.loads(json.dumps(status)) == status
        assert status["restarts"] == 1 and status["next_delay_s"] == 15.0
        health.on_died("x")
        health.on_died("x")
        assert health.status()["next_delay_s"] is None

    def test_on_died_without_on_started_still_decides(self):
        health, _ = self.health()

        assert health.on_died(None).action == ACTION_RESTART

    def test_concurrent_deaths_keep_the_count_consistent(self):
        health, _ = self.health(max_restarts=1000)
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            for _ in range(25):
                health.on_died("boom")

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert health.restarts == 200 and health.deaths == 200
