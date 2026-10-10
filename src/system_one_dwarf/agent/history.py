"""Histórico recente de decisões, anti-repetição e ordem U-curve do estado.

Mudança 07 de ``ARQUITETURA.md`` §8: o estado do JEV-A só mostra o **presente**,
por isso o agente repetia ações que já tinham falhado (``explore:n`` contra uma
parede, ``flee:s`` abortado pelo guard).  O :class:`~.stuck.StuckDetector`
bloqueia a ação repetida 3× *depois* do facto; o histórico COMPLEMENTA-o:
mostra ao modelo, *antes* de decidir, o que foi tentado e o que resultou.

Contrato: **factual** (o resultado vem do ``ExecutionResult`` e do diff
``before → after`` do ``GameState`` — posição, sangue/dor/feridas, linhas novas
do gamelog, kills — nunca de texto de modelo; o texto do jogo é
*dado*: sem controlo, entre aspas e curto) · **nunca levanta** (lixo ⇒ progresso
``None``) · memória/tempo limitados · offline e determinístico (relógio
injetável) · duck-typed (não importa tipos vizinhos em runtime).

Decisões (finais):

* janela 5, limiar 3 (= ``StuckDetector``, para concordarem) e 12 entradas —
  tudo tunável por constante ``DEFAULT_*`` ou argumento do construtor;
* só ``executed``/``aborted``/``error`` são *tentativas*; ``held``/``question``
  aparecem no bloco recente mas nunca são penalizadas (a aprovação humana
  depende de o modelo voltar a escolher a mesma ação); ``refused`` conta como
  ``error`` falhado (nada foi enviado);
* "sem progresso" depende do verbo (movimento ⇒ a posição mudou; ``rest``/
  ``wait`` nunca falham por isso); sem estados comparáveis o progresso é
  ``None`` e **nunca** marca falha;
* repetição produtiva (sempre com progresso confirmado) não é "não repetir";
  a alternância ``A,B,A,B`` é sempre avisada;
* o estado seguinte só existe no passo N+1: :meth:`DecisionHistory.record`
  regista logo e :meth:`DecisionHistory.settle` refina a última entrada.

Uso no loop::

    history.settle(state)                                   # início do passo N+1
    blocks["failed"], blocks["recent_decisions"] = history.failed_block(), history.recent_block()
    body = trim_to_budget(order_state_blocks(blocks), max_chars - len(header) - 1)
    summary = "\\n".join([header, *body])
    history.record(index, action, band=band, status=status,  # fim do passo N
                   executed=executed, before=state, errors=errors)
"""
from __future__ import annotations

import dataclasses
import logging
import math
import threading
import unicodedata
from collections import Counter, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("system_one_dwarf.agent.history")

__all__ = [
    "ATTEMPT_STATUSES", "BANDS", "DEFAULT_FAILED_CHARS", "DEFAULT_MAXLEN",
    "DEFAULT_RECENT_CHARS", "DEFAULT_RECENT_COUNT", "DEFAULT_REPEAT_THRESHOLD",
    "DEFAULT_WINDOW", "FAILED_HEADER", "HEAD_BLOCKS", "MAX_LOG_LINES",
    "MAX_OUTCOME_CHARS", "MAX_QUOTE_CHARS", "MAX_REASON_CHARS", "MIDDLE_BLOCKS",
    "MIN_BLOCK_CHARS", "MIN_STUB_CHARS", "RECENT_HEADER", "STATUSES", "TAIL_BLOCKS",
    "DecisionHistory", "HistoryEntry", "order_state_blocks", "trim_to_budget",
]

# ------------------------------------------------------------------ constantes

STATUS_EXECUTED, STATUS_HELD, STATUS_QUESTION = "executed", "held", "question"
STATUS_ERROR, STATUS_ABORTED, STATUS_SKIPPED = "error", "aborted", "skipped"
STATUSES: tuple[str, ...] = (STATUS_EXECUTED, STATUS_HELD, STATUS_QUESTION,
                             STATUS_ERROR, STATUS_ABORTED, STATUS_SKIPPED)
#: Estados em que a ação chegou a ser tentada (contam para falha/repetição).
ATTEMPT_STATUSES = frozenset({STATUS_EXECUTED, STATUS_ABORTED, STATUS_ERROR})
#: Do mais benigno ao mais grave: fusão do ``status`` do chamador com o resultado.
_STATUS_RANK = {s: i for i, s in enumerate(
    (STATUS_SKIPPED, STATUS_QUESTION, STATUS_HELD, STATUS_EXECUTED, STATUS_ABORTED, STATUS_ERROR))}
_STATUS_ALIASES = {"dry_run": STATUS_EXECUTED, "ok": STATUS_EXECUTED, "refused": STATUS_ERROR,
                   "failed": STATUS_ERROR, "killed": STATUS_ABORTED, "paused": STATUS_HELD,
                   "pending": STATUS_HELD}
BANDS: tuple[str, ...] = ("auto", "hitl", "abstain")
#: Ids que não são "uma ação tentada": a opção de saída e a falta de ação.
_UNTRACKED_IDS = frozenset({"", "none", "unknown", "other", "none_of_the_above"})

