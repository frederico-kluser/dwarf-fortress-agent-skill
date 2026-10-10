"""``SessionWatchdog`` + ``LoopHealth`` — a vigilância da SESSÃO (PROBLEMAS H3).

Porquê: o :class:`~system_one_dwarf.agent.stuck.StuckDetector` cobre o
*personagem* (3 validações com o mesmo hash).  A **sessão** pode estar viva e
inútil de maneiras que ele não vê — o JEV-A abstém-se 349 vezes em 415, o
personagem oscila entre dois tiles (o hash muda sempre), alterna duas ações, o
jogo devolve snapshots vazios.  Passou meia hora, nada se aprendeu, e os tokens
gastam-se.  Lógica **pura** (sem I/O nem threads, relógio injetável):

:class:`SessionWatchdog`
    mede *progresso líquido* e, passada a janela sem progresso, devolve uma
    :class:`Escalation` numa escada de três degraus, cada um **uma vez por janela**:
    ``replan`` (plano/dica nova do LLM) → ``force_explore`` (rumo ainda não
    tentado) → ``pause_and_ask`` (pausar o loop e levantar UMA pergunta ao humano
    em vez de rodopiar).  Os sinais de progresso são de **novidade** — oscilar
    A↔B nunca conta: ``new_tile`` (tile não visto), ``new_region``, ``kill``,
    ``new_unit`` (limitado por ``MAX_UNIT_ONLY_STREAK``), ``action`` (ação
    executada, não-``wait``/saída, cujo id nunca correu) e ``human`` (o humano
    respondeu).

:class:`LoopHealth`
    política *pura* de **reinício com backoff** quando a thread do loop morre
    (``max_restarts=3``, 5 s → 15 s → 45 s); o ``LoopRunner`` pergunta
    ``on_died()`` e executa a decisão.

Decisões: limiares conservadores (40 passos / 900 s — um falso positivo pausa e
pergunta, um falso negativo gasta tokens); cada degrau seguinte pede
``rung_fraction`` (0,5) da janela base (40 → 60 → 80 passos; ``pause_and_ask`` em
~30 min, não 45); passos **ou** segundos, o que vier primeiro; um intervalo
grande entre observações (``idle_gap_s``) é pausa/retoma, não estagnação; nunca
levanta, determinístico, tudo o que cresce está limitado.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .stuck import DIRECTIONS, StuckEvent
from .types import utc_now_iso

log = logging.getLogger("system_one_dwarf.agent.watchdog")

__all__ = [
    "ACTION_GIVE_UP", "ACTION_NONE", "ACTION_RESTART", "BACKOFF_S", "HEALTHY_AFTER_S",
    "IDLE_GAP_S", "LADDER", "LEVEL_FORCE_EXPLORE", "LEVEL_PAUSE_AND_ASK", "LEVEL_REPLAN",
    "MAX_RESTARTS", "NO_PROGRESS_S", "NO_PROGRESS_STEPS", "PAUSE_OPTIONS", "RUNG_FRACTION",
    "Escalation", "LoopHealth", "RestartDecision", "SessionWatchdog",
]

LEVEL_REPLAN = "replan"
LEVEL_FORCE_EXPLORE = "force_explore"
LEVEL_PAUSE_AND_ASK = "pause_and_ask"
#: A escada, do mais barato para o mais caro.
LADDER: tuple[str, str, str] = (LEVEL_REPLAN, LEVEL_FORCE_EXPLORE, LEVEL_PAUSE_AND_ASK)

# ----------------------------------------------------------- números afináveis
#: Passos sem progresso até ao 1.º degrau (``<= 0`` desliga o critério).
NO_PROGRESS_STEPS = 40
#: Segundos sem progresso até ao 1.º degrau (``<= 0`` desliga o critério).
NO_PROGRESS_S = 900.0
#: Cada degrau seguinte pede esta fração da janela base (0,5 → 40, 60, 80).
RUNG_FRACTION = 0.5
#: Intervalo entre observações a partir do qual é pausa/retoma, não estagnação.
IDLE_GAP_S = 300.0
#: Tiles distintos lembrados (FIFO) — voltar a um tile muito antigo conta como novo.
VISITED_CAP = 2048
#: Regiões/ações/criaturas distintas lembradas na sessão (FIFO).
REGIONS_CAP = 128
ACTIONS_CAP = 512
UNITS_CAP = 512
#: Quantos progressos *só por criatura nova* seguidos se aceitam: numa vila o
#: desfile de NPCs não pode mascarar para sempre um personagem parado.
MAX_UNIT_ONLY_STREAK = 3
#: Verbos de ação que nunca contam como progresso ("não fazer nada").
NON_PROGRESS_VERBS = frozenset({"wait", "other", "none", "noop", "idle", "none_of_the_above"})
#: Escalações lembradas para diagnóstico.
HISTORY_CAP = 20

#: Reinício do loop (``LoopHealth``): tentativas, esperas e "correu bem tempo
#: suficiente para a contagem de falhas zerar".
MAX_RESTARTS = 3
BACKOFF_S: tuple[float, ...] = (5.0, 15.0, 45.0)
HEALTHY_AFTER_S = 300.0

ACTION_NONE = "none"
ACTION_RESTART = "restart"
ACTION_GIVE_UP = "give_up"

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_MAX_ERROR_CHARS = 300

#: Opções PT da pergunta ``pause_and_ask`` (ids estáveis para o loop/UI).
PAUSE_OPTIONS: tuple[dict[str, str], ...] = (
    {"id": "continue", "text": "Continuar a tentar"},
    {"id": "explore", "text": "Explorar noutra direção"},
    {"id": "replan", "text": "Pedir um plano novo ao agente"},
    {"id": "stop", "text": "Parar a sessão"},
)


# ==================================================================== tipos


@dataclass(frozen=True)
class Escalation:
    """Um degrau da escada, devolvido por :meth:`SessionWatchdog.observe`.

    ``message_pt`` é o texto para o humano (a pergunta, no degrau
    ``pause_and_ask``); ``detail`` é o diagnóstico em EN para logs e para o
    prompt de re-plano.
    """

    level: str
    round: int
    reason: str
    steps_since_progress: int
    seconds_since_progress: float
    message_pt: str
    detail: str
    direction: str | None = None
    last_progress: tuple[str, ...] = ()
    options: tuple[dict[str, str], ...] = ()
    ts: str = ""

    @property
    def escalation(self) -> str:
        """Alias de :attr:`level` (o nome que ``StuckEvent`` usa)."""
        return self.level

    @property
    def pauses(self) -> bool:
        """``True`` no degrau em que o loop deve parar e perguntar ao humano."""
        return self.level == LEVEL_PAUSE_AND_ASK

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "level": self.level, "round": self.round, "reason": self.reason,
            "steps_since_progress": self.steps_since_progress,
            "seconds_since_progress": round(self.seconds_since_progress, 1),
            "message_pt": self.message_pt, "detail": self.detail,
            "last_progress": list(self.last_progress), "ts": self.ts,
        }
        if self.direction:
            out["direction"] = self.direction
        if self.options:
            out["options"] = [dict(option) for option in self.options]
        return out

    def as_question(self, question_id: str) -> dict[str, Any] | None:
        """A pergunta ÚNICA do degrau ``pause_and_ask`` (``None`` nos outros).

        Já na forma da fila (``question_new`` / ``docs/api.md``): campos do contrato
        + proveniência aditiva (``band``/``role``/``source_data``).  Sem
        ``action_id`` — é uma dúvida (``JEV-D``), nunca uma aprovação.
        """
        if not self.pauses:
            return None
        return {
            "id": question_id,
            "ts": self.ts or utc_now_iso(),
            "question": self.message_pt,
            "options": [dict(option) for option in self.options],
            "free_text_allowed": True,
            "status": "pending",
            "format": "multiple",
            "why_pt": "o watchdog da sessão não viu progresso: o loop foi pausado para não gastar em vão",
            "band": "abstain",
            "role": "JEV-D",
            "source_data": {
                "reason": "session_stalled", "band": "abstain",
                "steps_since_progress": self.steps_since_progress,
                "seconds_since_progress": round(self.seconds_since_progress, 1),
                "last_progress": list(self.last_progress),
            },
        }

    def as_stuck_event(self) -> StuckEvent | None:
        """A mesma escalada na forma do ``StuckDetector`` (``None`` em ``pause_and_ask``).

        Deixa o loop reutilizar, sem código novo, o caminho que já sabe forçar
        exploração (``force_explore``) e pedir re-plano (``replan``).
        """
        if self.level not in (LEVEL_REPLAN, LEVEL_FORCE_EXPLORE):
            return None
        return StuckEvent(
            "stuck", self.level, f"session watchdog: {self.detail}",
            direction=self.direction, repeat=self.steps_since_progress,
            stuck_round=self.round, ts=self.ts or utc_now_iso(),
        )


# ============================================================ extração tolerante


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _action_verb(action_id: str) -> str:
    return action_id.split(":", 1)[0].strip().lower()


def _clean_error(error: Any) -> str:
    if error is None:
        return ""
    return _CONTROL_RE.sub(" ", str(error)).strip()[:_MAX_ERROR_CHARS]


def _minutes(seconds: float) -> int:
    return int(round(max(0.0, seconds) / 60.0))


def _setting(settings: Any, name: str, default: Any, kind: type) -> Any:
    """``settings.<name>`` / ``settings[name]`` coerced to ``kind`` (lixo ⇒ ``default``)."""
    value = _get(settings, name)
    if value is None or isinstance(value, bool):
        return default
    try:
        return kind(value)
    except (TypeError, ValueError):
        return default


# ======================================================================= watchdog


class SessionWatchdog:
    """Progresso líquido da sessão + escada ``replan → force_explore → pause_and_ask``.

    Uso (no loop, **depois** do ``StuckDetector``)::

        wd = SessionWatchdog()
        esc = wd.observe(state, step=index, executed=result.executed,
                         human_answers=answers_so_far)
        if esc is not None:
            ...  # publicar "session_watchdog"; replan / force_explore / pausar+perguntar

    Args:
        no_progress_steps: passos sem progresso até ao 1.º degrau (``<= 0``
            desliga o critério por passos).
        no_progress_s: idem em segundos (``<= 0`` desliga o critério de tempo).
        rung_fraction: fração da janela base que cada degrau seguinte acrescenta.
        idle_gap_s: intervalo entre observações que conta como pausa/retoma.
        clock: ``() -> float`` em segundos (monotónico); injetável em testes.
        wall_clock: ``() -> str`` ISO-8601 para ``Escalation.ts``.

    Com ambos os critérios desligados o watchdog está **desativado** e
    :meth:`observe` devolve sempre ``None``.
    """

    def __init__(
        self,
        no_progress_steps: int = NO_PROGRESS_STEPS,
        no_progress_s: float = NO_PROGRESS_S,
        *,
        rung_fraction: float = RUNG_FRACTION,
        idle_gap_s: float = IDLE_GAP_S,
        visited_cap: int = VISITED_CAP,
        max_unit_only_streak: int = MAX_UNIT_ONLY_STREAK,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], str] = utc_now_iso,
    ) -> None:
        self.no_progress_steps = max(0, int(no_progress_steps))
        self.no_progress_s = max(0.0, float(no_progress_s))
        self.rung_fraction = min(1.0, max(0.0, float(rung_fraction)))
        self.idle_gap_s = max(0.0, float(idle_gap_s))
        self.visited_cap = max(1, int(visited_cap))
        self.max_unit_only_streak = max(0, int(max_unit_only_streak))
        self._clock = clock
        self._wall_clock = wall_clock
        self._lock = threading.RLock()

        self._visited: OrderedDict[tuple[int, int, int], None] = OrderedDict()
        self._regions: OrderedDict[str, None] = OrderedDict()
        self._actions: OrderedDict[str, None] = OrderedDict()
        self._units: OrderedDict[str, None] = OrderedDict()
        self._kills: int | None = None
        self._human_seen: int | None = None
        self._human_pending = 0
        self._unit_only_streak = 0
        self._history: deque[Escalation] = deque(maxlen=HISTORY_CAP)
        self._last_progress: tuple[str, ...] = ()
        self._last_progress_step: int | None = None
        self._explore_turn = 0
        self._last_seen: float | None = None
        self.observations = 0
        self.progress_events = 0
        self.escalations = 0
        self._reanchor(self._clock())

    @classmethod
    def from_settings(cls, settings: Any = None, **kwargs: Any) -> "SessionWatchdog":
        """Constrói a partir de um mapping/objeto com ``session_no_progress_steps`` e
        ``session_no_progress_s`` (um ``LoopConfig`` ou ``meta.json``); em falta ⇒
        os defaults conservadores.  ``0`` desliga o critério respetivo."""
        return cls(
            _setting(settings, "session_no_progress_steps", NO_PROGRESS_STEPS, int),
            _setting(settings, "session_no_progress_s", NO_PROGRESS_S, float),
            **kwargs,
        )

    # ----------------------------------------------------------------- estado

    @property
    def enabled(self) -> bool:
        return self.no_progress_steps > 0 or self.no_progress_s > 0

    @property
    def round(self) -> int:
        """Degraus já disparados na janela atual (0–3)."""
        return self._round

    @property
    def steps_since_progress(self) -> int:
        return self._steps

    @property
    def latched(self) -> bool:
        """``True`` depois de ``pause_and_ask``: mudo até haver progresso/``acknowledge``."""
        return self._round >= len(LADDER)

    @property
    def history(self) -> tuple[Escalation, ...]:
        return tuple(self._history)

    def seconds_since_progress(self, now: float | None = None) -> float:
        moment = self._clock() if now is None else float(now)
        return max(0.0, moment - self._progress_at)

    def status(self) -> dict[str, Any]:
        """Resumo JSON-friendly (UI/diagnóstico)."""
        with self._lock:
            return {
                "enabled": self.enabled, "steps_since_progress": self._steps,
                "seconds_since_progress": round(self.seconds_since_progress(), 1),
                "round": self._round, "latched": self.latched,
                "no_progress_steps": self.no_progress_steps, "no_progress_s": self.no_progress_s,
                "observations": self.observations, "progress_events": self.progress_events,
                "escalations": self.escalations,
                "last_progress": list(self._last_progress),
                "last_progress_step": self._last_progress_step,
                "last_level": self._history[-1].level if self._history else None,
            }

    # --------------------------------------------------------------- controlo

    def _reanchor(self, now: float) -> None:
        """Recomeça a janela (mantém o que a sessão já viu)."""
        self._progress_at = now
        self._steps = 0
        self._round = 0

    def acknowledge(self) -> None:
        """O humano tratou do assunto: a janela recomeça e o degrau final desarma."""
        with self._lock:
            self._reanchor(self._clock())
            self._unit_only_streak = 0

    def reset(self, *, hard: bool = False) -> None:
        """Recomeça a janela; ``hard`` esquece também tiles/regiões/ações/criaturas."""
        with self._lock:
            self._reanchor(self._clock())
            self._unit_only_streak = 0
            self._last_seen = None
            if hard:
                for table in (self._visited, self._regions, self._actions, self._units):
                    table.clear()
                self._kills = None
                self._human_seen = None
                self._human_pending = 0

    def note_human_answer(self, count: int = 1) -> None:
        """O humano respondeu/aprovou (chamável de qualquer thread).

        Conta como progresso na próxima :meth:`observe` — responder à sessão é o
        oposto de estar parada à espera de ninguém.
        """
        with self._lock:
            self._human_pending += max(0, int(count))

    # --------------------------------------------------------------- observar

    def observe(
        self,
        state: Any,
        step: Any = None,
        executed: Any = None,
        *,
        human_answers: int | None = None,
        action_id: str | None = None,
        now: float | None = None,
    ) -> Escalation | None:
        """Observa UM passo; devolve a escalada a aplicar (ou ``None``).

        ``state`` é o snapshot (``GameState`` ou compatível: só se leem
        ``metrics.location``, ``adventurer.kills``, ``metrics.units_nearby`` e
        ``is_alive``, com tolerância a campos em falta); ``step`` o índice do
        passo (ou um objeto com ``index``; só diagnóstico); ``executed`` o
        ``ExecutionResult`` (ou mapping/``None``); ``human_answers`` o nº
        **acumulado** de respostas humanas (alternativa a
        :meth:`note_human_answer`); ``action_id`` a ação executada se
        ``executed`` não a trouxer.  Nunca levanta: um erro interno vira ``None``.
        """
        try:
            with self._lock:
                return self._observe(state, step, executed, human_answers, action_id, now)
        except Exception:  # a vigilância nunca derruba o loop
            log.debug("session watchdog observe failed", exc_info=True)
            return None

    def _observe(self, state: Any, step: Any, executed: Any, human_answers: int | None,
                 action_id: str | None, now: float | None) -> Escalation | None:
        if not self.enabled:
            return None
        moment = self._clock() if now is None else float(now)
        self.observations += 1
        resumed = False
        if (self.idle_gap_s > 0 and self._last_seen is not None
                and moment - self._last_seen > self.idle_gap_s):
            # Pausa/retoma (ou o humano a decidir): a janela recomeça — não é culpa
            # da sessão que ninguém tenha chamado ``observe`` durante minutos.
            self._reanchor(moment)
            self._unit_only_streak = 0
            resumed = True
        self._last_seen = moment
        if _get(state, "is_alive", True) is False:
            return None  # morte: o StuckDetector manda parar; nada a escalar aqui

        signals = self._signals(state, executed, action_id, human_answers)
        if signals:
            self._on_progress(moment, step, signals)
            return None
        if resumed:
            return None  # a observação da retoma é o ponto de partida, não um passo parado
        self._steps += 1
        return self._escalate(moment)

    # ---------------------------------------------------------------- sinais

    @staticmethod
    def _novel(table: "OrderedDict[Any, None]", key: Any, cap: int) -> bool:
        if key in table:
            return False
        table[key] = None
        while len(table) > cap:
            table.popitem(last=False)
        return True

    def _signals(self, state: Any, executed: Any, action_id: str | None,
                 human_answers: int | None) -> tuple[str, ...]:
        """Os sinais de progresso desta observação (``()`` = nenhum)."""
        metrics = _get(state, "metrics")
        location = _get(metrics, "location")
        adventurer = _get(state, "adventurer")

        new_tile = False
        x, y, z = (_int_or_none(_get(location, axis, _get(adventurer, axis)))
                   for axis in ("x", "y", "z"))
        if x is not None and y is not None:  # z ausente conta como 0 (mapas planos)
            new_tile = self._novel(self._visited, (x, y, z if z is not None else 0),
                                   self.visited_cap)

        new_region = False
        label = "|".join(_text(_get(location, name)) for name in ("region", "site"))
        if label.strip("|"):
            new_region = self._novel(self._regions, label, REGIONS_CAP)

        kill = False
        kills = _int_or_none(_get(adventurer, "kills"))
        if kills is not None:
            kill = self._kills is not None and kills > self._kills
            self._kills = kills if self._kills is None else max(self._kills, kills)

        new_unit = False
        units = list(_get(_get(metrics, "units_nearby"), "units", ()) or ())
        units += list(_get(_get(metrics, "threats"), "hostiles", ()) or ())
        for unit in units:
            name = _text(_get(unit, "name")) or _text(_get(unit, "race"))
            if name and self._novel(self._units, name.lower(), UNITS_CAP):
                new_unit = True

        action = False
        ident = _text(action_id) or _text(_get(executed, "action_id"))
        if executed is not None and ident:
            status = _text(_get(executed, "status"))
            ran = _get(executed, "ok", None) is not False and status in ("", "executed")
            if ran and _action_verb(ident) not in NON_PROGRESS_VERBS:
                action = self._novel(self._actions, ident, ACTIONS_CAP)

        human = False
        if human_answers is not None:
            total = max(0, int(human_answers))
            if self._human_seen is not None and total > self._human_seen:
                human = True
            self._human_seen = total if self._human_seen is None else max(self._human_seen, total)
        if self._human_pending:
            human, self._human_pending = True, 0

        strong = new_tile or new_region or kill or action or human
        # Criatura nova SÓ: aceita no máximo ``max_unit_only_streak`` vezes seguidas.
        if new_unit and not strong:
            self._unit_only_streak += 1
            new_unit = self._unit_only_streak <= self.max_unit_only_streak
        elif strong:
            self._unit_only_streak = 0
        flags = (("new_tile", new_tile), ("new_region", new_region), ("kill", kill),
                 ("new_unit", new_unit), ("action", action), ("human", human))
        return tuple(name for name, on in flags if on)

    def _on_progress(self, now: float, step: Any, signals: tuple[str, ...]) -> None:
        self.progress_events += 1
        self._progress_at = now
        self._steps = 0
        self._round = 0
        self._last_progress = signals
        self._last_progress_step = _int_or_none(_get(step, "index", step))

    # --------------------------------------------------------------- escada

    def _rung_limits(self, rung: int) -> tuple[int, float]:
        """``(passos, segundos)`` de ``rung`` (0 = 1.º degrau); ``0`` = critério off."""
        steps = self.no_progress_steps
        seconds = self.no_progress_s
        if steps > 0:
            steps += rung * max(1, int(round(steps * self.rung_fraction)))
        if seconds > 0:
            seconds += rung * seconds * self.rung_fraction
        return steps, seconds

    def _escalate(self, now: float) -> Escalation | None:
        if self.latched:
            return None
        steps_limit, seconds_limit = self._rung_limits(self._round)
        elapsed = max(0.0, now - self._progress_at)
        by_steps = steps_limit > 0 and self._steps >= steps_limit
        by_time = seconds_limit > 0 and elapsed >= seconds_limit
        if not (by_steps or by_time):
            return None
        level = LADDER[self._round]
        self._round += 1
        self.escalations += 1
        direction: str | None = None
        if level == LEVEL_FORCE_EXPLORE:
            direction = DIRECTIONS[self._explore_turn % len(DIRECTIONS)]
            self._explore_turn += 1
        reason = "no_progress_steps" if by_steps else "no_progress_time"
        event = self._build(level, reason, elapsed, direction)
        self._history.append(event)
        log.warning("session watchdog: %s", event.detail)
        return event

    def _build(self, level: str, reason: str, elapsed: float, direction: str | None) -> Escalation:
        steps = self._steps
        minutes = _minutes(elapsed)
        recent = ", ".join(self._last_progress) or "nothing yet"
        detail = (
            f"no session progress for {steps} steps / {elapsed:.0f}s "
            f"(last progress: {recent}); escalation {self._round}/{len(LADDER)} = {level}"
        )
        if level == LEVEL_REPLAN:
            message = (f"A sessão está sem progresso há {steps} passos (~{minutes} min): "
                       "vou pedir um plano novo ao agente.")
        elif level == LEVEL_FORCE_EXPLORE:
            message = (f"Continua sem progresso há {steps} passos (~{minutes} min): "
                       f"vou forçar a exploração para {direction or 'um rumo novo'}.")
        else:
            message = (
                f"A sessão parece parada: {steps} passos (~{minutes} min) sem nova posição, "
                "região, abate, criatura nova nem resposta tua. Pausei o loop — o que devo fazer?"
            )
        return Escalation(
            level=level, round=self._round, reason=reason, steps_since_progress=steps,
            seconds_since_progress=elapsed, message_pt=message, detail=detail,
            direction=direction, last_progress=self._last_progress,
            options=PAUSE_OPTIONS if level == LEVEL_PAUSE_AND_ASK else (),
            ts=self._wall_clock(),
        )


# ====================================================================== health


@dataclass(frozen=True)
class RestartDecision:
    """O que o ``LoopRunner`` deve fazer quando a thread do loop morre."""

    #: ``restart`` | ``give_up`` | ``none`` (paragem pedida — nada a fazer).
    action: str
    #: Espera antes de reiniciar (segundos); ``0`` fora de ``restart``.
    delay_s: float = 0.0
    #: Nº desta tentativa (1-based) — ``0`` fora de ``restart``/``give_up``.
    attempt: int = 0
    max_restarts: int = MAX_RESTARTS
    reason: str = ""
    message_pt: str = ""

    @property
    def restarts(self) -> bool:
        return self.action == ACTION_RESTART

    def as_dict(self) -> dict[str, Any]:
        return {"action": self.action, "delay_s": self.delay_s, "attempt": self.attempt,
                "max_restarts": self.max_restarts, "reason": self.reason,
                "message_pt": self.message_pt}


class LoopHealth:
    """Política de reinício com backoff para a thread do loop (lógica pura).

    O ``LoopRunner`` chama :meth:`on_started` quando arranca a thread e
    :meth:`on_died` quando ela termina; a :class:`RestartDecision` diz se, e daqui
    a quanto tempo, reiniciar.  Escada: tentativa 1 → 5 s, 2 → 15 s, 3 → 45 s,
    depois ``give_up`` (o humano retoma pelo controlo — :meth:`reset`).

    Uma paragem **pedida** (``requested=True``: kill-switch/pausa/shutdown) nunca
    reinicia e zera a contagem; um loop que correu ``healthy_after_s`` antes de
    morrer também recomeça a contagem (é uma falha nova, não um *crash loop*).
    """

    def __init__(
        self,
        max_restarts: int = MAX_RESTARTS,
        backoff_s: Sequence[float] = BACKOFF_S,
        *,
        healthy_after_s: float = HEALTHY_AFTER_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_restarts = max(0, int(max_restarts))
        steps = tuple(max(0.0, float(value)) for value in backoff_s) or BACKOFF_S
        self.backoff_s: tuple[float, ...] = steps
        self.healthy_after_s = max(0.0, float(healthy_after_s))
        self._clock = clock
        self._lock = threading.Lock()
        self._started_at: float | None = None
        self.restarts = 0
        self.exhausted = False
        self.last_error: str | None = None
        self.deaths = 0

    @classmethod
    def from_settings(cls, settings: Any = None, **kwargs: Any) -> "LoopHealth":
        """Constrói a partir de ``loop_max_restarts`` (mapping/objeto); em falta ⇒ 3."""
        return cls(_setting(settings, "loop_max_restarts", MAX_RESTARTS, int), **kwargs)

    def delay_for(self, attempt: int) -> float:
        """Espera da tentativa ``attempt`` (1-based; satura no último degrau)."""
        index = min(max(1, int(attempt)), len(self.backoff_s)) - 1
        return self.backoff_s[index]

    def on_started(self) -> None:
        """A thread do loop arrancou (marca o início para medir se ficou saudável)."""
        with self._lock:
            self._started_at = self._clock()

    def reset(self) -> None:
        """Recomeça (o humano retomou o loop pelo controlo)."""
        with self._lock:
            self.restarts = 0
            self.exhausted = False
            self.last_error = None

    def on_died(self, error: Any = None, *, requested: bool = False) -> RestartDecision:
        """A thread terminou: decide.  Nunca levanta.

        Args:
            error: a exceção/texto com que morreu (``None`` = terminou sozinha).
            requested: ``True`` quando a paragem foi pedida (stop/kill/shutdown).
        """
        with self._lock:
            now = self._clock()
            ran = None if self._started_at is None else max(0.0, now - self._started_at)
            self._started_at = None
            self.deaths += 1
            if requested:
                self.restarts = 0
                self.exhausted = False
                return RestartDecision(
                    ACTION_NONE, max_restarts=self.max_restarts, reason="stop_requested",
                    message_pt="Paragem pedida: o loop não será reiniciado.",
                )
            self.last_error = _clean_error(error) or None
            if ran is not None and ran >= self.healthy_after_s:
                self.restarts = 0  # correu bem tempo suficiente: falha nova, não crash loop
                self.exhausted = False
            cause = f" ({self.last_error})" if self.last_error else ""
            if self.restarts >= self.max_restarts:
                self.exhausted = True
                falls = self.restarts + 1
                return RestartDecision(
                    ACTION_GIVE_UP, attempt=self.restarts, max_restarts=self.max_restarts,
                    reason="restarts_exhausted",
                    message_pt=(
                        f"O loop caiu {falls} {'vez' if falls == 1 else 'vezes seguidas'}{cause}: "
                        "não será reiniciado sozinho. Vê o erro e retoma pelo painel de controlo."
                    ),
                )
            self.restarts += 1
            delay = self.delay_for(self.restarts)
            return RestartDecision(
                ACTION_RESTART, delay_s=delay, attempt=self.restarts,
                max_restarts=self.max_restarts, reason="worker_died",
                message_pt=(
                    f"O loop caiu{cause}: a reiniciar em {delay:g} s "
                    f"(tentativa {self.restarts}/{self.max_restarts})."
                ),
            )

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "restarts": self.restarts, "max_restarts": self.max_restarts,
                "exhausted": self.exhausted, "deaths": self.deaths,
                "last_error": self.last_error,
                "next_delay_s": None if self.restarts >= self.max_restarts
                else self.delay_for(self.restarts + 1),
            }
