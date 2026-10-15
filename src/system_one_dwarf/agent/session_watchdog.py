"""``GlobalSessionWatchdog`` — o watchdog **global** da sessão (Passo 18).

Porquê: o :class:`~system_one_dwarf.agent.stuck.StuckDetector` protege o
*personagem* (3 validações com o mesmo hash); ninguém protege a **sessão**.  As
falhas mais caras do projecto foram *silenciosas*: overlays a engolir input com
teclas a reportar ``ok:true`` e o tick congelado (PROBLEMAS §D18), aprovações sem
execução (§E26), loops em pausa — sem exceções, sem erros: só **«o estado não
muda»** denuncia.  Em ambiente sem erros, **o progresso é o oráculo** — e é
exactamente isso que este módulo mede, estendendo a lógica do ``StuckDetector``
ao loop inteiro.

Lógica **pura** (sem I/O nem threads próprias; relógio injetável, nunca levanta):

* **Progresso** = *hash de estado* novo
  (:func:`stuck.state_fingerprint`, reutilizado por composição) ou resposta
  humana.  Os **eventos existentes** (``action_executed``, decisões, gastos)
  entram por composição/leitura de API (:class:`SessionActivity`) como
  *diagnóstico*: contam o que a sessão andou a fazer **sem** mudar o estado — é
  essa assinatura que classifica o bloqueio.
* **Escada** ``replan → ask_human → kill_switch`` (configurável por campanha via
  ``SOD_WATCHDOG_*`` / meta ``watchdog``, ver
  :func:`agent.config.watchdog_settings`): sem progresso durante a janela N,
  cada estágio dispara **uma vez**, com ``stage_step_min`` entre degraus.
* **Janela de graça**: nunca dispara antes de ``grace_min`` passados sobre a
  última *ativação* (arranque, retoma, ``acknowledge``, resposta humana) —
  absorve pausas legítimas do jogador mesmo quando a janela N já passou.
* **Nunca** dispara em ``paused`` nem com *kill-switch* ativo (o tempo em pausa
  não conta: a janela re-ancora); um gap de observações maior que
  ``idle_gap_s`` é pausa/retoma, não estagnação.
* Cada disparo emite um :class:`SessionStallEvent` pelo callback ``on_event``
  (estruturado, ``as_dict()``/``as_stuck_event()``) — o *wiring* liga-o à
  biblioteca de padrões **por API** (:func:`stuck.append_pattern_event`); este
  módulo nunca escreve em disco.

Uso (no loop, a cada passo — ver o snippet de *wiring* no relatório)::

    wd = GlobalSessionWatchdog(
        WatchdogConfig.from_settings(watchdog_settings(meta, os.environ)),
        on_event=lambda ev: append_pattern_event(event=ev.as_stuck_event()),
        activity_source=lambda: SessionActivity.from_sources(
            executed=..., decisions=runtime.decisions, spend=ledger.total_usd),
    )
    event = wd.observe(state, control=control)
    if event is not None:
        ...  # event.stage == "replan" | "ask_human" | "kill_switch"

Decisões: o oráculo é o hash (não os contadores — ações ``ok:true`` com o tick
congelado são o caso D18 e *não* podem contar como progresso); a graça é um
piso temporal ancorado na ativação (não soma à janela, para «detetada e
escalada em ≤N minutos» valer com as omissões); estágios conhecidos e fixos
(nunca inventados pelo modelo); tudo determinístico e limitado (história FIFO,
conjunto de hashes limitado).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .stuck import StuckEvent, state_fingerprint
from .types import MODE_PAUSED, utc_now_iso

log = logging.getLogger("system_one_dwarf.agent.session_watchdog")

__all__ = [
    "ASK_OPTIONS",
    "DEFAULT_GRACE_MIN",
    "DEFAULT_WINDOW_MIN",
    "KNOWN_STAGES",
    "LADDER",
    "STAGE_ASK_HUMAN",
    "STAGE_KILL_SWITCH",
    "STAGE_REPLAN",
    "STALL_APPROVAL_WITHOUT_EXECUTION",
    "STALL_DECIDING_WITHOUT_EXECUTION",
    "STALL_EXECUTING_WITHOUT_STATE_CHANGE",
    "STALL_KINDS",
    "STALL_SILENT_LOOP",
    "GlobalSessionWatchdog",
    "SessionActivity",
    "SessionStallEvent",
    "WatchdogConfig",
]

#: Os estágios da escada, por ordem (re-plano → pergunta PT → kill-switch).
STAGE_REPLAN = "replan"
STAGE_ASK_HUMAN = "ask_human"
STAGE_KILL_SWITCH = "kill_switch"
LADDER: tuple[str, ...] = (STAGE_REPLAN, STAGE_ASK_HUMAN, STAGE_KILL_SWITCH)
KNOWN_STAGES = frozenset(LADDER)

#: Minutos sem progresso até ao 1.º estágio (janela N do Passo 18).
DEFAULT_WINDOW_MIN = 15.0
#: Minutos de **janela de graça** (piso após cada ativação; 0 desliga o piso).
DEFAULT_GRACE_MIN = 2.0
#: Gap de observações a partir do qual é pausa/retoma, não estagnação (s).
DEFAULT_IDLE_GAP_S = 300.0
#: Hashes distintos lembrados (FIFO) — voltar a um hash muito antigo conta como novo.
SEEN_CAP = 2048
#: Escalações lembradas para diagnóstico.
HISTORY_CAP = 20

# ------------------------------------------------------- assinaturas do bloqueio
#: Ações executaram-se mas o estado não mudou — assinatura do D18 (input engolido).
STALL_EXECUTING_WITHOUT_STATE_CHANGE = "executing_without_state_change"
#: Houve perguntas/aprovações mas nada executou — assinatura do E26.
STALL_APPROVAL_WITHOUT_EXECUTION = "approval_without_execution"
#: Decisões/gastos sem execução (ex.: JEV a abstir-se em loop).
STALL_DECIDING_WITHOUT_EXECUTION = "deciding_without_execution"
#: Nada se mexeu: loop em pausa/morto ou jogo congelado.
STALL_SILENT_LOOP = "silent_loop"
STALL_KINDS: frozenset[str] = frozenset({
    STALL_EXECUTING_WITHOUT_STATE_CHANGE,
    STALL_APPROVAL_WITHOUT_EXECUTION,
    STALL_DECIDING_WITHOUT_EXECUTION,
    STALL_SILENT_LOOP,
})

#: Vocabulário da biblioteca de padrões para cada estágio (``StuckEvent.escalation``).
_STUCK_ESCALATION: Mapping[str, str] = {
    STAGE_REPLAN: "replan",
    STAGE_ASK_HUMAN: "break_loop",
    STAGE_KILL_SWITCH: "stop",
}

#: Opções PT da pergunta do estágio ``ask_human`` (ids estáveis para o loop/UI).
ASK_OPTIONS: tuple[dict[str, str], ...] = (
    {"id": "continue", "text": "Continuar a tentar"},
    {"id": "replan", "text": "Pedir um plano novo ao agente"},
    {"id": "stop", "text": "Parar a sessão (kill-switch)"},
)


# ============================================================== leitores tolerantes


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "y", "sim")
    return False


def _bool_or(value: Any, default: bool) -> bool:
    """Booleano tolerante: lixo (não booleano reconhecível) ⇒ ``default``."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on", "y", "sim"):
            return True
        if text in ("0", "false", "no", "off", "n", "nao", "não", ""):
            return False
    return default


