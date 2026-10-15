"""Integração do watchdog global de sessão com **stores/estado mockados** (Passo 18).

O objetivo é o cenário das falhas caras do projecto (PROBLEMAS §D18, §E26): o
loop está *vivo* — decide, executa, gasta — mas o **estado não muda**.  Aqui o
watchdog é alimentado pela composição real (:meth:`SessionActivity.from_sources`
sobre os anéis de ``CampaignRuntime`` e o ledger de ``SpendLedger``) e a escada
conduz um loop falso: re-plano → pergunta PT → kill-switch.  Depois do
kill-switch: zero disparos.
"""

from __future__ import annotations

import json

from system_one_dwarf.agent.config import watchdog_settings
from system_one_dwarf.agent.session_watchdog import (
    STAGE_ASK_HUMAN,
    STAGE_KILL_SWITCH,
    STAGE_REPLAN,
    STALL_EXECUTING_WITHOUT_STATE_CHANGE,
    GlobalSessionWatchdog,
    SessionActivity,
    WatchdogConfig,
)
from system_one_dwarf.agent.stuck import append_pattern_event
from system_one_dwarf.agent.types import ControlState

from .fakes import Clock, FakeRuntime, FakeSpendLedger, state_at


class FakeLoop:
    """O loop falso: consome a escada como o *wiring* real vai consumir."""

    def __init__(self, wd: GlobalSessionWatchdog, runtime: FakeRuntime,
                 ledger: FakeSpendLedger, control: dict) -> None:
        self.wd, self.runtime, self.ledger = wd, runtime, ledger
        self.control = control
        self.replans = 0
        self.questions: list[dict] = []
        self.executed = 0

    def tick(self, state) -> None:
        """Um passo do loop: lê os stores por API e observa a sessão."""
        event = self.wd.observe(
            state, control=self.control,
            activity=SessionActivity.from_sources(
                executed=self.executed,
                decisions=self.runtime.decisions,
                questions=self.runtime.questions,
                approvals=self.runtime.approvals,
                spend=self.ledger.total_usd,
            ),
        )
        if event is None:
            return
        if event.stage == STAGE_REPLAN:
            self.replans += 1
        elif event.stage == STAGE_ASK_HUMAN:
            self.questions.append(event.as_question(f"q-{len(self.questions)}"))
        else:  # STAGE_KILL_SWITCH
            self.control = {"mode": self.control.get("mode", "autopilot"),
                            "kill_switch": True}


def drive(loop: FakeLoop, state, *, minutes: float, step_s: float = 30.0,
          clock: Clock, busy: bool = False) -> None:
    for _ in range(int(round(minutes * 60.0 / step_s))):
        clock.advance(step_s)
        if busy:  # o overlay engole input: executa-se e gasta-se, o estado não muda
            loop.executed += 2
            loop.runtime.decide(action="move:n")
            loop.ledger.record(0.01)
        loop.tick(state)


def test_a_busy_but_frozen_session_is_escalated_within_n_minutes(tmp_path):
    library = tmp_path / "padroes.json"
    library.write_text(json.dumps({"stuck_events": []}), encoding="utf-8")
    clock = Clock()
    runtime, ledger = FakeRuntime(), FakeSpendLedger()
    control = {"mode": "autopilot", "kill_switch": False}
    events = []
    config = WatchdogConfig.from_settings(
        watchdog_settings({"watchdog": {"window_min": 10, "grace_min": 1}},
                          {"SOD_WATCHDOG_STAGE_STEP_MIN": "5"}))
    assert config.window_min == 10.0 and config.stage_step_min == 5.0
    wd = GlobalSessionWatchdog(
        config, clock=clock, wall_clock=lambda: "2026-10-12T12:00:00.000Z",
        on_event=lambda ev: (events.append(ev),
                             append_pattern_event(library, ev.as_stuck_event())),
    )
    loop = FakeLoop(wd, runtime, ledger, control)
    frozen = state_at(10, 10)  # tick congelado: o mesmo snapshot para sempre
    loop.tick(frozen)  # baseline: o primeiro hash é progresso

    drive(loop, frozen, minutes=10.0, clock=clock, busy=True)

    assert [e.stage for e in events] == [STAGE_REPLAN]  # ≤N minutos (N=10)
    assert events[0].stall_kind == STALL_EXECUTING_WITHOUT_STATE_CHANGE
    assert events[0].activity.actions_executed > 0  # assinatura do input engolido
    assert loop.replans == 1 and loop.questions == []


