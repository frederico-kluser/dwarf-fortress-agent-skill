"""Políticas de combate DETERMINÍSTICAS: mira, golpe, arma, recuo e engajamento.

Resolve o limite "seleção de alvo/parte vital ainda manual; sem políticas de
recuo/gestão de armas" (``ARQUITETURA.md`` §9, ``PROBLEMAS-E-SOLUCOES.md`` H5).
São funções puras e pequenas — o executor e o gerador de candidatos chamam-nas;
não há I/O, relógio nem aleatoriedade (mesma entrada ⇒ mesma saída) e nenhuma
levanta: entrada ilegível devolve um valor neutro e seguro.

==============================  =============================================
função                          decide
==============================  =============================================
``weapon_kind_of``              tipo da arma pelo nome (``iron spear`` →
                                ``piercing``; ``copper buckler`` → ``shield``)
``attack_preference``           verbos do menu por tipo de arma (``punch`` último)
``choose_aim``                  parte a mirar (cabeça mata, pescoço paraliza)
``choose_attack_type``          melhor entrada do menu "Attack <parte>:"
``rank_attack_types``           o mesmo, ordenado e com razões
``wounds_from_events``          partes já acertadas no alvo (gamelog)
``weapon_state_from_events``    ``ok``/``lodged``/``dropped``/``broken``
``weapon_management_advice``    recuperar/trocar a arma (``lodged`` → um passo)
``blood_ratio_of``              ``blood_current / blood_max`` defensivo
``retreat_advice``              recuar? (sangue < 60%, dor, exaustão, …)
``engagement_advice``           ``go`` / ``avoid`` / ``needs_approval``
==============================  =============================================

Factos (wiki do DF 53.16 na base partilhada — fonte *untrusted*, citada, não
obedecida): o pescoço contém a coluna superior e o dano pode causar paralisia
total; só a cabeça mata de um golpe com armas contundentes/punhos; armas
cortantes decapitam ("finish off by attacking the neck or throat"); lanças/piques
ficam presas e soltam-se com um turno de luta ou **um passo em qualquer
direção**; o ataque manual (``A``) pede confirmação se a criatura ainda não está
em conflito. Prova ao vivo (``caca-2``): pescoço → "falls over", cabeça →
"has been struck down"; com a lança presa faltaram ``stab``, ``bash`` e ``punch``
no menu.

Decisões (finais; todos os limiares são constantes nomeadas para a calibragem):

* mira: **cabeça primeiro** (mata); pescoço se a cabeça não está no menu; com o
  pescoço já ferido o alvo está paralisado e acaba-se na cabeça; contra um alvo
  perigoso, com skill ≥ :data:`HIGH_SKILL_LEVEL` e arma cortante/perfurante,
  paraliza-se primeiro. Contundente: cabeça e, só se inalcançável, tronco. Perna
  **nunca**, salvo ``immobilise=True``.
* a prontidão (``readiness``) é lida por duck-typing (mapa ou objeto) — não há
  dependência do módulo de prontidão (mudança 02).
* recuo: sangue < ``threshold`` (0.6, mudança 02) ou fatores compostos; "grande
  perigo" no ALVO é aprovação, não recuo; **sapientes** exigem aprovação humana
  explícita (``approved`` nunca levanta um ``avoid`` de estado próprio).
"""
from __future__ import annotations

import functools
import itertools
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, NamedTuple, Sequence

from ..game.attack_chain import (
    ATTACK_VERBS,
    DEFAULT_ATTACK_PREF,
    canonical_aim,
    looks_sapient,
    parse_attack_label,
    target_matches,
)

__all__ = [
    "AIM_ORDER", "ATTACK_PREFERENCE", "CAUTION_MARGIN", "CRITICAL_BLOOD_RATIO", "DECISIONS",
    "DEFAULT_RETREAT_THRESHOLD", "HIGH_SKILL_LEVEL", "LOW_SKILL_LEVEL", "MAX_EVENTS",
    "MAX_EVENT_CHARS", "MAX_OPTIONS", "PAIN_HIGH", "PAIN_SEVERE", "URGENCY_LEVELS",
    "WEAPON_KINDS", "WEAPON_STATES", "AimChoice", "EngagementDecision", "RankedAttack",
    "RetreatDecision", "WeaponAdvice", "WeaponStateReport", "attack_preference",
    "blood_ratio_of", "choose_aim", "choose_attack_type", "engagement_advice",
    "rank_attack_types", "retreat_advice", "weapon_kind_of", "weapon_management_advice",
    "weapon_state_from_events", "weapon_state_report", "wounds_from_events",
]

