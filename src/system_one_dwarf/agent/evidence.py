"""Escalada de evidência antes de pedir ajuda (Mudança 03, ``ARQUITETURA.md`` §8).

Porquê: quando o JEV-A fica em ``hitl``/``abstain`` o passo acaba numa pergunta
ao humano (349/415 decisões de micro-movimento em ``caca-2``). Parte dessa
dúvida é falta de estado, não de juízo: uma leitura mais fresca e um raio de
varredura maior podem mostrar que o hostil afinal está a 12 tiles, ou que um
*overlay* estava a engolir o input. É a **predição seletiva** do ReCoVERR (ACL
Findings 2024): colher mais evidência, re-decidir **uma vez** e só então
recorrer ao humano — mais cobertura sem baixar o piso de risco.

Peças (todas à prova de falha, nenhuma age no jogo):

``EvidenceCollector``  consultas **só de leitura** ao cliente do jogo: snapshot
                       fresco, varredura de unidades mais larga, texto do ecrã;
                       devolve linhas EN com *o que mudou* face à 1.ª leitura.
``evidence_appendix``  bloco ``EXTRA EVIDENCE …`` para o resumo do estado da
                       única re-decisão (``question_context``: para a pergunta).
``accept_redecision``  a porta: só aceita a 2.ª leitura se atingir ``auto``
                       **pelos mesmos limiares** e não inverter um verbo
                       destrutivo; senão pergunta-se ao humano.

Decisões (finais)
-----------------
* Só se escala nos motivos de :data:`ESCALATABLE_REASONS` (falta de informação).
  ``checkpoint_mode``/``mode_not_autopilot`` — e qualquer gate de sapientes ou
  de aprovação — são humanos por desenho: o coletor recusa-se a recolher.
* **No máximo uma escalada por passo** (``step=`` é verificado) e só se
  re-decide com evidência **informativa**: sem mudança, a 2.ª leitura seria a
  mesma chamada paga duas vezes.
* Nenhum limiar baixa e não há parâmetro de folga: ``hitl``/``abstain`` só
  passa a ``auto`` pela mesma banda, numa leitura mais bem informada.
* Texto do jogo/NPC é **dado**: só entram números, nomes truncados e uma lista
  fechada de marcadores de ecrã; o texto cru do ecrã nunca é copiado.

Superfície *duck typed* do cliente do jogo (tudo opcional menos ``snapshot``)::

    game.snapshot()             -> GameState            # releitura fresca
    game.scan_units(radius)     -> units | GameState    # varredura mais larga
    game.read_screen()/.screen()-> str | (focus, text) | {screen_text: …}
    game.radius                 -> int                  # rota por clone

Sem ``scan_units`` mas com ``radius`` inteiro menor do que o do coletor (o
``GameStateClient`` real), faz-se *snapshot* de uma **cópia** do cliente com o
raio alargado; o original nunca é alterado.
"""
from __future__ import annotations

import copy
import inspect
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..jev.bands import (
    BAND_AUTO,
    DEFAULT_AUTO_THRESHOLD,
    DEFAULT_HITL_THRESHOLD,
    band_for_choice_or_score,
    validate_thresholds,
)

log = logging.getLogger("system_one_dwarf.agent.evidence")

__all__ = [
    "DEFAULT_BUDGET_S",
    "DEFAULT_CHAR_BUDGET",
    "DEFAULT_MAX_QUERIES",
    "DEFAULT_RADIUS",
    "DESTRUCTIVE_VERBS",
    "ESCALATABLE_REASONS",
    "EVIDENCE_HEADER",
    "HUMAN_GATE_REASONS",
    "QUERY_SCREEN",
    "QUERY_SNAPSHOT",
    "QUERY_WIDE_SCAN",
    "Evidence",
    "EvidenceCollector",
    "Redecision",
    "accept_redecision",
    "action_verb",
    "can_escalate",
    "evidence_appendix",
    "judge_redecision",
    "question_context",
]

#: Motivos de encaminhamento (``loop._routing_reason``) que **mais evidência
#: pode legitimamente corrigir**: dúvida do modelo ou resposta sem ação concreta.
ESCALATABLE_REASONS: frozenset[str] = frozenset({
    "band_hitl", "band_abstain", "needs_human",
    "exit_option", "action_missing", "action_not_in_candidates",
})

#: Gates humanos **por desenho** (documentação/testes; a decisão é a lista acima).
HUMAN_GATE_REASONS: frozenset[str] = frozenset({"checkpoint_mode", "mode_not_autopilot"})