def _count(value: Any) -> int:
    """Contador a partir de int | sequência | mapping | callable | objeto com ``__len__``."""
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if callable(value):
        try:
            return _count(value())
        except Exception:
            return 0
    if isinstance(value, (Mapping, Sequence)) and not isinstance(value, (str, bytes)):
        return len(value)
    length = getattr(value, "__len__", None)
    if callable(length):
        try:
            return max(0, int(length()))
        except Exception:
            return 0
    return 0


def _money(value: Any) -> float:
    """Dinheiro a partir de float | callable | objeto com ``total_usd``."""
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    if callable(value):
        try:
            return _money(value())
        except Exception:
            return 0.0
    return _money(_get(value, "total_usd", 0.0))


def _is_number(value: Any) -> bool:
    """``True`` para int/float (não bool) ou texto numérico — para separar 0 de lixo."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        try:
            float(value.strip())
            return True
        except ValueError:
            return False
    return False


def _float_or(value: Any, default: float) -> float:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _minutes(seconds: float) -> float:
    return round(max(0.0, seconds) / 60.0, 1)


# ======================================================================= tipos


@dataclass(frozen=True)
class WatchdogConfig:
    """Configuração por campanha (minutos); ``stage_step_min=None`` ⇒ metade da janela.

    ``window_min <= 0`` ou ``enabled=False`` desativa o watchdog (nunca dispara).
    """

    window_min: float = DEFAULT_WINDOW_MIN
    grace_min: float = DEFAULT_GRACE_MIN
    stage_step_min: float | None = None
    stages: tuple[str, ...] = LADDER
    enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "window_min", max(0.0, float(self.window_min)))
        object.__setattr__(self, "grace_min", max(0.0, float(self.grace_min)))
        step = self.window_min / 2.0 if self.stage_step_min is None else float(self.stage_step_min)
        object.__setattr__(self, "stage_step_min", max(0.0, step))
        stages = tuple(self.stages)
        if not stages or any(stage not in KNOWN_STAGES for stage in stages):
            raise ValueError(f"unknown stages {stages!r}; expected a subset of {LADDER}")
        object.__setattr__(self, "stages", stages)
        object.__setattr__(self, "enabled", bool(self.enabled))

    @property
    def active(self) -> bool:
        """``True`` quando há janela configurada e o watchdog está ligado."""
        return bool(self.enabled) and self.window_min > 0

    @property
    def window_s(self) -> float:
        return self.window_min * 60.0

    @property
    def grace_s(self) -> float:
        return self.grace_min * 60.0

    @property
    def stage_step_s(self) -> float:
        return float(self.stage_step_min or 0.0) * 60.0

    @classmethod
    def from_settings(cls, settings: Any = None, **overrides: Any) -> "WatchdogConfig":
        """Constrói a partir de um mapping/objeto (``agent.config.watchdog_settings``);
        lixo ⇒ omissão (como ``SessionWatchdog.from_settings`` do módulo H3)."""
        values: dict[str, Any] = {}
        for name in ("window_min", "grace_min", "stage_step_min", "stages", "enabled"):
            raw = _get(settings, name)
            if raw is not None:
                values[name] = raw
        values.update({k: v for k, v in overrides.items() if v is not None})
        step = values.get("stage_step_min")
        enabled = values.get("enabled", True)
        return cls(
            window_min=_float_or(values.get("window_min"), DEFAULT_WINDOW_MIN),
            grace_min=_float_or(values.get("grace_min"), DEFAULT_GRACE_MIN),
            stage_step_min=None if step is None or not _is_number(step) else _float_or(step, 0.0),
            stages=_stages_or(values.get("stages"), LADDER),
            enabled=_bool_or(enabled, True),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "window_min": self.window_min, "grace_min": self.grace_min,
            "stage_step_min": self.stage_step_min, "stages": list(self.stages),
            "enabled": self.enabled,
        }


def _stages_or(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    """Estágios a partir de ``"replan,ask_human"`` | sequência; desconhecidos caem fora."""
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace(";", ",").split(",")]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        parts = [str(p).strip() for p in value]
    else:
        return default
    kept = tuple(dict.fromkeys(p for p in parts if p in KNOWN_STAGES))
    return kept or default


@dataclass(frozen=True)
class SessionActivity:
    """Retrato dos **eventos existentes** que a sessão produziu (contadores).

    Lidos por composição/leitura de API — nunca por I/O: o chamador passa o que
    já tem (``action_executed``, decisões, perguntas, aprovações, gastos,
    respostas humanas).  Nada disto conta como *progresso* (só o hash de estado
    e o humano contam); serve para **classificar** o bloqueio e enriquecer o
    evento que alimenta a biblioteca de padrões.
    """

    actions_executed: int = 0
    decisions: int = 0
    questions: int = 0
    approvals: int = 0
    spend_usd: float = 0.0
    human_answers: int = 0

    @classmethod
    def from_any(cls, value: Any) -> "SessionActivity":
        """De ``SessionActivity`` | mapping (aliases ``executed``/``total_usd``/…) | ``None``."""
        if isinstance(value, cls):
            return value
        if value is None:
            return cls()
        return cls.from_sources(
            executed=_get(value, "actions_executed", _get(value, "executed")),
            decisions=_get(value, "decisions"),
            questions=_get(value, "questions"),
            approvals=_get(value, "approvals"),
            spend=_get(value, "spend_usd", _get(value, "total_usd", _get(value, "spend"))),
            human_answers=_get(value, "human_answers", _get(value, "answers")),
        )

    @classmethod
    def from_sources(
        cls,
        *,
        executed: Any = None,
        decisions: Any = None,
        questions: Any = None,
        approvals: Any = None,
        spend: Any = None,
        human_answers: Any = None,
    ) -> "SessionActivity":
        """Contadores a partir das APIs existentes (tudo tolerante: int | lista |
        dict | callable | objeto com ``total_usd``/``__len__``)."""
        return cls(
            actions_executed=_count(executed),
            decisions=_count(decisions),
            questions=_count(questions),
            approvals=_count(approvals),
            spend_usd=_money(spend),
            human_answers=_count(human_answers),
        )

    def minus(self, older: "SessionActivity") -> "SessionActivity":
        """Delta desde ``older`` (contadores que só crescem; regressos contam 0)."""
        return SessionActivity(
            actions_executed=max(0, self.actions_executed - older.actions_executed),
            decisions=max(0, self.decisions - older.decisions),
            questions=max(0, self.questions - older.questions),
            approvals=max(0, self.approvals - older.approvals),
            spend_usd=round(max(0.0, self.spend_usd - older.spend_usd), 6),
            human_answers=max(0, self.human_answers - older.human_answers),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "actions_executed": self.actions_executed, "decisions": self.decisions,
            "questions": self.questions, "approvals": self.approvals,
            "spend_usd": self.spend_usd, "human_answers": self.human_answers,
        }


@dataclass(frozen=True)
class SessionStallEvent:
    """Evento estruturado por disparo — alimenta a biblioteca de padrões (via API).

    ``as_stuck_event()`` devolve o mesmo evento no vocabulário do
    :class:`stuck.StuckEvent` (``replan→replan``, ``ask_human→break_loop``,
    ``kill_switch→stop``) para ``stuck.append_pattern_event``; ``as_question()``
    é a pergunta PT do estágio ``ask_human`` na forma da fila (``docs/api.md``).
    """

    stage: str
    round: int
    reason: str = "no_progress"
    stall_kind: str = STALL_SILENT_LOOP
    minutes_without_progress: float = 0.0
    window_min: float = 0.0
    grace_min: float = 0.0
    state_hash: str = ""
    activity: SessionActivity = field(default_factory=SessionActivity)
    last_progress: tuple[str, ...] = ()
    message_pt: str = ""
    detail: str = ""
    ts: str = ""

    @property
    def escalation(self) -> str:
        """Alias de :attr:`stage` (vocabulário interno do watchdog)."""
        return self.stage

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage, "round": self.round, "reason": self.reason,
            "stall_kind": self.stall_kind,
            "minutes_without_progress": self.minutes_without_progress,
            "window_min": self.window_min, "grace_min": self.grace_min,
            "state_hash": self.state_hash,
            "activity": self.activity.as_dict(),
            "last_progress": list(self.last_progress),
            "message_pt": self.message_pt, "detail": self.detail, "ts": self.ts,
        }

    def as_stuck_event(self) -> StuckEvent:
        """O mesmo disparo no vocabulário da biblioteca de padrões (``stuck_events``)."""
        return StuckEvent(
            "stuck", _STUCK_ESCALATION.get(self.stage, "stop"),
            f"session watchdog [{self.stall_kind}]: {self.detail}",
            state_hash=self.state_hash, repeat=self.round, stuck_round=self.round,
            ts=self.ts or utc_now_iso(),
        )

    def as_question(self, question_id: str) -> dict[str, Any] | None:
        """A pergunta PT do estágio ``ask_human`` (``None`` nos outros estágios).

        Na forma da fila (``question_new`` / ``docs/api.md``): campos do contrato
        + proveniência aditiva; sem ``action_id`` — é uma dúvida, nunca uma
        aprovação.
        """
        if self.stage != STAGE_ASK_HUMAN:
            return None
        return {
            "id": question_id,
            "ts": self.ts or utc_now_iso(),
            "question": self.message_pt,
            "options": [dict(option) for option in ASK_OPTIONS],
            "free_text_allowed": True,
            "status": "pending",
            "format": "multiple",
            "why_pt": "o watchdog global da sessão não viu progresso: o loop foi "
                      "pausado para não gastar em vão",
            "band": "abstain",
            "role": "JEV-D",
            "source_data": {
                "reason": self.reason, "band": "abstain", "stall_kind": self.stall_kind,
                "minutes_without_progress": self.minutes_without_progress,
                "state_hash": self.state_hash,
                **self.activity.as_dict(),
            },
        }


# ================================================================== o watchdog


class GlobalSessionWatchdog:
    """Progresso líquido da sessão + escada ``replan → ask_human → kill_switch``.

    Uso (no loop, **depois** do ``StuckDetector``)::

        wd = GlobalSessionWatchdog(config, on_event=sink)
        event = wd.observe(state, control=control)
        if event is not None:
            ...  # aplicar o estágio (re-plano / perguntar / kill-switch)

    Args:
        config: :class:`WatchdogConfig` (ou mapping/objeto para
            :meth:`WatchdogConfig.from_settings`).
        on_event: recebe cada :class:`SessionStallEvent` (a ligação à biblioteca
            de padrões faz-se aqui: ``append_pattern_event(event=ev.as_stuck_event())``).
        activity_source: leitura dos contadores dos eventos existentes
            (``() -> SessionActivity | mapping``); ``observe(activity=…)`` tem
            precedência.
        idle_gap_s: gap de observações a partir do qual é pausa/retoma.
        clock/wall_clock: relógios injetáveis (monotónico / ISO-8601).
    """

    def __init__(
        self,
        config: WatchdogConfig | Mapping[str, Any] | None = None,
        *,
        on_event: Callable[[SessionStallEvent], Any] | None = None,
        activity_source: Callable[[], Any] | None = None,
        idle_gap_s: float = DEFAULT_IDLE_GAP_S,
        seen_cap: int = SEEN_CAP,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], str] = utc_now_iso,
    ) -> None:
        self.config = config if isinstance(config, WatchdogConfig) else WatchdogConfig.from_settings(config)
        self._on_event = on_event
        self._activity_source = activity_source
        self.idle_gap_s = max(0.0, float(idle_gap_s))
        self.seen_cap = max(1, int(seen_cap))
        self._clock = clock
        self._wall_clock = wall_clock
        self._lock = threading.RLock()
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._history: deque[SessionStallEvent] = deque(maxlen=HISTORY_CAP)
        self.observations = 0
        self.progress_events = 0
        self.escalations = 0
        self.suppressed = 0
        self._human_seen: int | None = None
        self._human_pending = 0
        self._last_seen: float | None = None
        self._last_progress: tuple[str, ...] = ()
        self._anchor_activity = SessionActivity()
        self._done = False
        self._reanchor(self._clock(), grace=True)

    # -------------------------------------------------------------- estado

    @property
    def round(self) -> int:
        """Estágios já disparados na janela atual (0..len(stages))."""
        return self._round

    @property
    def latched(self) -> bool:
        """``True`` depois do último estágio: mudo até haver progresso/``acknowledge``."""
        return self._done or self._round >= len(self.config.stages)

    @property
    def history(self) -> tuple[SessionStallEvent, ...]:
        return tuple(self._history)

    def seconds_since_progress(self, now: float | None = None) -> float:
        moment = self._clock() if now is None else float(now)
        return max(0.0, moment - self._progress_at)

    def status(self) -> dict[str, Any]:
        """Resumo JSON-friendly (UI/diagnóstico)."""
        with self._lock:
            now = self._clock()
            return {
                "enabled": self.config.active, "round": self._round, "latched": self.latched,
                "stages": list(self.config.stages),
                "window_min": self.config.window_min, "grace_min": self.config.grace_min,
                "stage_step_min": self.config.stage_step_min,
                "seconds_since_progress": round(self.seconds_since_progress(now), 1),
                "minutes_without_progress": _minutes(self.seconds_since_progress(now)),
                "grace_remaining_s": round(max(0.0, self._armed_at + self.config.grace_s - now), 1),
                "last_progress": list(self._last_progress),
                "observations": self.observations, "progress_events": self.progress_events,
                "escalations": self.escalations, "suppressed": self.suppressed,
                "last_stage": self._history[-1].stage if self._history else None,
            }

    # -------------------------------------------------------------- controlo

    def _reanchor(self, now: float, *, grace: bool, activity: SessionActivity | None = None,
                  clear_latch: bool = True) -> None:
        """Recomeça a janela; ``grace`` também re-armazena a janela de graça.

        ``clear_latch=False`` (pausa/retoma) mantém o trava do último estágio —
        só o progresso, o ``acknowledge`` ou o ``reset`` desarmam a escada.
        """
        self._progress_at = now
        if grace:
            self._armed_at = now
        self._round = 0
        if clear_latch:
            self._done = False
        if activity is not None:
            self._anchor_activity = activity

    def acknowledge(self) -> None:
        """O humano tratou do assunto: janela e graça recomeçam, a escada desarma."""
        with self._lock:
            self._reanchor(self._clock(), grace=True)

    def reset(self, *, hard: bool = False) -> None:
        """Recomeça (arranque/retoma do loop); ``hard`` esquece os hashes vistos."""
        with self._lock:
            self._reanchor(self._clock(), grace=True)
            self._last_seen = None
            self._human_pending = 0
            if hard:
                self._seen.clear()
                self._human_seen = None
                self._anchor_activity = SessionActivity()

    def note_human_answer(self, count: int = 1) -> None:
        """O humano respondeu/aprovou (chamável de qualquer thread).

        Conta como progresso na próxima :meth:`observe` **e** re-armazena a
        janela de graça — quem está a interagir merece o benefício da dúvida.
        """
        with self._lock:
            self._human_pending += max(0, int(count))

    # -------------------------------------------------------------- observar

    def observe(
        self,
        state: Any = None,
        *,
        control: Any = None,
        activity: SessionActivity | Mapping[str, Any] | None = None,
        human_answers: int | None = None,
        now: float | None = None,
    ) -> SessionStallEvent | None:
        """Observa UM passo; devolve o disparo a aplicar (ou ``None``).

        ``state`` é o snapshot (só se lê o hash via ``stuck.state_fingerprint``);
        ``control`` o estado vivo (``ControlState`` ou ``{mode, kill_switch}``) —
        em ``paused`` ou com *kill-switch* **nunca** dispara; ``activity`` os
        contadores dos eventos existentes (omissão: ``activity_source``);
        ``human_answers`` o nº **acumulado** de respostas humanas.  Nunca levanta.
        """
        try:
            with self._lock:
                return self._observe(state, control, activity, human_answers, now)
        except Exception:  # a vigilância nunca derruba o loop
            log.debug("session watchdog observe failed", exc_info=True)
            return None

    def _observe(self, state: Any, control: Any, activity: Any, human_answers: int | None,
                 now: float | None) -> SessionStallEvent | None:
        if not self.config.active:
            return None
        moment = self._clock() if now is None else float(now)
        self.observations += 1
        current = self._read_activity(activity)

        # -- pausa/kill-switch: nunca dispara, e o tempo não conta ----------
        if self._suppressed(control):
            self.suppressed += 1
            self._reanchor(moment, grace=True, activity=current, clear_latch=False)
            self._last_seen = moment
            return None
        # -- gap de observações = retoma, não estagnação --------------------
        if (self._last_seen is not None and self.idle_gap_s > 0
                and moment - self._last_seen > self.idle_gap_s):
            self._reanchor(moment, grace=True, activity=current, clear_latch=False)
            self._last_seen = moment
            return None
        self._last_seen = moment
        if _get(state, "is_alive", True) is False:
            return None  # morte: o StuckDetector manda parar; nada a escalar aqui

        # -- progresso? (hash novo ou humano) ------------------------------
        signals: list[str] = []
        fingerprint = None
        if state is not None:
            fingerprint = state_fingerprint(state)
            if self._novel(fingerprint):
                signals.append("state")
        if self._human_progress(human_answers):
            signals.append("human")
        if signals:
            self.progress_events += 1
            self._last_progress = tuple(signals)
            self._reanchor(moment, grace="human" in signals, activity=current)
            return None

        # -- escada --------------------------------------------------------
        if self.latched:
            return None
        window_at = self._progress_at + self.config.window_s
        grace_at = self._armed_at + self.config.grace_s
        rung_at = max(window_at, grace_at) + self._round * self.config.stage_step_s
        if moment < rung_at:
            return None
        event = self._build(moment, fingerprint, current)
        self._round += 1
        if self._round >= len(self.config.stages):
            self._done = True  # trava: o último estágio é terminal até progresso
        self.escalations += 1
        self._history.append(event)
        self._emit(event)
        return event

    # --------------------------------------------------------------- sinais

    def _novel(self, key: str) -> bool:
        """``True`` quando este hash de estado nunca foi visto (FIFO limitado)."""
        if key in self._seen:
            return False
        self._seen[key] = None
        while len(self._seen) > self.seen_cap:
            self._seen.popitem(last=False)
        return True

    def _human_progress(self, human_answers: int | None) -> bool:
        human = False
        if human_answers is not None:
            total = max(0, int(human_answers))
            if self._human_seen is not None and total > self._human_seen:
                human = True
            self._human_seen = total if self._human_seen is None else max(self._human_seen, total)
        if self._human_pending:
            human, self._human_pending = True, 0
        return human

    def _read_activity(self, activity: Any) -> SessionActivity:
        if activity is not None:
            return SessionActivity.from_any(activity)
        if self._activity_source is None:
            return SessionActivity()
        try:
            return SessionActivity.from_any(self._activity_source())
        except Exception:
            log.debug("session watchdog activity_source failed", exc_info=True)
            return SessionActivity()

    @staticmethod
    def _suppressed(control: Any) -> bool:
        if control is None:
            return False
        return _truthy(_get(control, "kill_switch")) or _text(_get(control, "mode")).lower() == MODE_PAUSED

    # --------------------------------------------------------------- escada

    @staticmethod
    def _classify(delta: SessionActivity) -> str:
        if delta.actions_executed > 0:
            return STALL_EXECUTING_WITHOUT_STATE_CHANGE
        if delta.questions > 0 or delta.approvals > 0:
            return STALL_APPROVAL_WITHOUT_EXECUTION
        if delta.decisions > 0 or delta.spend_usd > 0:
            return STALL_DECIDING_WITHOUT_EXECUTION
        return STALL_SILENT_LOOP

    def _build(self, moment: float, fingerprint: str | None,
               current: SessionActivity) -> SessionStallEvent:
        stage = self.config.stages[self._round]
        elapsed = max(0.0, moment - self._progress_at)
        delta = current.minus(self._anchor_activity)
        stall_kind = self._classify(delta)
        minutes = _minutes(elapsed)
        recent = ", ".join(self._last_progress) or "nothing yet"
        detail = (
            f"no session progress for {elapsed:.0f}s (last progress: {recent}; "
            f"executed={delta.actions_executed}, decisions={delta.decisions}, "
            f"questions={delta.questions}, approvals={delta.approvals}, "
            f"spend=${delta.spend_usd:.4f}); "
            f"escalation {self._round + 1}/{len(self.config.stages)} = {stage}"
        )
        if stage == STAGE_REPLAN:
            message = (f"A sessão está sem progresso há {minutes:g} min "
                       f"({stall_kind}): vou pedir um plano novo.")
        elif stage == STAGE_ASK_HUMAN:
            message = (f"Sem progresso há {minutes:g} min ({stall_kind}). "
                       "Parei o loop — o que devo fazer?")
        else:
            message = (f"Sem progresso há {minutes:g} min ({stall_kind}) mesmo após "
                       "re-plano e pergunta: vou ativar o kill-switch e parar a sessão.")
        return SessionStallEvent(
            stage=stage, round=self._round + 1, reason="no_progress", stall_kind=stall_kind,
            minutes_without_progress=minutes, window_min=self.config.window_min,
            grace_min=self.config.grace_min, state_hash=fingerprint or "",
            activity=delta, last_progress=self._last_progress,
            message_pt=message, detail=detail, ts=self._wall_clock(),
        )

    def _emit(self, event: SessionStallEvent) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(event)
        except Exception:  # o sink nunca derruba o loop
            log.warning("session watchdog sink failed", exc_info=True)