# ------------------------------------------------------------------ vocabulário

WEAPON_KINDS: tuple[str, ...] = (
    "edged", "piercing", "blunt", "ranged", "unarmed", "shield", "unknown")
WEAPON_STATES: tuple[str, ...] = ("ok", "lodged", "dropped", "broken")
URGENCY_LEVELS: tuple[str, ...] = ("none", "low", "medium", "high", "critical")
DECISIONS: tuple[str, ...] = ("go", "avoid", "needs_approval")

#: Limiar de fuga por omissão: sangue < 60% (mudança 02 da ``ARQUITETURA.md``).
DEFAULT_RETREAT_THRESHOLD = 0.6
#: Abaixo disto o recuo é crítico (derivado: nunca acima do limiar pedido).
CRITICAL_BLOOD_RATIO = 0.35
#: Faixa de cautela acima do limiar (aviso ``low``, ainda sem recuo).
CAUTION_MARGIN = 0.2
#: Dor (``counters.pain`` do snapshot): "pesada" (recuar antes do colapso) e a de
#: desmaio (wiki ``wound.md``: 150+); alinhado com ``game/readiness.py``. Heurística.
PAIN_HIGH, PAIN_SEVERE = 75.0, 150.0
#: Skill de combate (``getEffectiveSkill``, escala 0-based do DF: Dabbling = 0):
#: Adequate = 2 · Proficient = 5 (limiar da wiki para sparring, mudança 11).
LOW_SKILL_LEVEL, HIGH_SKILL_LEVEL = 2.0, 5.0
#: Teto de eventos do gamelog lidos e de caracteres por evento.
MAX_EVENTS, MAX_EVENT_CHARS, MAX_OPTIONS = 200, 300, 64

#: Ordem de mira por tipo de arma efetiva (a 1.ª alcançável ganha).
AIM_ORDER: Mapping[str, tuple[str, ...]] = {
    "edged": ("head", "neck", "upper body"),
    "piercing": ("head", "neck", "upper body"),
    "blunt": ("head", "upper body"),  # só a cabeça mata de um golpe contundente
}
#: Preferência de golpe (verbos do menu) por tipo de arma; ``punch`` por último.
ATTACK_PREFERENCE: Mapping[str, tuple[str, ...]] = {
    "edged": ("slash", "hack", "chop", "cut", "stab", "pierce", "bash", "strike", "punch", "kick"),
    "piercing": ("stab", "pierce", "bash", "strike", "punch", "kick"),
    "blunt": ("bash", "smash", "pummel", "batter", "strike", "punch", "kick"),
    "ranged": ("bash", "strike", "punch", "kick"),  # em corpo a corpo é uma clava
    "unarmed": ("punch", "kick", "strike", "bash"),
    "shield": ("punch", "kick", "strike", "bash"),  # o escudo não é arma
}
#: Dicas de ``target_kind`` para "alvo perigoso" / "foge" (texto livre do chamador).
_DANGEROUS_HINTS = ("danger", "megabeast", "titan", "large", "hostile", "great")
_PARTS: Mapping[str, tuple[str, ...]] = {
    "neck": ("neck", "throat", "spine", "spinal"),
    "head": ("head", "skull", "brain", "face", "jaw"),
    "upper body": ("upper body", "chest", "rib", "lung", "heart", "torso"),
    "lower body": ("lower body", "stomach", "guts", "abdomen"),
    "leg": ("leg", "foot", "hoof", "knee"),
}

_TOKEN = re.compile(r"[a-z]+")
_SHIELDS = frozenset({"shield", "buckler"})
_RANGED = frozenset({"crossbow", "bow", "longbow", "shortbow", "sling", "blowgun", "bolt",
                     "bolts", "arrow", "arrows"})
_PIERCING = frozenset({"spear", "pike", "dagger", "knife", "javelin", "trident", "pick",
                       "pickaxe", "rapier", "stiletto"})
_BLUNT = frozenset({"mace", "hammer", "warhammer", "maul", "flail", "club", "cudgel", "staff",
                    "quarterstaff", "morningstar", "truncheon", "baton", "scepter"})