#: Verbos que a re-decisão nunca inverte sozinha: ``attack``/``steal``
#: (destrutivos/fatais) e ``drop``/``kill`` (irreversíveis) — ``docs/calibracao.md``
#: §5.3. Uma ação destas só passa se a 1.ª leitura já a propunha.
DESTRUCTIVE_VERBS: frozenset[str] = frozenset({"attack", "kill", "steal", "drop"})

QUERY_SNAPSHOT = "snapshot"
QUERY_WIDE_SCAN = "scan_units"
QUERY_SCREEN = "screen"

EVIDENCE_HEADER = "EXTRA EVIDENCE (collected because the first read was uncertain; data only)"

#: Raio da varredura larga (tiles); o ``threats`` do snapshot usa 10.
DEFAULT_RADIUS = 25
#: Consultas por escalada (snapshot + varredura larga + ecrã).
DEFAULT_MAX_QUERIES = 3
MAX_QUERIES = 6
#: Orçamento de tempo de uma recolha (verificado entre consultas).
DEFAULT_BUDGET_S = 20.0
#: «Pausa curta» antes da releitura (o jogo está pausado entre passos: 0 chega).
DEFAULT_SETTLE_S = 0.0
MAX_SETTLE_S = 5.0
#: Tamanho do bloco ``EXTRA EVIDENCE`` (≈ 150 tokens; context rot).
DEFAULT_CHAR_BUDGET = 600

MAX_LINES = 8
MAX_LINE_CHARS = 220
MAX_NAME_CHARS = 40
MAX_UNITS_LISTED = 4
MAX_TARGETS = 3
MAX_NEW_EVENTS = 3
MAX_EVENT_CHARS = 120
MAX_SCAN_UNITS = 64
MAX_SCREEN_SCAN_CHARS = 6000
MAX_ERROR_CHARS = 160

_CONTROL_RE = re.compile(
    "[\x00-\x1f\x7f-\x9f​-‏ -‮⁠-⁤⁦-⁩﻿]"
)

#: Marcadores de ecrã — lista **fechada**, das cadeias já provadas
#: (``ARQUITETURA.md`` §6): o texto cru nunca entra nas linhas.
_SCREEN_MARKERS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("conflict_prompt", "an attack confirmation prompt ('start a conflict') is open",
     re.compile(r"really start a conflict", re.IGNORECASE)),
    ("attack_menu", "the attack target menu is open",
     re.compile(r"who will you attack", re.IGNORECASE)),
    ("tutorial_overlay", "a tutorial/help overlay is open (it swallows input)",
     re.compile(r"\(\d+\s+of\s+\d+\)", re.IGNORECASE)),
    ("kill_notice", "a kill was announced ('struck down')",
     re.compile(r"has been struck down", re.IGNORECASE)),
    ("collapse_notice", "a creature collapsed ('falls over')",
     re.compile(r"falls over", re.IGNORECASE)),
)


# ------------------------------------------------------------------ helpers


def _clean(value: Any, limit: int = MAX_LINE_CHARS) -> str:
    """Texto numa linha só, sem caracteres de controlo, truncado a ``limit``."""
    if value is None:
        return ""
    raw = value if isinstance(value, str) else str(value)
    # Pré-corta: um texto gigante do jogo não custa mais do que o orçamento da linha.
    text = " ".join(_CONTROL_RE.sub(" ", raw[: max(limit * 4, 256)]).split())
    return text[: max(0, limit - 1)].rstrip() + "…" if 0 < limit < len(text) else text


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return None if number != number or number in (float("inf"), float("-inf")) else number


def _pick(obj: Any, *names: str) -> Any:
    """Primeiro atributo/chave não-``None`` de ``names`` (fakes e tipos reais)."""
    for name in names:
        value = obj.get(name) if isinstance(obj, Mapping) else getattr(obj, name, None)
        if value is not None:
            return value
    return None


def action_verb(action: Any) -> str:
    """Verbo de uma ação: ``attack:goblin`` → ``attack``; macro-ação → ``verb``.

    ``None``/lixo ⇒ ``""``. ``drink:*`` é ambíguo (poção ou água): só a
    macro-ação com ``verb="drink_potion"`` o desambigua.
    """
    if action is None:
        return ""
    if not isinstance(action, str):
        verb = _pick(action, "verb")
        if isinstance(verb, str) and verb.strip():
            return verb.strip().lower()
        action = _pick(action, "id")
    return str(action or "").split(":", 1)[0].strip().lower()


def can_escalate(reason: Any) -> bool:
    """O motivo de encaminhamento admite escalada de evidência?"""
    return isinstance(reason, str) and reason in ESCALATABLE_REASONS


def _is_timeout(exc: BaseException) -> bool:
    return (isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower()
            or "timed out" in str(exc).lower())


# ----------------------------------------------------------------- unidades