def test_the_ladder_reaches_the_kill_switch_and_then_stays_silent(tmp_path):
    clock = Clock()
    runtime, ledger = FakeRuntime(), FakeSpendLedger()
    control = {"mode": "autopilot", "kill_switch": False}
    events = []
    wd = GlobalSessionWatchdog(
        WatchdogConfig(window_min=6.0, grace_min=1.0, stage_step_min=3.0),
        clock=clock, on_event=events.append,
        wall_clock=lambda: "2026-10-12T12:00:00.000Z",
    )
    loop = FakeLoop(wd, runtime, ledger, control)
    frozen = state_at(10, 10)
    loop.tick(frozen)  # baseline

    drive(loop, frozen, minutes=30.0, clock=clock, busy=True)  # 5× a janela

    assert [e.stage for e in events] == [STAGE_REPLAN, STAGE_ASK_HUMAN, STAGE_KILL_SWITCH]
    assert loop.replans == 1
    assert len(loop.questions) == 1 and loop.questions[0]["question"]  # pergunta PT
    assert loop.control["kill_switch"] is True  # o loop aplicou o kill-switch
    assert wd.latched is True
    # com o kill-switch ativo: zero disparos, mesmo continuando tudo congelado
    drive(loop, frozen, minutes=30.0, clock=clock, busy=True)
    assert len(events) == 3 and wd.suppressed > 0


def test_zero_triggers_while_the_campaign_is_paused(tmp_path):
    clock = Clock()
    runtime, ledger = FakeRuntime(), FakeSpendLedger()
    control = {"mode": "paused", "kill_switch": False}
    events = []
    wd = GlobalSessionWatchdog(
        WatchdogConfig(window_min=6.0, grace_min=0.0, stage_step_min=3.0),
        clock=clock, on_event=events.append,
        wall_clock=lambda: "2026-10-12T12:00:00.000Z",
    )
    loop = FakeLoop(wd, runtime, ledger, control)
    frozen = state_at(10, 10)

    drive(loop, frozen, minutes=120.0, clock=clock, busy=True)  # 20× a janela

    assert events == [] and wd.escalations == 0
    assert wd.status()["suppressed"] > 0


def test_the_kill_switch_gate_accepts_the_real_control_state():
    clock = Clock()
    runtime, ledger = FakeRuntime(), FakeSpendLedger()
    events = []
    wd = GlobalSessionWatchdog(
        WatchdogConfig(window_min=6.0, grace_min=0.0), clock=clock,
        on_event=events.append, wall_clock=lambda: "T",
    )
    frozen = state_at(10, 10)
    wd.observe(frozen)

    for _ in range(40):
        clock.advance(30.0)
        control = ControlState(mode="autopilot", kill_switch=True)
        activity = SessionActivity.from_sources(
            executed=7, decisions=runtime.decisions,
            questions=runtime.questions, approvals=runtime.approvals,
            spend=ledger.total_usd,
        )
        assert wd.observe(frozen, control=control, activity=activity) is None
    assert events == []


def test_a_healthy_session_with_state_changes_never_triggers(tmp_path):
    clock = Clock()
    runtime, ledger = FakeRuntime(), FakeSpendLedger()
    control = {"mode": "autopilot", "kill_switch": False}
    events = []
    wd = GlobalSessionWatchdog(
        WatchdogConfig(window_min=6.0, grace_min=1.0, stage_step_min=3.0),
        clock=clock, on_event=events.append, wall_clock=lambda: "T",
    )
    loop = FakeLoop(wd, runtime, ledger, control)

    for i in range(60):  # 30 min de jogo saudável: o hash muda sempre
        clock.advance(30.0)
        loop.executed += 1
        loop.runtime.decide(action="move:n")
        loop.ledger.record(0.01)
        loop.tick(state_at(10 + i, 10))

    assert events == [] and wd.progress_events > 0


def test_the_emitted_events_feed_the_pattern_library_by_api(tmp_path):
    library = tmp_path / "padroes.json"
    library.write_text(json.dumps({"version": 1, "patterns": [], "stats": {},
                                   "stuck_events": []}), encoding="utf-8")
    clock = Clock()
    runtime, ledger = FakeRuntime(), FakeSpendLedger()
    control = {"mode": "autopilot", "kill_switch": False}
    wd = GlobalSessionWatchdog(
        WatchdogConfig(window_min=6.0, grace_min=1.0, stage_step_min=3.0),
        clock=clock, on_event=lambda ev: append_pattern_event(
            library, ev.as_stuck_event()),
        wall_clock=lambda: "2026-10-12T12:00:00.000Z",
    )
    loop = FakeLoop(wd, runtime, ledger, control)
    frozen = state_at(10, 10)
    loop.tick(frozen)  # baseline

    drive(loop, frozen, minutes=13.0, clock=clock, busy=True)

    stored = json.loads(library.read_text(encoding="utf-8"))["stuck_events"]
    assert [e["escalation"] for e in stored] == ["replan", "break_loop", "stop"]
    assert all(e["kind"] == "stuck" and e["ts"] for e in stored)
    assert {e["state_hash"] for e in stored} == {stored[0]["state_hash"]}  # estado parado