_EDGED = frozenset({"sword", "scimitar", "axe", "battleaxe", "greataxe", "handaxe", "halberd",
                    "whip", "scourge", "falchion", "sabre", "saber", "katana", "cleaver",
                    "greatsword", "longsword", "shortsword", "broadsword"})
_UNARMED = frozenset({"fist", "fists", "hand", "hands", "none", "unarmed", "empty", "nothing",
                      "bare"})

# ---------------------------------------------------------------------- helpers


def _never_raises(
    fallback: Callable[[], Any]
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Política no caminho do loop nunca levanta: erro ⇒ valor neutro e seguro."""
    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except Exception:
                return fallback()
        return wrapper
    return decorate


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value == value and abs(value) != float("inf") else None


def _fraction(value: Any) -> float | None:
    """Rácio 0..1 (tolera percentagem 1<x≤100); inválido ⇒ ``None``."""
    number = _number(value)
    if number is None or number < 0:
        return None
    if number > 1.05:
        number = number / 100.0 if number <= 100 else None
    return None if number is None else min(1.0, number)


def blood_ratio_of(current: Any, maximum: Any) -> float | None:
    """``blood_current / blood_max`` do snapshot (0..1) ou ``None`` se não é calculável."""
    now, top = _number(current), _number(maximum)
    if now is None or top is None or top <= 0 or now < 0:
        return None
    return min(1.0, now / top)


def _as_list(value: Any, cap: int) -> list[Any]:
    """Qualquer iterável (inclusive gerador) → lista com teto; ``str``/mapa = 1 item."""
    if value is None:
        return []
    if isinstance(value, (str, Mapping)):
        return [value]
    try:
        return list(itertools.islice(value, cap))
    except TypeError:
        return []


def _lookup(source: Any, *names: str) -> Any:
    for name in names:
        value = source.get(name) if isinstance(source, Mapping) else getattr(source, name, None)
        if value is not None:
            return value
    return None


def _text_of(value: Any) -> str:
    """Texto de um evento/opção (``str``, mapa ``text``/``label``, objeto ``.text``)."""
    if isinstance(value, str):
        return value
    got = _lookup(value, "text", "label", "name")
    return got if isinstance(got, str) else ""


def _clean(text: str) -> str:
    return " ".join(re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", text).split())[:MAX_EVENT_CHARS]


def _wounded_parts(wounds: Any) -> set[str]:
    out: set[str] = set()
    for item in _as_list(wounds, MAX_EVENTS):
        text = (item if isinstance(item, str) else _clean(
            str(_lookup(item, "body_part", "part", "text") or ""))).lower()
        for part, words in _PARTS.items():
            if any(word in text for word in words):
                out.add(part)
    return out


# ------------------------------------------------------------------------ arma


@_never_raises(lambda: "unknown")
def weapon_kind_of(weapon_name: Any) -> str:
    """Tipo da arma pelo nome do item (um de :data:`WEAPON_KINDS`).

    Vazio/``None`` ⇒ ``unarmed``; nome não reconhecido ⇒ ``unknown`` (a política
    cai nos defaults do menu). ``training`` ⇒ ``blunt`` (armas de treino são
    contundentes); ``buckler``/``shield`` ⇒ ``shield`` — escudo não é arma,
    mesmo que o menu ofereça ``strike/copper buckler``.
    """
    if not isinstance(weapon_name, str) or not weapon_name.strip():
        return "unarmed"
    tokens = set(_TOKEN.findall(weapon_name.lower()))
    if not tokens:
        return "unknown"
    if tokens & _SHIELDS:
        return "shield"
    if "training" in tokens:
        return "blunt"
    if tokens & _RANGED:
        return "ranged"
    for kind, words in (("piercing", _PIERCING), ("blunt", _BLUNT), ("edged", _EDGED)):
        if tokens & words:
            return kind
    if "morning star" in weapon_name.lower() or "war hammer" in weapon_name.lower():
        return "blunt"
    return "unarmed" if tokens <= _UNARMED else "unknown"


def _effective_kind(weapon_kind: Any) -> str:
    kind = weapon_kind if weapon_kind in WEAPON_KINDS else "unknown"
    blunt_like = {"ranged": "blunt", "unarmed": "blunt", "shield": "blunt", "unknown": "blunt"}
    return blunt_like.get(kind, kind)


def attack_preference(weapon_kind: Any, aim: Any = None) -> tuple[str, ...]:
    """Verbos do menu por ordem de preferência para este tipo de arma.

    ``unknown`` usa :data:`DEFAULT_ATTACK_PREF` (``stab``, ``bash``, ``strike``,
    ``punch``). ``aim`` é aceite por simetria com :func:`choose_aim` (a ordem
    por arma já cobre as duas miras letais).
    """
    prefs = ATTACK_PREFERENCE.get(weapon_kind if isinstance(weapon_kind, str) else "")
    return prefs or DEFAULT_ATTACK_PREF


# ------------------------------------------------------------------------ mira


class AimChoice(NamedTuple):
    """Mira escolhida + razão (em inglês: entra nos registos/prompts)."""

    aim: str
    reason: str


@_never_raises(lambda: AimChoice("head", "internal error: documented lethal aim"))
def choose_aim(
    weapon_kind: Any,
    target_kind: Any = None,
    readiness: Any = None,
    wounds: Iterable[Any] = (),
    *,
    available: Sequence[str] | None = None,
    immobilise: bool = False,
) -> AimChoice:
    """Parte a mirar com a arma ``weapon_kind`` contra um alvo ``target_kind``.

    Args:
        weapon_kind: saída de :func:`weapon_kind_of`.
        target_kind: ``"animal"``/``"sapient"``/``"dangerous"``/``None`` (texto
            livre; "danger"/"megabeast"/"large"/"hostile" ⇒ alvo perigoso).
        readiness: mapa/objeto com ``weapon_skill`` (ou ``effective_skill``,
            ``skill``); desconhecido ⇒ política por omissão.
        wounds: partes já feridas no ALVO (texto do gamelog ou ``body_part``).
        available: mira(s) visíveis no menu (``None`` = assume tudo alcançável).
        immobilise: o alvo tem de ser imobilizado ⇒ perna (única exceção).
    """
    wounded = _wounded_parts(wounds)
    skill = _number(_lookup(readiness, "weapon_skill", "effective_skill", "skill",
                            "weapon_effective_skill")) if readiness is not None else None
    kind = _effective_kind(weapon_kind)
    note = " (sapient target: needs explicit human approval)" if (
        isinstance(target_kind, str) and target_kind.strip().lower() == "sapient") else ""
    reach = None if available is None else [canonical_aim(a) for a in available]

    if immobilise:
        legs = [a for a in (reach or ["leg"]) if "leg" in a or "hoof" in a or "foot" in a]
        aim = legs[0] if legs else "leg"
        return AimChoice(aim, f"immobilise: the target must not move, so aim at a leg{note}")

    order = list(AIM_ORDER.get(kind, AIM_ORDER["blunt"]))
    why = {"edged": "edged weapon: the head kills and slashing can sever the neck",
           "piercing": "piercing weapon: a stab to the head reaches the brain",
           "blunt": "blunt/unarmed: only the head can kill in one blow"}.get(kind, "")
    dangerous = isinstance(target_kind, str) and any(
        hint in target_kind.lower() for hint in _DANGEROUS_HINTS)
    skilled = skill is not None and skill >= HIGH_SKILL_LEVEL
    if kind in ("edged", "piercing") and dangerous and skilled:
        order = ["neck", "head", "upper body"]
        why = "skilled vs a dangerous target: paralyse at the neck first, finish at the head"
    if "neck" in wounded and "head" not in wounded:  # paralisado ("falls over")
        order = ["head"] + [a for a in order if a != "head"]
        why = "neck already wounded (spine): finish at the head (coup de grace)"
    if skill is not None and skill < LOW_SKILL_LEVEL:
        why += f"; low weapon skill ({skill:g}): keep to the larger lethal parts"
        order = [a for a in order if a != "neck"] or order

    for rank, aim in enumerate(order):
        if reach is None or aim in reach:
            skipped = f"; {order[0]} is not in the menu, using {aim}" if rank else ""
            return AimChoice(aim, why + skipped + note)
    if reach is not None:
        for aim in ("upper body", "lower body"):
            if aim in reach:
                return AimChoice(
                    aim, f"{why}; the preferred parts are unreachable, using {aim}{note}")
    return AimChoice(order[0], f"{why}; no known aim is reachable, keeping {order[0]}{note}")


# ---------------------------------------------------------------- tipo de golpe


class RankedAttack(NamedTuple):
    """Uma entrada do menu de golpes com a sua posição na preferência."""

    entry: Any
    verb: str
    score: int
    reason: str


@_never_raises(lambda: [])
def rank_attack_types(
    options: Iterable[Any], weapon_kind: Any, *, weapon_state: str = "ok", aim: Any = None,
) -> list[RankedAttack]:
    """Entradas do menu "Attack <parte>:" ordenadas do melhor para o pior.

    Só contam golpes (``ATTACK_VERBS``): agarrar/lançar/… ficam de fora. Com a
    arma ``lodged`` o menu pode já não ter ``stab`` (visto ao vivo) — o que
    sobrar é ordenado pela mesma preferência e a razão regista o recurso.
    """
    prefs = attack_preference(weapon_kind, aim)
    ranked: list[tuple[int, int, RankedAttack]] = []
    for index, option in enumerate(_as_list(options, MAX_OPTIONS)):
        label = _text_of(option)
        parsed = parse_attack_label(label) if label else None
        verb = parsed.verb if parsed else str(getattr(option, "verb", "") or "")
        if verb not in ATTACK_VERBS:
            continue
        if verb in prefs:
            score, how = prefs.index(verb), f"preferred #{prefs.index(verb) + 1} for {weapon_kind}"
        else:
            score, how = len(prefs) + ATTACK_VERBS.index(verb), "not in the weapon preference"
        if weapon_state == "lodged":
            how += "; weapon lodged: the menu may list fewer strikes"
        ranked.append((score, index, RankedAttack(option, verb, score, how)))
    return [item for _, _, item in sorted(ranked, key=lambda r: (r[0], r[1]))]


def choose_attack_type(
    options: Iterable[Any], weapon_kind: Any, *, weapon_state: str = "ok", aim: Any = None,
) -> Any:
    """Melhor entrada disponível do menu de golpes (ou ``None`` se não há golpe).

    Cortante: ``slash``/``hack`` e depois ``stab``; perfurante: ``stab`` e, com a
    ponta presa, ``bash`` (haste); contundente: ``bash``; ``punch`` por último.
    ``None`` com a arma presa e sem nenhum golpe listado ⇒ o chamador recupera a
    arma (:func:`weapon_management_advice`) em vez de carregar em "Done" às cegas.
    """
    ranked = rank_attack_types(options, weapon_kind, weapon_state=weapon_state, aim=aim)
    return ranked[0].entry if ranked else None


_HIT_LINE = re.compile(
    r"^you\s+(?:" + "|".join(ATTACK_VERBS) + r")e?s?\s+(?:the\s+)?(?P<target>[a-z][a-z' -]{1,40}?)"
    r"\s+in\s+the\s+(?P<part>[a-z][a-z' -]{1,30}?)(?=\s+(?:from|with)\b|[,.!]|$)")


@_never_raises(lambda: ())
def wounds_from_events(events: Iterable[Any], target_name: Any = None) -> tuple[str, ...]:
    """Partes que o aventureiro JÁ acertou no alvo, pelo gamelog (ordem, sem repetir).

    ``You stab the stray mule in the neck from the side with your iron spear…``
    → ``("neck",)``. Com ``target_name`` só contam linhas sobre essa espécie
    (os golpes que o aventureiro LEVA não são ferimentos do alvo). Alimenta
    ``choose_aim(wounds=…)``: pescoço já ferido ⇒ acabar na cabeça.
    """
    parts: list[str] = []
    for raw in _as_list(events, MAX_EVENTS):
        found = _HIT_LINE.match(_clean(_text_of(raw)).lower())
        if not found:
            continue
        if target_name and not target_matches(found.group("target"), target_name):
            continue
        part = found.group("part").strip()
        if part not in parts:
            parts.append(part)
    return tuple(parts)


# ----------------------------------------------------------- estado da arma


class WeaponStateReport(NamedTuple):
    """Estado da arma + a arma e a linha do gamelog que o provam."""

    state: str
    weapon: str
    evidence: str


#: "The iron spear has lodged firmly in the wound!" (a arma é do aventureiro: sem
#: posse "goblin's …") e "You maintain possession of …" (2.ª pessoa).
_LODGED_LINE = re.compile(
    r"\b(?:the|your)\s+(?P<w>[a-z' -]{2,40}?)\s+has\s+lodged\s+(?:firmly\s+)?in\b")
_LODGED = (r"\byou\s+maintain\s+possession\s+of\b",)
_FREED = (r"\bno\s+longer\s+lodged\b", r"\bcomes?\s+free\b",
          r"\byou\s+(?:pull|wrench|work)\b.*\b(?:out|free|loose)\b")
_STRIKE = re.compile(r"^you\s+(?:" + "|".join(ATTACK_VERBS) + r")e?s?\b.*\bwith\s+your\b")
_DROPPED = (r"\byou\s+drop\b", r"\byour\s+[\w' -]{2,40}\s+(?:is|was)\s+(?:knocked|torn|wrenched|"
            r"pulled)\s+(?:from|out\s+of)\b", r"\byou\s+lose\s+(?:your\s+)?grip\b")
_BROKEN = (r"\byour\s+[\w' -]{2,40}\s+(?:shatters|breaks|snaps|is\s+destroyed)\b",)
_RECOVERED = (r"\byou\s+(?:pick\s+up|wield|equip|draw|ready)\b",)
_WEAPON_NAME = re.compile(
    r"\b(?:the|your)\s+([a-z][a-z' -]{2,40}?)\s+(?:has|is|was|shatters|breaks)\b"
    r"|(?:possession\s+of|you\s+drop)\s+(?:the|your)\s+([a-z][a-z' -]{2,40}?)\s*[.!]")


def _any(patterns: Iterable[str], text: str) -> bool:
    return any(re.search(p, text) for p in patterns)


@_never_raises(lambda: WeaponStateReport("ok", "", ""))
def weapon_state_report(events: Iterable[Any]) -> WeaponStateReport:
    """Percorre o gamelog (do mais antigo ao mais novo) e devolve o estado FINAL.

    ``The iron spear has lodged firmly in the wound!`` → ``lodged`` (armas de
    outras unidades — ``The goblin's iron spear …`` — não contam); ``You
    maintain possession of …`` mantém ``lodged``; um golpe seguinte do
    aventureiro (``You stab … with your …``) volta a ``ok`` (a arma saiu) e uma
    nova linha de "lodged" nesse mesmo golpe volta a ``lodged``. ``dropped`` e
    ``broken`` exigem ``you``/``your`` (outras unidades largam as suas armas).
    Frases a verificar ao vivo; só as de ``lodged`` estão documentadas.
    """
    state, weapon, evidence = "ok", "", ""
    for raw in _as_list(events, MAX_EVENTS):
        line = _clean(_text_of(raw))
        low = line.lower()
        if not low:
            continue
        found = _WEAPON_NAME.search(low)
        name = (found.group(1) or found.group(2) or "").strip() if found else ""
        mine = _LODGED_LINE.search(low)
        if (mine is not None and "'s" not in mine.group("w")) or _any(_LODGED, low):
            state, weapon, evidence = "lodged", name or weapon, line
        elif state == "lodged" and _any(_FREED, low):
            state, evidence = "ok", line
        elif _any(_DROPPED, low):
            state, weapon, evidence = "dropped", name or weapon, line
        elif _any(_BROKEN, low):
            state, weapon, evidence = "broken", name or weapon, line
        elif state in ("dropped", "broken") and _any(_RECOVERED, low):
            state, evidence = "ok", line
        elif state == "lodged" and _STRIKE.match(low):
            state, evidence = "ok", line  # um golpe novo: a arma saiu da ferida
    return WeaponStateReport(state, weapon, evidence)


def weapon_state_from_events(events: Iterable[Any]) -> str:
    """``lodged`` | ``dropped`` | ``broken`` | ``ok`` pelas linhas do gamelog."""
    return weapon_state_report(events).state


class WeaponAdvice(NamedTuple):
    """Ação sobre a arma: ``keep`` | ``step_away`` | ``pick_up`` | ``swap`` | ``fists``."""

    action: str
    item: str
    reason: str
    direction: str = ""


_OPPOSITE = {"n": "s", "s": "n", "e": "w", "w": "e", "ne": "sw", "sw": "ne", "nw": "se", "se": "nw"}


@_never_raises(lambda: WeaponAdvice("keep", "", "internal error: keep the weapon"))
def weapon_management_advice(
    weapon_state: Any,
    equipped: Any = None,
    inventory: Iterable[Any] = (),
    *,
    target_direction: Any = None,
) -> WeaponAdvice:
    """O que fazer com a arma (``lodged`` → um passo; solta/partida → trocar).

    ``lodged``: a wiki documenta que **um passo em qualquer direção** puxa a
    lança/pique para fora (ou um turno de luta); ``direction`` é o oposto do
    alvo (afastar-se). ``swap``/``pick_up`` precisam de mapeamento de teclas do
    inventário (não verificado: o executor deixa-os como passos manuais).
    """
    state = weapon_state if weapon_state in WEAPON_STATES else "ok"
    item = _clean(equipped) if isinstance(equipped, str) else ""
    spare = next((name for name in (_clean(_text_of(i)) for i in _as_list(inventory, MAX_OPTIONS))
                  if name and name.lower() != item.lower()
                  and weapon_kind_of(name) in ("edged", "piercing", "blunt")), "")
    if state == "ok":
        return WeaponAdvice("keep", item, "weapon in hand and usable")
    if state == "lodged":
        away = _OPPOSITE.get(str(target_direction or "").strip().lower(), "")
        return WeaponAdvice(
            "step_away", item,
            "weapon lodged in the wound: moving one tile pulls it out "
            "(or spend a turn wrestling it)",
            away)
    if state == "dropped":
        if spare:
            return WeaponAdvice(
                "swap", spare, "weapon dropped: a spare weapon is faster than fetching it")
        return WeaponAdvice("pick_up", item, "weapon dropped: pick it up before engaging")
    if spare:
        return WeaponAdvice("swap", spare, "weapon broken: wield the spare weapon")
    return WeaponAdvice("fists", "", "weapon broken and no spare: only unarmed attacks are left")


# ------------------------------------------------------------------- recuo


@dataclass(frozen=True)
class RetreatDecision:
    """Recuar? ``urgency`` ∈ :data:`URGENCY_LEVELS`; ``reasons`` em inglês."""

    retreat: bool
    urgency: str = "none"
    reasons: tuple[str, ...] = ()

    @property
    def level(self) -> int:
        return URGENCY_LEVELS.index(self.urgency) if self.urgency in URGENCY_LEVELS else 0


@_never_raises(lambda: RetreatDecision(False, "none", ("internal error: no retreat advice",)))
def retreat_advice(
    blood_ratio: Any = None,
    pain: Any = None,
    exhausted: Any = False,
    stunned: Any = False,
    outnumbered: Any = False,
    great_danger: Any = False,
    threshold: Any = DEFAULT_RETREAT_THRESHOLD,
) -> RetreatDecision:
    """Recuar da luta atual? Regras (nível 0..4 → ``none``..``critical``).

    * sangue (``blood_current/blood_max``) < ``critical`` (0.35, nunca acima do
      limiar) → ``critical``; < ``threshold`` → ``medium``; faixa de cautela
      acima do limiar → ``low`` (sem recuo);
    * atordoado → ``high`` · dor ≥ ``PAIN_SEVERE`` → ``high``, ≥ ``PAIN_HIGH`` →
      ``medium`` · exausto / em inferioridade numérica / grande perigo → ``medium``
      (``exhausted``: passe ``True`` só acima do limiar do contador — a condição
      ``exhausted`` do snapshot acende com qualquer valor > 0);
    * dois ou mais fatores de nível ≥ ``medium`` somam +1 (máx. ``critical``).

    ``retreat`` é verdadeiro a partir de ``medium``. Valores ausentes/inválidos
    não contam como fator (a ausência de dados nunca provoca fuga).
    """
    limit = _number(threshold)
    limit = DEFAULT_RETREAT_THRESHOLD if limit is None else min(0.95, max(0.05, limit))
    ratio, level, reasons, strong = _fraction(blood_ratio), 0, [], 0

    def raise_to(value: int, reason: str) -> None:
        nonlocal level, strong
        level = max(level, value)
        strong += 1 if value >= 2 else 0
        reasons.append(reason)

    if ratio is not None:
        critical = min(CRITICAL_BLOOD_RATIO, limit)
        if ratio < critical:
            raise_to(4, f"blood loss critical ({ratio:.0%} of max)")
        elif ratio < limit:
            raise_to(2, f"blood {ratio:.0%} is below the {limit:.0%} retreat threshold")
        elif ratio < min(1.0, limit + CAUTION_MARGIN):
            raise_to(1, f"blood {ratio:.0%}: getting low (caution)")
    if bool(stunned):
        raise_to(3, "stunned: cannot fight back")
    hurt = _number(pain)
    if hurt is not None and hurt >= PAIN_SEVERE:
        raise_to(3, f"severe pain ({hurt:g})")
    elif hurt is not None and hurt >= PAIN_HIGH:
        raise_to(2, f"high pain ({hurt:g})")
    for flag, text in ((exhausted, "exhausted"), (outnumbered, "outnumbered"),
                       (great_danger, "great danger nearby")):
        if bool(flag):
            raise_to(2, text)
    if strong >= 2:
        level = min(4, level + 1)
        reasons.append("several retreat factors compound")
    return RetreatDecision(level >= 2, URGENCY_LEVELS[level], tuple(reasons))


# ------------------------------------------------------------- engajamento


@dataclass(frozen=True)
class EngagementDecision:
    """``go`` | ``avoid`` | ``needs_approval`` + razões + urgência do estado próprio."""

    decision: str
    reasons: tuple[str, ...] = ()
    urgency: str = "none"

    @property
    def go(self) -> bool:
        return self.decision == "go"

    @property
    def avoid(self) -> bool:
        return self.decision == "avoid"

    @property
    def needs_approval(self) -> bool:
        return self.decision == "needs_approval"


@_never_raises(lambda: EngagementDecision("needs_approval", ("internal error: ask a human",)))
def engagement_advice(
    target_name: Any = "",
    target_kind: Any = None,
    *,
    target_hostile: bool = False,
    great_danger: bool = False,
    blood_ratio: Any = None,
    pain: Any = None,
    exhausted: bool = False,
    stunned: bool = False,
    outnumbered: bool = False,
    weapon_state: Any = "ok",
    approved: bool = False,
    threshold: Any = DEFAULT_RETREAT_THRESHOLD,
) -> EngagementDecision:
    """Atacar ``target_name`` agora? ``go`` · ``avoid`` · ``needs_approval``.

    ``target_name`` aceita também um ``VisibleUnit`` (nome + raça, ``kind``,
    ``hostile`` e ``great_danger`` vêm dele, como em ``candidates``).

    Ordem (a primeira que casa):

    1. **estado próprio** pede recuo (:func:`retreat_advice` sem "grande perigo"
       do alvo) ⇒ ``avoid`` — nem a aprovação humana o levanta: curar-se/fugir
       primeiro;
    2. **sapiente** (``target_kind=="sapient"`` ou :func:`looks_sapient`), alvo de
       **grande perigo** ou arma perdida (``dropped``/``broken``) ⇒
       ``needs_approval``; ``approved=True`` (aprovação humana explícita para
       este alvo) deixa passar;
    3. senão ``go`` (``target_hostile`` ⇒ legítima defesa).
    """
    if target_name is not None and not isinstance(target_name, str):  # um VisibleUnit
        unit = target_name
        target_name = " ".join(str(_lookup(unit, f) or "") for f in ("name", "race")).strip()
        target_kind = target_kind if target_kind is not None else _lookup(unit, "kind")
        target_hostile = bool(target_hostile or _lookup(unit, "hostile"))
        great_danger = bool(great_danger or _lookup(unit, "great_danger"))
    own = retreat_advice(blood_ratio, pain, exhausted, stunned, outnumbered, False, threshold)
    if own.retreat:
        return EngagementDecision("avoid", own.reasons, own.urgency)
    asks: list[str] = []
    name = target_name if isinstance(target_name, str) else ""
    kind = target_kind.strip().lower() if isinstance(target_kind, str) else ""
    if kind == "sapient" or looks_sapient(name):
        asks.append("sapient target: attacking people needs explicit human approval")
    if bool(great_danger):
        asks.append("great danger target: needs human approval before engaging")
    if weapon_state in ("dropped", "broken"):
        asks.append(f"weapon {weapon_state}: fighting unarmed needs human approval")
    if asks and not approved:
        return EngagementDecision("needs_approval", tuple(asks), own.urgency)
    notes = list(own.reasons)
    if asks:
        notes.append("explicitly approved by a human")
    if target_hostile:
        notes.append("hostile target: self-defence")
    return EngagementDecision("go", tuple(notes), own.urgency)