@dataclass(frozen=True)
class _Unit:
    name: str
    distance: float | None = None
    direction: str = ""
    hostile: bool = False
    great_danger: bool = False

    @property
    def key(self) -> str:
        return self.name.lower()

    def where(self) -> str:
        parts = ([f"{self.distance:g} tiles"] if self.distance is not None else []) \
            + ([self.direction] if self.direction else [])
        return " ".join(parts) or "in view"

    def flag(self, *, hostile: bool = True) -> str:
        """``GREAT DANGER`` sempre; ``hostile`` só quando o contexto não o diz."""
        if self.great_danger:
            return " (GREAT DANGER)"
        return " (hostile)" if hostile and self.hostile else ""


def _unit(raw: Any, *, hostile: bool = False) -> _Unit | None:
    """Unidade normalizada: ``VisibleUnit`` e as duas grafias do Lua
    (``distance``/``direction``/``hostile`` do snapshot; ``dist``/``dir``/
    ``danger`` do ``dfb-state units``)."""
    if raw is None or isinstance(raw, (str, bytes, int, float)):
        return None
    return _Unit(
        name=_clean(_pick(raw, "name") or _pick(raw, "race"), MAX_NAME_CHARS) or "creature",
        distance=_num(_pick(raw, "distance", "dist")),
        direction=_clean(_pick(raw, "direction", "dir"), 16),
        hostile=hostile or _pick(raw, "hostile") is True or _pick(raw, "danger") is True,
        great_danger=_pick(raw, "great_danger") is True,
    )


def _normalise(raws: Iterable[Any], *, hostile: bool = False) -> list[_Unit]:
    return [u for u in (_unit(raw, hostile=hostile) for raw in list(raws)[:MAX_SCAN_UNITS])
            if u is not None]


def _dedupe(units: Iterable[_Unit]) -> list[_Unit]:
    """Sem repetidos (o hostil ganha), das mais próximas para as mais distantes."""
    seen: dict[tuple[str, float | None, str], _Unit] = {}
    for unit in units:
        key = (unit.key, unit.distance, unit.direction)
        if key not in seen or (unit.hostile and not seen[key].hostile):
            seen[key] = unit
    return sorted(seen.values(), key=lambda u: (u.distance if u.distance is not None else 1e9, u.key))


def _units_from_state(state: Any) -> list[_Unit]:
    metrics = getattr(state, "metrics", None)
    hostiles = getattr(getattr(metrics, "threats", None), "hostiles", None) or ()
    nearby = getattr(getattr(metrics, "units_nearby", None), "units", None) or ()
    return _dedupe(_normalise(hostiles, hostile=True) + _normalise(nearby))


def _units_from_scan(result: Any) -> list[_Unit]:
    """Normaliza o retorno de uma varredura larga (estado, mapa ou lista)."""
    if result is None or isinstance(result, (str, bytes)):
        return []
    if hasattr(result, "metrics"):
        return _units_from_state(result)
    items: Any = result
    if isinstance(result, Mapping):
        items = [u for key in ("units", "threats", "hostiles") for u in (result.get(key) or ())]
    elif hasattr(result, "units"):
        items = result.units
    try:
        return _dedupe(_normalise(list(items)))
    except TypeError:
        return []


# --------------------------------------------------------- leitura do estado


def _health(state: Any) -> dict[str, Any]:
    health = getattr(getattr(state, "metrics", None), "health_injuries", None)
    if health is None:
        return {}
    wounds = getattr(health, "wound_count", None)
    return {
        "status": _clean(getattr(health, "status", None), 40) or None,
        "wounds": wounds if isinstance(wounds, int) and not isinstance(wounds, bool) else None,
        "pain": _num(getattr(health, "pain", None)),
        "blood": _num(getattr(health, "blood_current", None)),
        "conditions": frozenset(
            _clean(c, 30) for c in (getattr(health, "status_conditions", None) or ()) if c
        ),
    }


def _position(state: Any) -> tuple[int | None, int | None, int | None] | None:
    """``(x, y, z)`` do aventureiro (``z`` pode faltar); ``None`` sem ``x``/``y``."""
    location = getattr(getattr(state, "metrics", None), "location", None)
    coords = [getattr(location, axis, None) for axis in ("x", "y", "z")]
    if coords[0] is None or coords[1] is None:
        adventurer = getattr(state, "adventurer", None)
        coords = [getattr(adventurer, axis, None) for axis in ("x", "y", "z")]
    numbers = [int(c) if isinstance(c, (int, float)) and not isinstance(c, bool) else None
               for c in coords]
    return None if numbers[0] is None or numbers[1] is None else (numbers[0], numbers[1], numbers[2])