DEFAULT_MAXLEN = 12            # entradas no anel
DEFAULT_WINDOW = 5             # entradas que contam para repetição/alternância
DEFAULT_REPEAT_THRESHOLD = 3   # mesma ação N× na janela = repetida
DEFAULT_RECENT_COUNT = 5
DEFAULT_RECENT_CHARS = 600
DEFAULT_FAILED_CHARS = 300
MAX_OUTCOME_CHARS = 120
MAX_REASON_CHARS = 60
MAX_QUOTE_CHARS = 48
MAX_ID_CHARS = 64
MAX_TS_CHARS = 40
MAX_ERROR_TEXTS = 8            # mensagens do passo que entram como motivo
MAX_LOG_LINES = 200            # linhas de gamelog comparadas por snapshot
MAX_REPORT_STEPS = 64          # passos do relatório da ponte inspecionados
_EPSILON = 1e-6                # variação mínima de sangue/dor que conta
MIN_BLOCK_CHARS = 60           # bloco do meio abaixo disto é largado inteiro
MIN_STUB_CHARS = 20            # cabeça/cauda: abaixo disto cai em vez de ficar um cotoco

#: U-curve: crítico no início, histórico acionável no fim, volumoso no meio.
HEAD_BLOCKS: tuple[str, ...] = ("character", "threats", "needs", "objective")
MIDDLE_BLOCKS: tuple[str, ...] = ("inventory", "location", "clock", "knowledge", "narrative", "screen")
TAIL_BLOCKS: tuple[str, ...] = ("failed", "recent_decisions", "recent_events")
RECENT_HEADER = "Recent decisions (oldest first, with result): "
FAILED_HEADER = "Failed or repeated - do not repeat unless the situation changed: "

#: Verbos de movimento: o progresso é a posição mudar.  Esperar/descansar nunca
#: falha por "sem progresso" (esperar não avança de propósito).
_MOVEMENT_VERBS = frozenset("explore flee navigate navigate_to hunt goto move travel retreat".split())
_REST_VERBS = frozenset("rest sleep wait".split())
#: Mesma tabela de gravidade que o ``StuckDetector`` (dano = piorar nisto).
_SEVERITY = {"dead": 4, "unconscious": 3, "badly wounded": 2, "injured": 2, "wounded": 1, "healthy": 0}
_DROP_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})


