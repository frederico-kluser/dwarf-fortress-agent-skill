"""``StuckDetector`` — deteção de STUCK / LOOP DE AÇÕES / MORTE / DANO.

Porquê: a personagem ficou **congelada desde o arranque** em corridas anteriores
e nada o detetava. Este módulo é a peça anti-congelamento do loop: observa cada
validação (snapshot) e compara uma **impressão digital do estado** — posição +
arredores + ids das unidades próximas — com a anterior.

Eventos (um por observação, por ordem de urgência):

===============  ==========  =====================================================
``kind``         ``escalation``  Quando
===============  ==========  =====================================================
``death``        ``stop``    o aventureiro morreu (``status="dead"``/``is_alive``)
``damage``       ``retreat`` HP/dano piorou (ferida nova, sangue a descer, status)
``action_loop``  ``break_loop`` a MESMA ação repetiu-se ≥ N vezes sem progresso
``stuck``        ``force_explore``/``replan`` estado inalterado ≥ N validações
===============  ==========  =====================================================

Escalada do ``stuck`` (a missão exige esta escada):

1. **forçar movimento de exploração** (rumo ainda não tentado) — o loop executa
   a ação ``explore:<rumo>`` imediatamente, sem esperar por banda/aprovação
   (é anti-congelamento; a personagem TEM de se mexer);
2. se continuar preso → **re-plan do LLM** (``replan_fn`` injetável; sem LLM o
   loop pede re-plano e explora outro rumo);
3. loop de ações idênticas (a MESMA ação ≥ 3×) → **quebra com outro candidato**
   (``should_block``: a ação falhada nunca mais é repetida).

Dano/morte: dano força ``retreat`` (fugir/re-plan); morte pára as ações
(``stop``) e o loop reporta.

Cada evento de stuck/loop é **registado na biblioteca de padrões**
(``docs/padroes-navegacao.json``, chave ``stuck_events``) — aprendido uma vez,
nunca re-derivado (:func:`append_pattern_event`).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

__all__ = [
    "StuckEvent",
    "StuckDetector",
    "append_pattern_event",
    "health_fingerprint",
    "pattern_library_path",
    "state_fingerprint",
]

#: Rumos de exploração, por ordem de preferência.
DIRECTIONS: tuple[str, ...] = ("n", "e", "s", "w", "ne", "nw", "se", "sw")

#: Biblioteca de padrões (fonte de verdade), relativa à raiz do repositório.
_LIBRARY_REL = ("docs", "padroes-navegacao.json")


def pattern_library_path() -> Path:
    """Caminho da biblioteca de padrões (raiz do repo detectada por ``__file__``).

    ``src/system_one_dwarf/agent/stuck.py`` → quatro níveis acima é a raiz.
    """
    return Path(__file__).resolve().parents[3].joinpath(*_LIBRARY_REL)


# ------------------------------------------------------------- impressões digitais


def _unit_ids(state: Any) -> list[str]:
    """Identidades estáveis das unidades próximas (ids/nomes — não posições).

    As OUTRAS criaturas movem-se sozinhas: as posições delas mudam sem que a
    personagem faça progresso, por isso só os ids contam para o hash.
    """
    out: list[str] = []
    units = getattr(getattr(state, "metrics", None), "units_nearby", None)
    for unit in getattr(units, "units", ()) or ():
        name = (getattr(unit, "name", None) or "?").strip()
        out.append(name)
    return sorted(out)


def state_fingerprint(state: Any) -> str:
    """Hash do estado relevante: posição + arredores + unidades próximas.

    Duas validações com o mesmo hash = a personagem não progrediu (nem se moveu,
    nem o mundo à volta mudou). O ``screen_text`` fica DE FORA de propósito: o
    relógio/contadores do ecrã mudam sem progresso real.
    """
    metrics = getattr(state, "metrics", None)
    loc = getattr(metrics, "location", None)
    payload = {
        "pos": [getattr(loc, "x", None), getattr(loc, "y", None), getattr(loc, "z", None)],
        "surroundings": [getattr(loc, "region", None), getattr(loc, "site", None),
                         getattr(loc, "surroundings", None), getattr(loc, "tile", None)],
        "units": _unit_ids(state),
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()


_STATUS_RANK: Mapping[str, int] = {
    "dead": 4, "unconscious": 3, "badly wounded": 2, "injured": 2,
    "wounded": 1, "healthy": 0,
}


def health_fingerprint(state: Any) -> tuple[int, int, float, float]:
    """``(gravidade_status, nº_feridas, dor, sangue)`` — pior = maior gravidade.

    ``hp_current`` não existe em adventure mode (é sempre ``None``): o sinal de
    dano é a gravidade do status + contagem de feridas + dor + sangue.
    """
    health = getattr(getattr(state, "metrics", None), "health_injuries", None)
    status = (getattr(health, "status", None) or "").strip().lower()
    severity = _STATUS_RANK.get(status, 1 if status else 0)
    wounds = int(getattr(health, "wound_count", 0) or 0)
    pain = float(getattr(health, "pain", 0.0) or 0.0)
    blood = float(getattr(health, "blood_current", 0.0) or 0.0)
    return (severity, wounds, pain, blood)


def _health_worsened(before: tuple[int, int, float, float],
                     after: tuple[int, int, float, float]) -> bool:
    """O aventureiro levou dano entre as duas validações?"""
    if after[0] > before[0]:
        return True
    if after[1] > before[1]:
        return True
    if after[2] > before[2] + 1e-6:
        return True
    if after[3] < before[3] - 1e-6:
        return True
    return False


# -------------------------------------------------------------------- eventos


@dataclass(frozen=True)
class StuckEvent:
    """Um evento de stuck/loop/morte/dano detetado entre validações."""

    #: ``stuck`` | ``action_loop`` | ``damage`` | ``death``.
    kind: str
    #: ``force_explore`` | ``replan`` | ``break_loop`` | ``retreat`` | ``stop``.
    escalation: str
    detail: str
    #: hash do estado que ficou inalterado (quando aplicável).
    state_hash: str = ""
    #: ação que se repetiu sem progresso (quando aplicável).
    action_id: str | None = None
    #: ação que NUNCA mais deve repetir (``action_loop``).
    blocked_action_id: str | None = None
    #: rumo forçado da exploração (``force_explore``).
    direction: str | None = None
    #: nº de validações inalteradas / repetições da ação.
    repeat: int = 0
    #: nº do evento stuck consecutivo (1 = primeira escada, 2 = re-plano, …).
    stuck_round: int = 0
    ts: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    def as_dict(self) -> dict[str, Any]:
        out = {"kind": self.kind, "escalation": self.escalation, "detail": self.detail,
               "ts": self.ts}
        for key in ("state_hash", "action_id", "blocked_action_id", "direction"):
            value = getattr(self, key)
            if value:
                out[key] = value
        for key in ("repeat", "stuck_round"):
            value = getattr(self, key)
            if value:
                out[key] = value
        return out


# ------------------------------------------------------- biblioteca de padrões


def append_pattern_event(
    path: Path | None = None,
    event: StuckEvent | None = None,
    *,
    max_events: int = 200,
) -> bool:
    """Regista o evento em ``stuck_events`` da biblioteca de padrões (best-effort).

    A biblioteca é a fonte de verdade dos padrões COMPROVADOS: cada evento de
    stuck/loop fica lá para nunca voltar a ser re-derivado. I/O nunca rebenta o
    loop: qualquer erro → ``False``.
    """
    if event is None:
        return False
    target = Path(path) if path is not None else pattern_library_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False
        events = data.get("stuck_events")
        if not isinstance(events, list):
            events = []
        events.append(event.as_dict())
        data["stuck_events"] = events[-max_events:]
        target.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
        return True
    except (OSError, ValueError):
        return False


# -------------------------------------------------------------------- detetor


class StuckDetector:
    """Deteção de stuck/loop/morte/dano com escada de escalação.

    Uso (no loop)::

        detector = StuckDetector()
        event = detector.observe(state, action_id=last_action_id)
        if event is not None:
            ...  # publicar + escalar (forçar exploração / re-plan / quebra)

    Args:
        stuck_after: validações consecutivas sem mudança até ``stuck``
            (missão: ``N=3``).
        action_loop_after: repetições consecutivas da MESMA ação sem progresso
            até ``action_loop`` (missão: ≥ 3×).
        pattern_sink: recebe cada :class:`StuckEvent`.  ``True`` → registo na
            biblioteca real (:func:`append_pattern_event`); ``None``/``False`` →
            sem disco (testes); callable/caminho → o que for dado.
    """

    STUCK_AFTER = 3
    ACTION_LOOP_AFTER = 3

    def __init__(
        self,
        *,
        stuck_after: int = STUCK_AFTER,
        action_loop_after: int = ACTION_LOOP_AFTER,
        pattern_sink: Callable[[StuckEvent], Any] | bool | str | Path | None = None,
    ) -> None:
        self.stuck_after = max(1, int(stuck_after))
        self.action_loop_after = max(1, int(action_loop_after))
        if pattern_sink is True:
            # Registo na biblioteca real — exceto sob pytest (os testes verificam
            # a escrita com caminhos explícitos; a biblioteca do repositório não
            # pode ser poluída por estados falsos).
            if os.environ.get("PYTEST_CURRENT_TEST"):
                self._sink: Callable[[StuckEvent], Any] = lambda ev: False
            else:
                self._sink = lambda ev: append_pattern_event(None, ev)
        elif pattern_sink is None or pattern_sink is False:
            self._sink = lambda ev: False
        elif isinstance(pattern_sink, (str, Path)):
            self._sink = lambda ev: append_pattern_event(Path(pattern_sink), ev)
        else:
            self._sink = pattern_sink
        self._events: list[StuckEvent] = []
        self._blocked: set[str] = set()
        self.reset()

    # -------------------------------------------------------------- estado

    def reset(self) -> None:
        """Recomeça a vigilância (mantém histórico e ações bloqueadas)."""
        self._last_hash: str | None = None
        self._unchanged = 0
        self._stuck_rounds = 0
        self._last_action_id: str | None = None
        self._action_repeats = 0
        self._action_loop_reported: set[str] = set()
        self._tried_directions: list[str] = []
        self._health: tuple[int, int, float, float] | None = None
        self._death_reported = False

    # -------------------------------------------------------------- consultas

    @property
    def events(self) -> tuple[StuckEvent, ...]:
        """Histórico de eventos (todos, por ordem)."""
        return tuple(self._events)

    def blocked_actions(self) -> frozenset[str]:
        """Ações que falharam em loop — **nunca** devem repetir-se."""
        return frozenset(self._blocked)

    def should_block(self, action_id: str | None) -> bool:
        """True quando esta ação já se repetiu sem progresso e deve ser evitada."""
        return bool(action_id) and action_id in self._blocked

    def unvisited_direction(self) -> str:
        """Um rumo ainda não tentado pela escalação (cicla quando esgota)."""
        for direction in DIRECTIONS:
            if direction not in self._tried_directions:
                self._tried_directions.append(direction)
                return direction
        self._tried_directions.clear()
        direction = DIRECTIONS[0]
        self._tried_directions.append(direction)
        return direction

    # -------------------------------------------------------------- observação

    def observe(self, state: Any, *, action_id: str | None = None) -> StuckEvent | None:
        """Observa UMA validação; devolve o evento mais urgente (ou ``None``).

        A ordem de urgência é ``death > damage > action_loop > stuck``. O hash do
        estado só conta como "sem progresso" quando TUDO ficou igual (posição,
        arredores e unidades próximas).
        """
        fp = state_fingerprint(state)
        health = health_fingerprint(state)
        alive = bool(getattr(state, "is_alive", True))
        progressed = fp != self._last_hash
        event: StuckEvent | None = None

        # -- morte ---------------------------------------------------------
        if not alive or health[0] >= 4:
            if not self._death_reported:
                self._death_reported = True
                status = getattr(getattr(state, "metrics", None), "health_injuries", None)
                event = StuckEvent(
                    "death", "stop",
                    f"the adventurer is dead (status={getattr(status, 'status', None)!r})",
                    state_hash=fp, repeat=self._unchanged,
                )
            self._last_hash = fp
            self._health = health
            return self._emit(event)

        # -- dano ----------------------------------------------------------
        if self._health is not None and _health_worsened(self._health, health):
            event = StuckEvent(
                "damage", "retreat",
                f"HP/damage dropped: health {self._health} -> {health}",
                state_hash=fp, repeat=self._unchanged,
            )
            self._unchanged = 0
            self._stuck_rounds = 0

        # -- loop de ações idênticas --------------------------------------
        if action_id and action_id == self._last_action_id and not progressed:
            self._action_repeats += 1
        else:
            self._action_repeats = 1 if action_id else 0
        if (event is None and self._action_repeats >= self.action_loop_after
                and self._last_action_id
                and self._last_action_id not in self._action_loop_reported):
            self._action_loop_reported.add(self._last_action_id)
            self._blocked.add(self._last_action_id)
            event = StuckEvent(
                "action_loop", "break_loop",
                f"action {self._last_action_id!r} repeated {self._action_repeats}x with no progress — blocking it",
                state_hash=fp, action_id=self._last_action_id,
                blocked_action_id=self._last_action_id, repeat=self._action_repeats,
            )
        if action_id:
            self._last_action_id = action_id

        # -- stuck ---------------------------------------------------------
        # ``_unchanged`` conta observações CONSECUTIVAS com o mesmo hash (a
        # primeira conta como 1): N=3 validações iguais seguidas = stuck.
        if progressed:
            self._unchanged = 1
            self._stuck_rounds = 0
        else:
            self._unchanged += 1
            if event is None and self._unchanged >= self.stuck_after:
                self._stuck_rounds += 1
                direction: str | None = None
                if self._stuck_rounds % 2 == 1:
                    escalation = "force_explore"
                    direction = self.unvisited_direction()
                    detail = (f"state unchanged for {self._unchanged} validations — "
                              f"forcing an exploration move towards {direction!r}")
                else:
                    escalation = "replan"
                    detail = (f"still stuck after forced exploration "
                              f"(round {self._stuck_rounds}) — LLM re-plan")
                event = StuckEvent(
                    "stuck", escalation, detail,
                    state_hash=fp, direction=direction, repeat=self._unchanged,
                    stuck_round=self._stuck_rounds,
                )
                self._unchanged = 0  # a escada avança a cada N validações novas

        self._last_hash = fp
        self._health = health
        return self._emit(event)

    # -------------------------------------------------------------- interno

    def _emit(self, event: StuckEvent | None) -> StuckEvent | None:
        if event is None:
            return None
        self._events.append(event)
        try:
            self._sink(event)
        except Exception:  # pragma: no cover - logging nunca rebenta o loop
            pass
        return event