def _moved(before: tuple[Any, ...] | None, now: tuple[Any, ...] | None) -> str | None:
    """Linha ``Moved: …`` quando ``x``/``y`` (ou ``z``, se ambos o têm) mudaram."""
    if not before or not now:
        return None
    if before[:2] == now[:2] and (before[2] is None or now[2] is None or before[2] == now[2]):
        return None
    show = lambda pos: ",".join("?" if c is None else str(c) for c in pos)  # noqa: E731
    return f"Moved: ({show(before)}) -> ({show(now)})."


def _events(state: Any) -> list[str]:
    texts = (e if isinstance(e, str) else getattr(e, "text", "")
             for e in (getattr(state, "recent_events", None) or ()))
    return [t.strip() for t in texts if isinstance(t, str) and t.strip()]


def _screen_text(state: Any) -> str:
    text = getattr(state, "screen_text", "")
    return text if isinstance(text, str) else ""


def _markers(text: str) -> dict[str, str]:
    """``{marcador: frase}`` presentes no ecrã (lista fechada, texto limitado)."""
    window = text[:MAX_SCREEN_SCAN_CHARS]
    return {name: phrase for name, phrase, pattern in _SCREEN_MARKERS if pattern.search(window)}


def _screen_from_result(result: Any) -> str:
    """Texto do ecrã de um ``read_screen()``/``screen()``: str, (focus, text) ou mapa."""
    if isinstance(result, str):
        return result
    if isinstance(result, (tuple, list)):
        texts = [item for item in result if isinstance(item, str)]
        return texts[1] if len(result) == 2 and len(texts) == 2 else max(texts, key=len, default="")
    for name in ("screen_text", "text", "screen"):
        value = _pick(result, name)
        if isinstance(value, str):
            return value
    return ""


def _targets(candidates: Any) -> list[str]:
    """Nomes de alvo dos candidatos que agem sobre uma criatura (attack/hunt/talk)."""
    out: list[str] = []
    try:
        items = list(candidates or ())[:32]
    except TypeError:
        return out
    for candidate in items:
        if action_verb(candidate) not in ("attack", "hunt", "talk"):
            continue
        if isinstance(candidate, str):
            target: Any = candidate.partition(":")[2].replace("_", " ")
        else:
            target = _pick(candidate, "target") or _pick(_pick(candidate, "params"), "target_name")
        name = _clean(target, MAX_NAME_CHARS)
        if name and name.lower() not in ("prey", "food", "water", "here") \
                and name.lower() not in {t.lower() for t in out}:
            out.append(name)
        if len(out) >= MAX_TARGETS:
            break
    return out


def _matches(target: str, unit: _Unit) -> bool:
    """Mesmo nome, ou um contido no outro **como palavras inteiras** (``wolf`` ⊂
    ``giant wolf``, mas ``cat`` ⊄ ``bobcat``)."""
    a, b = target.lower().replace("_", " "), unit.key.replace("_", " ")

    def inside(small: str, big: str) -> bool:
        return len(small) >= 3 and re.search(rf"(?<!\w){re.escape(small)}(?!\w)", big) is not None

    return a == b or inside(a, b) or inside(b, a)


# --------------------------------------------------------------- as linhas


def _hostile_line(first: Sequence[_Unit] | None, fresh: Sequence[_Unit]) -> str | None:
    now = [u for u in fresh if u.hostile]
    near = now[0] if now else None
    if first is None:
        return (f"Hostiles in view: {len(now)}; nearest {near.name} {near.where()}"
                f"{near.flag(hostile=False)}.") if near else None
    before = [u for u in first if u.hostile]
    was = before[0] if before else None
    if not now:
        return f"Hostiles: none in view now (first read: {len(before)})." if before else None
    moved = (was is None or was.key != near.key or was.direction != near.direction
             or (was.distance is not None and near.distance is not None
                 and abs(was.distance - near.distance) >= 1.0))
    if len(now) == len(before) and not moved:
        return None
    text = (f"Hostiles: {len(now)} in view (first read: {len(before)}); "
            f"nearest {near.name} {near.where()}{near.flag(hostile=False)}")
    if was is not None and was.key == near.key and None not in (was.distance, near.distance) \
            and was.distance != near.distance:
        text += f" (was {was.distance:g} tiles: " \
                f"{'closing in' if near.distance < was.distance else 'moving away'})"
    return text + "."