# ---------------------------------------------------------------- saneamento


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _cut(text: str, limit: int) -> str:
    """Corta a ``limit`` caracteres (fronteira de palavra se der) com ``…``."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    head = text[: limit - 1]
    space = head.rfind(" ")
    return (head[:space] if space >= limit // 2 else head).rstrip() + "…"


def _clean(value: Any, limit: int = 0) -> str:
    """Texto não confiável → uma linha segura (dado, nunca instrução).

    Sem caracteres de controlo/formatação (inclui bidi), espaços colapsados,
    ``|`` (separador dos blocos) → ``/`` e ``"`` → ``'``.  Idempotente; nunca levanta.
    """
    try:
        text = value if isinstance(value, str) else ("" if value is None else str(value))
        text = text[: limit * 4 + 16] if limit > 0 else text  # limita o trabalho
        text = "".join(" " if unicodedata.category(c) in _DROP_CATEGORIES else c for c in text)
        text = " ".join(text.split()).replace("|", "/").replace('"', "'")
    except Exception:  # __str__ hostil
        return ""
    return _cut(text, limit) if limit > 0 else text


def _safe_id(value: Any) -> str:
    """Id de ação seguro: alfanumérico + ``_.:-/``, no máximo ``MAX_ID_CHARS``."""
    text = _clean(value, MAX_ID_CHARS * 2)
    return "".join(c if c.isalnum() or c in "_.:-/" else "_" for c in text)[:MAX_ID_CHARS]


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """``getattr`` que nunca levanta (nem com propriedades que rebentam)."""
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except Exception:  # TypeError/ValueError/OverflowError ou um __int__ hostil
        return None


def _as_float(value: Any) -> float | None:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    return float(value) if ok else None


def _texts(values: Any) -> tuple[str, ...]:
    """Mensagens (str única, iterável ou lixo) → textos limpos e curtos."""
    try:
        items = list((values,) if isinstance(values, (str, bytes)) else values or ())[:MAX_ERROR_TEXTS]
    except Exception:  # não iterável, ou um iterador hostil
        items = []
    return tuple(t for t in (_clean(v, MAX_REASON_CHARS) for v in items) if t)


def _status_of(value: Any) -> tuple[str, bool]:
    """``(status do vocabulário | "", era "refused")`` — aliases tolerados."""
    text = value.strip().lower() if isinstance(value, str) else ""
    mapped = _STATUS_ALIASES.get(text, text)
    return (mapped if mapped in STATUSES else ""), text == "refused"


def _key(value: Any) -> str:
    """Nome de bloco normalizado (``"  Failed "`` → ``"failed"``); nunca levanta."""
    try:
        return str(value).strip().lower()
    except Exception:
        return ""


def _band_of(value: Any) -> str | None:
    text = value.strip().lower() if isinstance(value, str) else ""
    return text if text in BANDS else None


# --------------------------------------------------------------------- entrada


@dataclass(frozen=True)
class HistoryEntry:
    """Uma decisão registada e o que RESULTOU (factual).

    ``outcome`` é a oração a seguir à seta do bloco (``"executed, moved 3 tiles
    n"``); ``progressed`` é ``None`` quando não há como saber; ``failed`` é
    ``True`` para ``aborted``/``error``/``refused`` ou quando se *sabe* que não
    houve progresso.
    """

    step: int
    action_id: str
    verb: str = ""
    band: str | None = None
    status: str = STATUS_EXECUTED
    outcome: str = ""
    progressed: bool | None = None
    failed: bool = False
    ts: str = ""

    @property
    def attempted(self) -> bool:
        """A ação chegou a ser tentada (e não é a opção de saída)?"""
        return self.status in ATTEMPT_STATUSES and self.action_id not in _UNTRACKED_IDS

    @property
    def failure_kind(self) -> str:
        """``aborted`` | ``refused`` | ``error`` | ``no progress`` (``""`` se não falhou)."""
        if not self.failed:
            return ""
        if self.status == STATUS_ERROR:
            return "refused" if self.outcome.startswith("refused") else "error"
        return "aborted" if self.status == STATUS_ABORTED else "no progress"

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "HistoryEntry | None":
        """Entrada de um mapeamento tolerante; lixo ⇒ ``None``."""
        if not isinstance(data, Mapping) or not _safe_id(data.get("action_id")):
            return None
        progressed = data.get("progressed")
        return cls(
            step=_as_int(data.get("step")) or 0, action_id=_safe_id(data.get("action_id")),
            verb=_safe_id(data.get("verb")).lower(), band=_band_of(data.get("band")),
            status=_status_of(data.get("status"))[0] or STATUS_SKIPPED,
            outcome=_clean(data.get("outcome"), MAX_OUTCOME_CHARS),
            progressed=progressed if isinstance(progressed, bool) else None,
            failed=data.get("failed") is True, ts=_clean(data.get("ts"), MAX_TS_CHARS),
        )


# ------------------------------------------------------- resultado e diff de estado


@dataclass(frozen=True)
class _Result:
    """O que interessa de um ``ExecutionResult`` (objeto ou mapeamento)."""

    status: str = ""
    reason: str = ""
    steps: int | None = None
    manual: bool = False
    report: Mapping[str, Any] | None = None
    action_id: str = ""


def _read_result(executed: Any) -> _Result | None:
    if executed is None:
        return None
    get = executed.get if isinstance(executed, Mapping) else (lambda key: _attr(executed, key))
    status, reason, report = get("status"), get("reason"), get("report")
    return _Result(
        status=status.strip().lower() if isinstance(status, str) else "",
        reason=_clean(reason, MAX_REASON_CHARS) if isinstance(reason, str) else "",
        steps=_as_int(get("steps_executed")), manual=get("needs_manual_mapping") is True,
        report=report if isinstance(report, Mapping) else None, action_id=_safe_id(get("action_id")),
    )


@dataclass(frozen=True)
class _Diff:
    """Diff ``before → after``: grupos comparáveis, sinais positivos e frases EN."""

    known: frozenset[str] = frozenset()    # pos kills health events
    signals: frozenset[str] = frozenset()  # moved kill recovered events
    parts: tuple[str, ...] = ()            # por ordem de importância


def _grab(obj: Any, *path: str) -> Any:
    for name in path:
        obj = _attr(obj, name)
        if obj is None:
            return None
    return obj


def _position(state: Any) -> tuple[int, int, int] | None:
    for source in (_grab(state, "metrics", "location"), _attr(state, "adventurer")):
        x, y, z = (_as_int(_attr(source, axis)) for axis in "xyz")
        if x is not None and y is not None:
            return x, y, z or 0
    return None


def _health(state: Any) -> tuple[int, int, float, float | None] | None:
    info = _grab(state, "metrics", "health_injuries")
    if info is None:
        return None
    status = _attr(info, "status")
    status = status.strip().lower() if isinstance(status, str) else ""
    return (_SEVERITY.get(status, 1 if status else 0), _as_int(_attr(info, "wound_count")) or 0,
            _as_float(_attr(info, "pain")) or 0.0, _as_float(_attr(info, "blood_current")))


def _health_change(before: tuple, after: tuple) -> tuple[str, str]:
    """``(piorou, melhorou)`` em texto curto (``"blood 100->92, wounds 0->1"``)."""
    worse: list[str] = []
    better: list[str] = []
    if after[0] != before[0]:
        (worse if after[0] > before[0] else better).append(
            "status worsened" if after[0] > before[0] else "status improved")
    for name, old, new, bad_when_higher in (
        ("wounds", before[1], after[1], True), ("pain", before[2], after[2], True),
        ("blood", before[3], after[3], False),
    ):
        if old is not None and new is not None and abs(new - old) > _EPSILON:
            (worse if (new > old) == bad_when_higher else better).append(f"{name} {old:g}->{new:g}")
    return ", ".join(worse[:3]), ", ".join(better[:3])


def _event_lines(state: Any) -> list[str] | None:
    """Linhas do gamelog do snapshot (janela deslizante); ``None`` se indisponível."""
    events = _attr(state, "recent_events")
    if events is None:
        events = _grab(state, "metrics", "events")
    if events is None or isinstance(events, (str, bytes, Mapping)):
        return None
    try:
        items = list(events)[-MAX_LOG_LINES:]
    except Exception:
        return None
    lines: list[str] = []
    for item in items:
        text = item if isinstance(item, str) else (
            item.get("text") if isinstance(item, Mapping) else _attr(item, "text"))
        if isinstance(text, str) and text.strip():
            lines.append(" ".join(text.split()))
    return lines


def _new_lines(before: list[str], after: list[str]) -> list[str]:
    """Linhas de ``after`` que não estavam em ``before``.

    O gamelog é uma janela deslizante (as últimas N linhas): o maior sufixo de
    ``before`` que é prefixo de ``after`` já se conhecia.  Mais fiável do que a
    diferença de conjuntos, que perde linhas repetidas (``"You stab the mule."``).
    """
    for size in range(min(len(before), len(after)), 0, -1):
        if before[-size:] == after[:size]:
            return after[size:]
    return list(after)


def _moved_text(dx: int, dy: int, dz: int) -> str:
    dist = max(abs(dx), abs(dy))
    if not dist:
        return f"changed level (z {dz:+d})"
    heading = ("n" if dy < 0 else "s" if dy > 0 else "") + ("e" if dx > 0 else "w" if dx < 0 else "")
    text = f"moved {dist} tile{'' if dist == 1 else 's'} {heading}"
    return f"{text}, z {dz:+d}" if dz else text


def _diff_states(before: Any, after: Any) -> _Diff:
    """Compara dois ``GameState`` (tolerante: o que faltar fica "desconhecido")."""
    if before is None or after is None:
        return _Diff()
    known: set[str] = set()
    signals: set[str] = set()
    parts: list[str] = []

    kills = [_as_int(_grab(s, "adventurer", "kills")) for s in (before, after)]
    if None not in kills:
        known.add("kills")
        if kills[1] > kills[0]:
            signals.add("kill")
            parts.append(f"kills +{kills[1] - kills[0]}")

    pos_before, pos_after = _position(before), _position(after)
    if pos_before is not None and pos_after is not None:
        known.add("pos")
        delta = tuple(a - b for a, b in zip(pos_after, pos_before))
        if any(delta):
            signals.add("moved")
            parts.append(_moved_text(*delta))

    health_before, health_after = _health(before), _health(after)
    if health_before is not None and health_after is not None:
        known.add("health")
        worse, better = _health_change(health_before, health_after)
        if worse:
            parts.append(f"took damage ({worse})")
        if better:
            signals.add("recovered")
            parts.append(f"recovered ({better})")

    lines_before, lines_after = _event_lines(before), _event_lines(after)
    if lines_before is not None and lines_after is not None:
        known.add("events")
        fresh = _new_lines(lines_before, lines_after)
        if fresh:
            signals.add("events")
            quote = _clean(fresh[-1], MAX_QUOTE_CHARS)
            parts.append(f"{len(fresh)} new events, last: '{quote}'" if len(fresh) > 1
                         else f"1 new event: '{quote}'")
    return _Diff(frozenset(known), frozenset(signals), tuple(parts))


def _report_moved(report: Mapping[str, Any] | None) -> bool | None:
    """O relatório do ``act batch`` diz se os passos ``move``/``goto`` mexeram?"""
    steps = report.get("steps") if isinstance(report, Mapping) else None
    moves = [s for s in (steps[:MAX_REPORT_STEPS] if isinstance(steps, (list, tuple)) else ())
             if isinstance(s, Mapping) and s.get("op") in ("move", "goto")]
    return any(s.get("ok") is True for s in moves) if moves else None


def _judge(movement: bool, rest: bool, status: str, res: _Result | None, diff: _Diff) -> bool | None:
    """Houve progresso?  ``True``/``False``, ou ``None`` quando não se sabe."""
    if status not in ATTEMPT_STATUSES or (res is not None and res.status == "dry_run"):
        return None
    counted = {"moved", "kill"} if movement else {"moved", "kill", "recovered"} if rest \
        else {"moved", "kill", "events", "recovered"}
    if diff.signals & counted:
        return True
    if res is not None and status == STATUS_EXECUTED and res.reason.lower().startswith("target down"):
        return True
    if rest:
        return None
    if diff.known & ({"pos"} if movement else {"pos", "events", "kills", "health"}):
        return False
    return _report_moved(res.report) if movement and res is not None else None


def _compose_outcome(status: str, *, refused: bool, reason: str, res: _Result | None,
                     diff: _Diff, movement: bool, progressed: bool | None) -> str:
    detail = f" ({reason})" if reason else ""
    if status == STATUS_SKIPPED:
        return f"skipped ({reason or 'no concrete action'})"
    if status == STATUS_QUESTION:
        return "asked the human" + detail
    if status == STATUS_HELD:
        return "held for human approval" + detail
    if status in (STATUS_ABORTED, STATUS_ERROR):
        return ", ".join([("refused" if refused else status) + detail, *diff.parts])
    if res is not None and res.status == "dry_run":
        return "executed, dry run (nothing sent)"
    parts = ([reason] if reason else []) + list(diff.parts)
    if progressed is False:
        parts.append("no movement" if movement else "no visible effect")
    if res is not None and res.manual:
        parts.append("manual steps not executed")
    return "executed, " + ", ".join(parts) if parts else "executed"


# ----------------------------------------------------------------- o histórico


@dataclass
class _Stat:
    tries: int = 0
    last: int = 0
    unconfirmed: bool = False
    kinds: Counter = field(default_factory=Counter)


class DecisionHistory:
    """Anel das últimas decisões com resultado factual + deteção de repetição.

    Args:
        maxlen: entradas guardadas (as mais antigas caem).
        window: entradas recentes que contam para repetição/alternância
            (limitada a ``maxlen``).
        repeat_threshold: mesma ação tentada tantas vezes na janela = repetida.
        clock: ``() -> str`` ISO-8601 UTC (injetável nos testes).

    Seguro entre threads (``RLock``) e **nunca levanta** na API pública.
    """

    def __init__(self, maxlen: int = DEFAULT_MAXLEN, window: int = DEFAULT_WINDOW,
                 repeat_threshold: int = DEFAULT_REPEAT_THRESHOLD, *,
                 clock: Callable[[], str] | None = None) -> None:
        self.maxlen = max(1, _as_int(maxlen) or DEFAULT_MAXLEN)
        self.window = max(1, min(_as_int(window) or DEFAULT_WINDOW, self.maxlen))
        self.repeat_threshold = max(1, _as_int(repeat_threshold) or DEFAULT_REPEAT_THRESHOLD)
        self._clock: Callable[[], str] = clock if callable(clock) else _utc_now_iso
        self._lock = threading.RLock()
        self._entries: deque[HistoryEntry] = deque(maxlen=self.maxlen)
        #: ``(entrada, argumentos)`` da última tentativa sem ``after`` (para ``settle``).
        self._pending: tuple[HistoryEntry, tuple] | None = None

    # -------------------------------------------------------------- escrita

    def record(self, step: Any, action: Any, band: Any = None, status: Any = STATUS_EXECUTED,
               executed: Any = None, before: Any = None, after: Any = None,
               errors: Any = ()) -> HistoryEntry:
        """Regista uma decisão e o seu resultado; devolve a entrada.

        ``executed`` é o ``ExecutionResult``; ``before``/``after`` os
        ``GameState`` de antes/depois.  Sem ``after`` o progresso sai do
        relatório da ponte (ou fica ``None``) e :meth:`settle` refina-o quando
        chega o snapshot seguinte.  Nunca levanta.
        """
        args = (action, band, status, executed, before, _texts(errors))
        try:
            with self._lock:
                number = _as_int(step)
                if number is None:
                    number = self._entries[-1].step + 1 if self._entries else 1
            entry = self._derive(number, *args, after, ts=None)
        except Exception:  # o histórico nunca derruba o passo
            log.debug("history.record: could not derive the outcome", exc_info=True)
            entry = HistoryEntry(
                step=_as_int(step) or 0, action_id=_safe_id(_attr(action, "id", action)) or "unknown",
                band=_band_of(band), status=_status_of(status)[0] or STATUS_SKIPPED,
                outcome="history unavailable", ts=self._now())
        with self._lock:
            self._entries.append(entry)
            pending = after is None and before is not None and entry.status in ATTEMPT_STATUSES
            self._pending = (entry, args) if pending else None
        return entry

    def settle(self, after: Any) -> HistoryEntry | None:
        """Fecha a última entrada com o estado seguinte (o snapshot do passo N+1).

        Refaz o resultado com o diff ``before → after`` real; devolve a entrada
        atualizada, ou ``None`` quando não havia nada pendente.  Nunca levanta.
        """
        if after is None:
            return None
        with self._lock:
            pending, self._pending = self._pending, None
            if pending is None or not self._entries or self._entries[-1] is not pending[0]:
                return None
            try:
                entry = self._derive(pending[0].step, *pending[1], after, ts=pending[0].ts)
            except Exception:
                log.debug("history.settle: could not refine the outcome", exc_info=True)
                return None
            self._entries[-1] = entry
            return entry

    def reset(self) -> None:
        """Esquece tudo (novo aventureiro/campanha)."""
        with self._lock:
            self._entries.clear()
            self._pending = None

    # ------------------------------------------------------------- consulta

    def entries(self) -> tuple[HistoryEntry, ...]:
        """Entradas, da mais antiga para a mais recente."""
        with self._lock:
            return tuple(self._entries)

    def recent_block(self, n: int = DEFAULT_RECENT_COUNT,
                     char_budget: int = DEFAULT_RECENT_CHARS) -> str:
        """``"Recent decisions (oldest first, with result): #12 explore:n -> …"``.

        As ``n`` últimas entradas em ``char_budget`` caracteres (cai a mais
        antiga primeiro, sempre entradas inteiras); ``""`` sem histórico.
        """
        count = max(0, min(DEFAULT_RECENT_COUNT if _as_int(n) is None else _as_int(n), self.maxlen))
        items = [f"#{e.step} {e.action_id} -> {e.outcome or e.status}"
                 for e in (self.entries()[-count:] if count else ())]
        return _fit(RECENT_HEADER, items, _budget(char_budget, DEFAULT_RECENT_CHARS),
                    sep=" | ", keep_last=True)

    def failed_block(self, char_budget: int = DEFAULT_FAILED_CHARS) -> str:
        """``"Failed or repeated - do not repeat unless the situation changed: …"``.

        A alternância ``A,B,A,B`` e cada ação que falhou ou que se repetiu sem
        progresso confirmado na janela (mais falhadas primeiro); ``""`` se não
        há nada a avisar.
        """
        attempts, span = self._window_attempts()
        items: list[str] = []
        pair = _alternation(attempts)
        if pair is not None:
            items.append(f"alternating {pair[0]} <-> {pair[1]}")
        flagged = [(key, stat) for key, stat in _stats(attempts).items() if self._flagged(stat)]
        flagged.sort(key=lambda kv: (-sum(kv[1].kinds.values()), -kv[1].last, kv[0]))
        for key, stat in flagged:
            if stat.kinds:
                detail = ", ".join(f"{kind} x{count}" for kind, count in stat.kinds.items())
                items.append(f"{key} ({detail} in last {span})")
            else:
                items.append(f"{key} (repeated x{stat.tries} in last {span}, progress unknown)")
        return _fit(FAILED_HEADER, items, _budget(char_budget, DEFAULT_FAILED_CHARS),
                    sep="; ", keep_last=False)

    def repeated_actions(self) -> dict[str, int]:
        """``{action_id: tentativas na janela}`` para as que chegam ao limiar."""
        counts = Counter(e.action_id for e in self._window_attempts()[0])
        return {key: n for key, n in counts.items() if n >= self.repeat_threshold}

    def alternating(self) -> tuple[str, str] | None:
        """``(A, B)`` quando a janela tem ``A,B,A,B`` (oscilação), senão ``None``."""
        return _alternation(self._window_attempts()[0])

    def annotate(self, action_id: Any) -> str | None:
        """Nota curta EN para a descrição de um candidato (``None`` = nada a avisar).

        Ex.: ``"tried 2x in the last 5 steps, no progress"``.  Só há nota para
        ações que falharam na janela ou que se repetiram sem progresso confirmado.
        """
        key = _safe_id(action_id)
        attempts, span = self._window_attempts()
        stat = None if key in _UNTRACKED_IDS else _stats(attempts).get(key)
        if stat is None or not self._flagged(stat):
            return None
        if stat.kinds:
            failed, kinds = sum(stat.kinds.values()), "/".join(stat.kinds)
            why = kinds if failed == stat.tries else f"{failed} failed: {kinds}"
        else:
            why = "progress not confirmed"
        return f"tried {stat.tries}x in the last {span} steps, {why}"

    # ---------------------------------------------------------- persistência

    def to_dicts(self) -> list[dict[str, Any]]:
        """Entradas como dicionários JSON-seguros (persistência por campanha)."""
        return [entry.as_dict() for entry in self.entries()]

    @classmethod
    def from_dicts(cls, items: Any, **kwargs: Any) -> "DecisionHistory":
        """Reconstrói um histórico de :meth:`to_dicts` (``kwargs`` = construtor).

        Lixo é descartado; o anel de ``maxlen`` guarda as entradas mais recentes.
        """
        history = cls(**kwargs)
        try:
            if isinstance(items, Iterable) and not isinstance(items, (str, bytes, Mapping)):
                history._entries.extend(e for e in map(HistoryEntry.from_dict, items) if e is not None)
        except Exception:  # um iterável hostil: fica o que já foi lido
            log.debug("history.from_dicts: stopped at an unreadable item", exc_info=True)
        return history

    # -------------------------------------------------------------- interno

    def _window_attempts(self) -> tuple[list[HistoryEntry], int]:
        """Tentativas das últimas ``window`` entradas + o tamanho real da janela."""
        with self._lock:
            recent = list(self._entries)[-self.window:]
        return [entry for entry in recent if entry.attempted], len(recent)

    def _flagged(self, stat: _Stat) -> bool:
        return bool(stat.kinds) or (stat.tries >= self.repeat_threshold and stat.unconfirmed)

    def _now(self) -> str:
        try:
            return _clean(self._clock(), MAX_TS_CHARS)
        except Exception:
            return ""

    def _derive(self, step: int, action: Any, band: Any, status: Any, executed: Any, before: Any,
                errors: tuple[str, ...], after: Any, *, ts: str | None) -> HistoryEntry:
        res = _read_result(executed)
        if isinstance(action, Mapping):
            raw_id, raw_verb = action.get("id"), action.get("verb")
        elif isinstance(action, str):
            raw_id, raw_verb = action, ""
        else:
            raw_id, raw_verb = _attr(action, "id"), _attr(action, "verb")
        action_id = _safe_id(raw_id) or (res.action_id if res else "") \
            or ("none" if action is None else "unknown")
        prefix = action_id.split(":", 1)[0].lower()
        verb = (_safe_id(raw_verb) or prefix).lower()

        given, refused_given = _status_of(status)
        derived, refused_result = _status_of(res.status if res else None)
        votes = [s for s in (given, derived) if s]
        merged = max(votes, key=_STATUS_RANK.__getitem__) if votes else STATUS_SKIPPED
        refused = (refused_given or refused_result) and merged == STATUS_ERROR

        movement = verb in _MOVEMENT_VERBS or prefix in _MOVEMENT_VERBS
        rest = not movement and (verb in _REST_VERBS or prefix in _REST_VERBS)
        # Um plano abortado/recusado sem passos executados não mexeu no jogo: o
        # que mudou no estado entretanto não é efeito desta ação.
        sent = not (res is not None and res.steps == 0 and merged in (STATUS_ABORTED, STATUS_ERROR))
        diff = _diff_states(before, after) if merged in ATTEMPT_STATUSES and sent else _Diff()
        progressed = _judge(movement, rest, merged, res, diff)
        failed = merged in (STATUS_ABORTED, STATUS_ERROR) or progressed is False
        if merged in (STATUS_ABORTED, STATUS_ERROR):
            reason = (res.reason if res else "") or (errors[0] if errors else "")
        elif merged == STATUS_EXECUTED:
            reason = res.reason if res else ""  # avisos do passo não são "resultado"
        else:
            reason = "" if votes or res else "unknown status"
        outcome = _compose_outcome(merged, refused=refused, reason=reason, res=res, diff=diff,
                                   movement=movement, progressed=progressed)
        return HistoryEntry(step=step, action_id=action_id, verb=verb, band=_band_of(band),
                            status=merged, outcome=_cut(outcome, MAX_OUTCOME_CHARS),
                            progressed=progressed, failed=failed,
                            ts=ts if ts is not None else self._now())


def _stats(attempts: Sequence[HistoryEntry]) -> dict[str, _Stat]:
    stats: dict[str, _Stat] = {}
    for entry in attempts:
        stat = stats.setdefault(entry.action_id, _Stat())
        stat.tries += 1
        stat.last = entry.step
        if entry.failed:
            stat.kinds[entry.failure_kind] += 1
        if entry.progressed is not True:
            stat.unconfirmed = True
    return stats


def _alternation(attempts: Sequence[HistoryEntry]) -> tuple[str, str] | None:
    ids = [entry.action_id for entry in attempts]
    for start in range(len(ids) - 4, -1, -1):  # a oscilação mais recente primeiro
        a, b, c, d = ids[start:start + 4]
        if a == c and b == d and a != b:
            return a, b
    return None


def _budget(value: Any, default: int) -> int:
    number = _as_int(value)
    return default if number is None else max(0, number)


def _fit(header: str, items: Sequence[str], budget: int, *, sep: str, keep_last: bool) -> str:
    """``header + sep.join(itens)`` em ``<= budget`` caracteres, só com itens inteiros.

    ``keep_last`` guarda os mais recentes (larga os do início); senão larga os
    do fim.  Se nem um item cabe, ``""`` — nunca um bloco cortado a meio.
    """
    if not items or budget <= len(header):
        return ""
    order = list(reversed(items)) if keep_last else list(items)
    kept: list[str] = []
    used = len(header)
    for item in order:
        extra = len(item) + (len(sep) if kept else 0)
        if used + extra > budget:
            break
        kept.append(item)
        used += extra
    if not kept:
        return ""
    return header + sep.join(reversed(kept) if keep_last else kept)


# ------------------------------------------------------------- ordem U-curve


class _OrderedBlocks(list):
    """``list[str]`` que lembra quantos blocos são cabeça/cauda (para o trim)."""

    head_count = 0
    tail_count = 0


def order_state_blocks(blocks: Mapping[str, str], *, head: Sequence[str] = HEAD_BLOCKS,
                       tail: Sequence[str] = TAIL_BLOCKS) -> list[str]:
    """Blocos do estado em ordem **U-curve** (textos, sem vazios).

    O modelo usa melhor o início e o fim do contexto (Liu et al., TACL 2024):
    primeiro o **crítico** (``head``, na ordem dada), no fim o histórico
    **acionável** (``tail``) e no meio o resto — o volumoso e pouco prioritário
    (inventário, localização, relógio, conhecimento, narrativa, ecrã) e os
    blocos desconhecidos — na ordem de entrada.  Blocos vazios caem; o resultado
    só depende do conteúdo e da ordem de ``blocks``.  Um nome em ``head`` e
    ``tail`` conta como ``head``.

    É uma ``list`` normal que lembra quantos blocos são cabeça/cauda, para o
    :func:`trim_to_budget` saber o que proteger.
    """
    ordered = _OrderedBlocks()
    if not isinstance(blocks, Mapping):
        return ordered
    ranks: list[dict[str, int]] = []
    for group in (head, tail):
        try:
            names = (group,) if isinstance(group, str) else tuple(group)
        except Exception:
            names = ()
        ranks.append({_key(n): i for i, n in enumerate(names)})
    slots: list[list[tuple[int, str]]] = [[], [], []]  # cabeça, meio, cauda
    for name, value in blocks.items():
        try:
            text = "" if value is None else (value if isinstance(value, str) else str(value)).strip()
        except Exception:
            continue
        key = _key(name)
        if text:
            slot = 0 if key in ranks[0] else 2 if key in ranks[1] else 1
            slots[slot].append((ranks[0].get(key, ranks[1].get(key, 0)), text))
    for slot in slots:  # ordenação estável: o meio (rank 0) fica na ordem de entrada
        ordered.extend(text for _, text in sorted(slot, key=lambda pair: pair[0]))
    ordered.head_count, ordered.tail_count = len(slots[0]), len(slots[2])
    return ordered


def _shrink(text: str, target: int) -> str:
    """Encurta a ``<= target``: linhas do fim, depois itens ``" | "``/``"; "``
    (do início se o rótulo diz "oldest first", senão do fim).  Um bloco de itens
    fica com itens inteiros ou cai (``""``) — nunca cortado a meio; só o texto
    corrido (objetivo, ecrã) é cortado numa fronteira de palavra.  Linear."""
    if len(text) <= target:
        return text
    lines, size = text.split("\n"), len(text)
    while len(lines) > 1 and size > target:
        size -= len(lines.pop()) + 1
    text = "\n".join(lines)
    label, colon, rest = text.partition(": ")
    for sep in (" | ", "; "):
        if colon and len(text) > target and sep in rest:
            items, oldest_first = rest.split(sep), "oldest first" in label.lower()
            lo, hi, size = 0, len(items), len(text)
            while hi - lo > 1 and size > target:
                if oldest_first:
                    size -= len(items[lo]) + len(sep)
                    lo += 1
                else:
                    hi -= 1
                    size -= len(items[hi]) + len(sep)
            text = label + colon + sep.join(items[lo:hi])
            return text if len(text) <= target else ""
    return _cut(text, target)


def trim_to_budget(lines: Sequence[str], max_chars: int, *, keep_head: int | None = None,
                   keep_tail: int | None = None, separator: str = "\n") -> list[str]:
    """Cabe os blocos em ``max_chars`` (``separator.join`` incluído); ``list[str]``.

    Mantém a **cabeça** e a **cauda** e corta primeiro o **meio**: o maior bloco
    encolhe só o excedente (ou cai, quando quase nada sobra).  Só se o meio
    acabar é que cabeça/cauda encolhem — por itens/linhas inteiros, nunca um
    ``failed``/``recent`` cortado a meio de um item (o maior primeiro).  O
    resultado cabe SEMPRE em ``max_chars``.

    ``keep_head``/``keep_tail`` = blocos protegidos em cada ponta; por omissão
    usa a contagem que :func:`order_state_blocks` deixou na lista (ou
    ``len(HEAD_BLOCKS)``/``len(TAIL_BLOCKS)`` numa lista simples).  Vazios são
    ignorados; ``max_chars`` inválido ⇒ sem corte, ``<= 0`` ⇒ ``[]``.
    """
    try:
        blocks = [t for t in (str(b) for b in lines if b is not None) if t.strip()]
    except Exception:  # não iterável, ou um iterador/objeto hostil
        blocks = []
    limit, sep = _as_int(max_chars), separator if isinstance(separator, str) else "\n"
    if limit is None:
        return blocks
    if limit <= 0:
        return []

    def size(items: Sequence[str]) -> int:
        return sum(map(len, items)) + len(sep) * max(0, len(items) - 1)

    if size(blocks) <= limit:
        return blocks
    head_n = _as_int(keep_head if keep_head is not None else _attr(lines, "head_count", len(HEAD_BLOCKS)))
    tail_n = _as_int(keep_tail if keep_tail is not None else _attr(lines, "tail_count", len(TAIL_BLOCKS)))
    head_n = min(max(head_n or 0, 0), len(blocks))
    tail_n = min(max(tail_n or 0, 0), len(blocks) - head_n)
    head, middle, tail = blocks[:head_n], blocks[head_n:len(blocks) - tail_n], blocks[len(blocks) - tail_n:]

    while any(middle):  # 1) o meio: o maior bloco encolhe só o excedente (ou cai)
        excess = size(head + [b for b in middle if b] + tail) - limit
        if excess <= 0:
            break
        idx = max((i for i, b in enumerate(middle) if b), key=lambda i: (len(middle[i]), i))
        target = len(middle[idx]) - excess
        shrunk = _shrink(middle[idx], target) if target >= MIN_BLOCK_CHARS else ""
        middle[idx] = shrunk if len(shrunk) < len(middle[idx]) else ""
    blocks = head + [b for b in middle if b] + tail

    while size(blocks) > limit and blocks:  # 2) cabeça/cauda: o maior encolhe (ou cai)
        idx = max(range(len(blocks)), key=lambda i: (len(blocks[i]), i))
        target = len(blocks[idx]) - (size(blocks) - limit)
        blocks[idx] = _shrink(blocks[idx], target) if target >= MIN_STUB_CHARS else ""
        blocks = [b for b in blocks if b]
    return blocks
