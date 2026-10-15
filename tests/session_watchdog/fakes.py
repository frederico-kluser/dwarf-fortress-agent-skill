"""Fakes partilhados dos testes do watchdog global de sessão (Passo 18).

Tudo determinístico: relógio injetado, stores falsos com a **mesma forma** das
APIs reais (anéis de ``app/runtime.CampaignRuntime`` e ledger de
``campaign/spend.SpendLedger``) e estado construído pelo parser real
(:meth:`GameState.from_dict`) a partir de payloads mínimos.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from system_one_dwarf.game.models import GameState


class Clock:
    """Relógio monotónico controlado pelo teste."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += float(seconds)


def state_at(x: int = 10, y: int = 10, z: int = 0, *,
             units: Sequence[str] = (), status: str = "healthy") -> GameState:
    """``GameState`` real (parser do snapshot) com o aventureiro em ``(x, y, z)``."""
    return GameState.from_dict({
        "schema_version": 1,
        "ts": "2026-10-12T12:00:00.000Z",
        "mode": "adventure",
        "metrics": {
            "location": {
                "x": x, "y": y, "z": z, "world": "Testworld", "region": "R",
                "site": "S", "surroundings": "forest", "tile": None,
            },
            "health_injuries": {"status": status, "wounds": [], "wound_count": 0},
            "units_nearby": {"count": len(units),
                             "units": [{"name": name, "distance": 4} for name in units]},
        },
    })


class FakeRuntime:
    """Espelho dos anéis de ``CampaignRuntime`` (``decisions``/``questions``/``approvals``)."""

    def __init__(self) -> None:
        self.decisions: list[dict[str, Any]] = []
        self.questions: dict[str, dict[str, Any]] = {}
        self.approvals: dict[str, dict[str, Any]] = {}

    def decide(self, **payload: Any) -> dict[str, Any]:
        record = {"id": f"d-{len(self.decisions)}", "status": "pending", **payload}
        self.decisions.append(record)
        return record

    def ask(self, **payload: Any) -> dict[str, Any]:
        record = {"id": f"q-{len(self.questions)}", "status": "pending", **payload}
        self.questions[record["id"]] = record
        return record

    def approve(self, **payload: Any) -> dict[str, Any]:
        record = {"id": f"a-{len(self.approvals)}", "status": "pending", **payload}
        self.approvals[record["id"]] = record
        return record


class FakeSpendLedger:
    """Espelho de ``SpendLedger`` — só a API de leitura usada pela composição."""

    def __init__(self) -> None:
        self.total = 0.0

    def record(self, cost: float) -> None:
        self.total += float(cost)

    def total_usd(self) -> float:
        return self.total


def activity_from(runtime: FakeRuntime, ledger: FakeSpendLedger,
                  executed: int = 0) -> Mapping[str, Any]:
    """A leitura de API que o *wiring* faz: anéis + ledger + contador de execuções."""
    return {
        "actions_executed": executed,
        "decisions": runtime.decisions,
        "questions": runtime.questions,
        "approvals": runtime.approvals,
        "spend_usd": ledger.total_usd,
    }


def sweep(wd: Any, clock: Clock, state: Any, *, minutes: float, step_s: float = 30.0,
          control: Any = None, activity: Any = None,
          each: Callable[[int], None] | None = None) -> list[tuple[float, Any]]:
    """Observa ``state`` durante ``minutes`` min em passos de ``step_s``.

    ``state`` pode ser um ``i -> estado`` (para alternar estados); devolve
    ``(momento, evento)`` de cada disparo; ``each(i)`` corre antes de cada
    observação (para crescer stores falsos, por exemplo).
    """
    fired: list[tuple[float, Any]] = []
    steps = int(round(minutes * 60.0 / step_s))
    for i in range(steps):
        clock.advance(step_s)
        if each is not None:
            each(i)
        target = state(i) if callable(state) else state
        event = wd.observe(target, control=control, activity=activity)
        if event is not None:
            fired.append((clock.t, event))
    return fired