def _health_line(first: Mapping[str, Any], fresh: Mapping[str, Any]) -> str | None:
    if not first or not fresh:
        return None
    parts: list[str] = []
    for label, key, fmt in (("status", "status", str), ("wounds", "wounds", str),
                            ("pain", "pain", "{:g}".format), ("blood", "blood", "{:g}".format)):
        now, was = fresh.get(key), first.get(key)
        if now is not None and now != was:
            parts.append(f"{label} {fmt(was) if was is not None else '?'} -> {fmt(now)}")
    gained = sorted(fresh.get("conditions", frozenset()) - first.get("conditions", frozenset()))
    if gained:
        parts.append("new condition: " + ", ".join(gained))
    return ("Health: " + "; ".join(parts) + ".") if parts else None


def _target_lines(targets: Sequence[str], first: Sequence[_Unit] | None,
                  fresh: Sequence[_Unit]) -> list[str]:
    """Onde estão agora os alvos que os candidatos visam (só se mudou)."""
    lines: list[str] = []
    for target in targets:
        now = next((u for u in fresh if _matches(target, u)), None)
        if first is None:  # sem 1.ª leitura não há «antes»: descreve-se o agora
            if now is not None:
                lines.append(f"Target {target}: {now.where()}{now.flag()}.")
            continue
        was = next((u for u in first if _matches(target, u)), None)
        if now is not None and (was is None or (now.distance, now.direction) != (was.distance, was.direction)):
            seen = f"first read: {was.where()}" if was is not None else "not seen in the first read"
            lines.append(f"Target {target}: {now.where()} ({seen}).")
        elif now is None and was is not None:
            lines.append(f"Target {target}: no longer in view (first read: {was.where()}).")
    return lines


def _roster_lines(first: Sequence[_Unit] | None, fresh: Sequence[_Unit]) -> list[str]:
    """Quem entrou/saiu de vista (por nome, com multiplicidade)."""
    if first is None:
        return []
    remaining = [u.key for u in first]
    arrived: list[_Unit] = []
    for unit in fresh:
        if unit.key in remaining:
            remaining.remove(unit.key)
        else:
            arrived.append(unit)
    left = [u for u in first if u.key in remaining]

    def listing(label: str, units: list[_Unit], text: Callable[[_Unit], str]) -> str:
        more = f" (+{len(units) - MAX_UNITS_LISTED} more)" if len(units) > MAX_UNITS_LISTED else ""
        return f"{label}: " + ", ".join(text(u) for u in units[:MAX_UNITS_LISTED]) + more + "."

    lines = []
    if arrived:
        lines.append(listing("New in view", arrived, lambda u: f"{u.name} {u.where()}{u.flag()}"))
    if left:
        lines.append(listing("Out of view", left, lambda u: f"{u.name} (was {u.where()})"))
    return lines


def _wide_line(near: Sequence[_Unit], wide: Sequence[_Unit], radius: int) -> str | None:
    """Criaturas que só a varredura larga viu (por nome, com multiplicidade)."""
    remaining = [u.key for u in near]
    extra: list[_Unit] = []
    for unit in wide:
        if unit.key in remaining:
            remaining.remove(unit.key)
        else:
            extra.append(unit)
    if not extra:
        return None
    hostile = [u for u in extra if u.hostile]
    shown = (hostile or extra)[:MAX_UNITS_LISTED]
    return (f"Wide scan (radius {radius}): {len(extra)} more creature(s) beyond the near read, "
            f"{len(hostile)} hostile: "
            + ", ".join(f"{u.name} {u.where()}{u.flag(hostile=not hostile)}" for u in shown) + ".")


def _marker_line(first_text: str | None, fresh_text: str) -> str | None:
    now = _markers(fresh_text)
    if first_text is None:
        return ("Screen: " + "; ".join(now.values()) + ".") if now else None
    before = _markers(first_text)
    parts = []
    if any(name not in before for name in now):
        parts.append("now " + "; ".join(p for n, p in now.items() if n not in before))
    if any(name not in now for name in before):
        parts.append("no longer shown: " + "; ".join(p for n, p in before.items() if n not in now))
    return ("Screen: " + " | ".join(parts) + ".") if parts else None


# ----------------------------------------------------------------- Evidence


@dataclass(frozen=True)
class Evidence:
    """O que uma escalada colheu (imutável; nunca contém texto cru do ecrã).

    ``lines`` = linhas EN do que **mudou** face à 1.ª leitura; ``queries`` =
    consultas efetivamente feitas; ``fresh_state`` = o ``GameState`` fresco
    (``None`` se o snapshot falhou; permite regenerar candidatos); ``calls`` =
    chamadas feitas ao cliente; ``errors`` = falhas registadas (a recolha nunca
    levanta); ``reason`` = motivo de encaminhamento; ``elapsed_s`` = duração.
    """

    lines: tuple[str, ...] = ()
    queries: tuple[str, ...] = ()
    fresh_state: Any = None
    calls: int = 0
    errors: tuple[str, ...] = ()
    reason: str = ""
    elapsed_s: float = 0.0

    @property
    def informative(self) -> bool:
        """Houve mudança que justifique re-decidir? (sem linhas, não)."""
        return bool(self.lines)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly (vai para ``source_data`` da pergunta e para o log)."""
        return {
            "reason": self.reason, "lines": list(self.lines), "queries": list(self.queries),
            "calls": int(self.calls), "errors": list(self.errors),
            "elapsed_s": round(float(self.elapsed_s), 3),
        }


class EvidenceCollector:
    """Colhe evidência **só de leitura** antes de pedir ajuda ao humano.

    Args:
        game: cliente do jogo (``GameStateClient`` ou fake); só ``snapshot()``
            é obrigatório (ver a superfície *duck typed* do módulo).
        radius: raio da varredura larga (tiles).
        max_queries: teto de consultas por escalada (0 desliga).
        settle_s: pausa curta antes da releitura (0 = nenhuma; ≤ 5 s).
        sleeper: ``sleep`` injetável (``None`` ⇒ ``time.sleep`` na hora).
        budget_s: orçamento total de tempo (verificado entre consultas).
        clock: relógio monotónico injetável.
        max_lines: teto de linhas devolvidas.
    """

    def __init__(
        self,
        game: Any,
        *,
        radius: int = DEFAULT_RADIUS,
        max_queries: int = DEFAULT_MAX_QUERIES,
        settle_s: float = DEFAULT_SETTLE_S,
        sleeper: Callable[[float], Any] | None = None,
        budget_s: float = DEFAULT_BUDGET_S,
        clock: Callable[[], float] | None = None,
        max_lines: int = MAX_LINES,
    ) -> None:
        self.game = game
        self.radius = radius if isinstance(radius, int) and not isinstance(radius, bool) \
            and radius > 0 else DEFAULT_RADIUS
        self.max_queries = max(0, min(MAX_QUERIES, int(_num(max_queries) or 0)))
        settle = _num(settle_s)
        self.settle_s = 0.0 if settle is None else max(0.0, min(MAX_SETTLE_S, settle))
        budget = _num(budget_s)
        self.budget_s = DEFAULT_BUDGET_S if budget is None or budget <= 0 else budget
        self.max_lines = max(1, int(_num(max_lines) or MAX_LINES))
        self._sleeper = sleeper
        self._clock = clock
        self._last_step: Any = None

    def _now(self) -> float:
        try:
            return float((self._clock or time.monotonic)())
        except Exception:  # noqa: BLE001 - um relógio injetado partido não derruba a recolha
            return time.monotonic()

    def collect(self, state: Any, reason: str, candidates: Any = (), *, step: Any = None) -> Evidence:
        """Releitura fresca + varredura larga + ecrã; devolve o que **mudou**.

        Recusa (``Evidence`` vazia, zero chamadas, ``errors`` explica) quando
        ``reason`` não é escalável ou já se recolheu neste ``step``. **Nunca
        levanta e nunca age no jogo.**
        """
        text = _clean(reason, 40)
        started = self._now()
        if not can_escalate(text):
            kind = "a human gate" if text in HUMAN_GATE_REASONS else "not an escalatable reason"
            return Evidence(reason=text, errors=(f"reason {text!r} is {kind}: no evidence collected",))
        if step is not None and step == self._last_step:
            return Evidence(reason=text, errors=(f"evidence already collected for step {step!r}",))
        if step is not None:
            self._last_step = step
        if self.max_queries <= 0:
            return Evidence(reason=text)

        errors: list[str] = []
        queries: list[str] = []
        halted = False

        def fail(where: str, exc: BaseException) -> None:
            nonlocal halted
            kind = "timeout" if _is_timeout(exc) else type(exc).__name__
            errors.append(f"{where}: {kind}: {_clean(exc, MAX_ERROR_CHARS)}".rstrip(": "))
            halted = halted or kind == "timeout"  # o jogo não responde: não se espera por mais
            log.debug("evidence %s failed", where, exc_info=True)

        def query(name: str, func: Callable[[], Any] | None) -> tuple[Any, bool]:
            """Uma consulta (se permitida): ``(valor, ok)``; nunca levanta."""
            if func is None or halted or len(queries) >= self.max_queries \
                    or self._now() - started >= self.budget_s:
                return None, False
            queries.append(name)
            try:
                return func(), True
            except Exception as exc:  # noqa: BLE001 - a recolha nunca derruba o passo
                fail(name, exc)
                return None, False

        fresh: Any = None
        wide: list[_Unit] | None = None
        screen: str | None = None
        try:
            if self.settle_s > 0:
                try:
                    (self._sleeper or time.sleep)(self.settle_s)
                except Exception as exc:  # noqa: BLE001
                    fail("settle", exc)
            snapshot = getattr(self.game, "snapshot", None)
            if not callable(snapshot):
                errors.append("snapshot: game client has no snapshot()")
            value, ok = query(QUERY_SNAPSHOT, snapshot if callable(snapshot) else None)
            fresh = value if ok else None
            value, ok = query(QUERY_WIDE_SCAN, self._wide_scan_callable())
            wide = _units_from_scan(value) if ok else None
            value, ok = query(QUERY_SCREEN, self._screen_callable())
            screen = _screen_from_result(value) if ok else None
        except Exception as exc:  # noqa: BLE001 - defesa final
            fail("collect", exc)

        lines: list[str] = []
        try:
            lines = self._lines(state, fresh, wide, screen, candidates)
        except Exception as exc:  # noqa: BLE001
            fail("diff", exc)
        return Evidence(
            lines=tuple(lines[: self.max_lines]), queries=tuple(queries), fresh_state=fresh,
            calls=len(queries), errors=tuple(errors), reason=text,
            elapsed_s=max(0.0, self._now() - started),
        )

    def _wide_scan_callable(self) -> Callable[[], Any] | None:
        hook = getattr(self.game, "scan_units", None)
        if callable(hook):
            try:
                takes_radius = bool(inspect.signature(hook).parameters)
            except (TypeError, ValueError):
                takes_radius = True
            return (lambda: hook(self.radius)) if takes_radius else hook
        current = getattr(self.game, "radius", None)
        if (isinstance(current, int) and not isinstance(current, bool) and self.radius > current
                and callable(getattr(self.game, "snapshot", None))):
            try:
                clone = copy.copy(self.game)
                clone.radius = self.radius
            except Exception:  # noqa: BLE001 - cliente imutável/exótico: sem varredura larga
                return None
            return clone.snapshot
        return None

    def _screen_callable(self) -> Callable[[], Any] | None:
        for name in ("read_screen", "screen"):
            hook = getattr(self.game, name, None)
            if callable(hook):
                return hook
        return None

    def _lines(self, state: Any, fresh: Any, wide: list[_Unit] | None, screen: str | None,
               candidates: Any) -> list[str]:
        """Diferenças por ordem de importância (o teto de linhas corta o fim)."""
        baseline = state is not None
        first = _units_from_state(state) if baseline else None
        near = _units_from_state(fresh) if fresh is not None else (first or [])
        out: list[str | None] = []
        if fresh is not None:
            out.extend(_target_lines(_targets(candidates), first, near))
            out.append(_hostile_line(first, near))
            if baseline:
                out.append(_health_line(_health(state), _health(fresh)))
        shown = screen or (_screen_text(fresh) if fresh is not None else "")
        if shown:
            out.append(_marker_line(_screen_text(state) if baseline else None, shown))
        if wide is not None:
            out.append(_wide_line(near, wide, self.radius))
        if fresh is not None:
            out.extend(_roster_lines(first, near))
            if baseline:
                out.append(_moved(_position(state), _position(fresh)))
                was, is_now = _clean(getattr(state, "focus", None), 60), _clean(getattr(fresh, "focus", None), 60)
                if was and is_now and was != is_now:
                    out.append(f"Focus: {was} -> {is_now}.")
                seen = set(_events(state))
                out.extend(f"New log: '{_clean(t, MAX_EVENT_CHARS)}'"
                           for t in [t for t in _events(fresh) if t not in seen][-MAX_NEW_EVENTS:])
        return [_clean(line, MAX_LINE_CHARS) for line in out if line]


# ----------------------------------------------------------------- apêndice


def evidence_appendix(evidence: Any, char_budget: int = DEFAULT_CHAR_BUDGET) -> str:
    """Bloco ``EXTRA EVIDENCE …`` para juntar ao resumo do estado (≤ ``char_budget``).

    ``""`` quando não há linhas ou o orçamento não cobre o cabeçalho e uma
    linha curta. Linhas que não cabem são omitidas com ``(+N more)``; o
    orçamento nunca é excedido.
    """
    raw = getattr(evidence, "lines", evidence)
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Iterable):
        return ""
    lines = [line for line in (_clean(item, MAX_LINE_CHARS) for item in raw if isinstance(item, str)) if line]
    budget = _num(char_budget)
    header = EVIDENCE_HEADER + ":"
    if not lines or budget is None or budget < len(header) + len("\n- ") + 24:
        return ""
    out, used = [header], len(header)
    for index, line in enumerate(lines):
        entry, room = f"- {line}", int(budget) - used - 1  # o '\n' que antecede a entrada
        if len(entry) > room:
            if index > 0:
                note = f"- (+{len(lines) - index} more)"
                if len(note) <= room:
                    out.append(note)
                break
            entry = entry[: room - 1].rstrip() + "…"  # a 1.ª linha cabe sempre truncada
        out.append(entry)
        used += 1 + len(entry)
    return "\n".join(out)


def question_context(context: str, evidence: Any, char_budget: int = DEFAULT_CHAR_BUDGET) -> str:
    """Contexto da pergunta humana com a evidência **à cabeça**.

    A pergunta de aprovação do loop corta o contexto aos 800 primeiros
    caracteres (``context[:800]``): a evidência no fim perdia-se.
    """
    base = context if isinstance(context, str) else ""
    block = evidence_appendix(evidence, char_budget)
    return f"{block}\n\n{base}" if block and base else (block or base)


# ------------------------------------------------------------------ a porta


@dataclass(frozen=True)
class Redecision:
    """Veredicto da porta de re-decisão, com o motivo (para o registo)."""

    accepted: bool
    reason: str


def judge_redecision(
    first_band: Any,
    second_band: Any,
    first_action_id: Any,
    second_action_id: Any,
    auto_threshold: float = DEFAULT_AUTO_THRESHOLD,
    hitl_threshold: float = DEFAULT_HITL_THRESHOLD,
    *,
    second_confidence: float | None = None,
    second_needs_human: bool | None = False,
    destructive_verbs: Iterable[str] = DESTRUCTIVE_VERBS,
) -> Redecision:
    """Aceita ou recusa a 2.ª leitura, dizendo porquê. **Nunca levanta.**

    Todas têm de valer: (1) a 2.ª banda é ``auto`` e, se a confiança vier, os
    **mesmos limiares** (os do loop) reproduzem ``auto``; (2) o ``needs_human``
    da 2.ª leitura não está levantado; (3) há ação concreta (não ``other``);
    (4) **sem inversão destrutiva**: se o ``id`` mudou e um dos lados tem verbo
    destrutivo, recusa-se — uma ação destrutiva só passa se a 1.ª leitura já a
    propunha. ``first_band`` só se regista: a 2.ª leitura justifica-se sozinha;
    não há regra que a facilite.
    """
    try:
        band = second_band.strip().lower() if isinstance(second_band, str) else ""
        if band != BAND_AUTO:
            return Redecision(False, "second_not_auto")
        validate_thresholds(auto_threshold, hitl_threshold)
        if second_confidence is not None and band_for_choice_or_score(
            second_confidence, auto_threshold, hitl_threshold
        ) != BAND_AUTO:
            return Redecision(False, "confidence_below_threshold")
        if second_needs_human:
            return Redecision(False, "needs_human")
        second = second_action_id.strip() if isinstance(second_action_id, str) else ""
        if not second:
            return Redecision(False, "no_second_action")
        if second.lower() in ("other", "none", "none_of_the_above"):
            return Redecision(False, "exit_option")
        first = first_action_id.strip() if isinstance(first_action_id, str) else ""
        destructive = {str(v).strip().lower() for v in destructive_verbs}
        if first != second and (action_verb(first) in destructive or action_verb(second) in destructive):
            return Redecision(False, "destructive_flip")
        return Redecision(True, "accepted")
    except Exception as exc:  # noqa: BLE001 - em dúvida, pergunta-se ao humano
        log.warning("judge_redecision failed (%s: %s); keeping the human in the loop",
                    type(exc).__name__, exc)
        return Redecision(False, "invalid_input")


def accept_redecision(
    first_band: Any,
    second_band: Any,
    first_action_id: Any,
    second_action_id: Any,
    auto_threshold: float = DEFAULT_AUTO_THRESHOLD,
    hitl_threshold: float = DEFAULT_HITL_THRESHOLD,
    *,
    second_confidence: float | None = None,
    second_needs_human: bool | None = False,
    destructive_verbs: Iterable[str] = DESTRUCTIVE_VERBS,
) -> bool:
    """``True`` só se a 2.ª leitura atinge ``auto`` pelos mesmos limiares e não
    inverte um verbo destrutivo (regras em :func:`judge_redecision`). Senão o
    humano é perguntado — com a evidência no contexto (:func:`question_context`).
    ``auto_threshold``/``hitl_threshold`` são os **do loop**, nunca outros."""
    return judge_redecision(
        first_band, second_band, first_action_id, second_action_id, auto_threshold, hitl_threshold,
        second_confidence=second_confidence, second_needs_human=second_needs_human,
        destructive_verbs=destructive_verbs,
    ).accepted
