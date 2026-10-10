"""Classes de risco da ação e limiares de ``auto`` por classe (ARQUITETURA, mudança 06).

*Porquê.* "Olhar para o lado" não é "atacar": um único limiar de ``auto`` (0.90)
trata do mesmo modo uma ação que se desfaz (``explore``) e uma que não tem volta
(``attack``). Este módulo dá a cada macro-ação uma **classe de risco** e a cada
classe um **limiar de ``auto``** (política de ``docs/calibracao.md`` §5.3):

==============  ===============================================  ==============
Classe          Exemplos (vocabulário do loop)                   ``auto``
==============  ===============================================  ==============
``reversible``  explore, wait, navigate_to, talk, rest, pick_up  >= 0.90
``costly``      eat, drink, drink_potion, flee, hunt, trade      >= 0.95
``irreversible`` attack em presa/hostil não-sapiente, drop       >= 0.95
``destructive`` attack em sapiente, steal, ação marcada          >= 0.97 **ou** ``auto`` desligado
==============  ===============================================  ==============

Regras que este módulo garante (todas por código, nunca por prompt):

* **A banda é teto, não permissão.** Nada aqui concede autonomia: o limiar de uma
  classe só pode **subir** acima do piso da política. Uma tabela de calibração
  (:mod:`~system_one_dwarf.jev.calibration`) pode baixá-lo apenas quando a
  entrada está explicitamente ``verified`` **e** tem suporte suficiente
  (``MIN_SUPPORT_TO_LOWER``), nunca abaixo de ``LOWEST_AUTO_THRESHOLD`` e nunca
  na classe ``destructive``.
* ``has_injection=true`` limita a ``hitl`` **sempre** (a injeção detetada só pode
  reduzir autonomia).
* ``destructive`` tem ``auto`` **desligado** por omissão (``destructive_auto``):
  só com opt-in explícito chega a ``auto``, e então a >= 0.97.
* Verbo desconhecido ⇒ classe conservadora (``UNKNOWN_VERB_CLASS``): um verbo
  novo nunca herda os 0.90 por omissão.
* Nenhuma função pública levanta por entrada inválida: o pior caso é a classe
  mais conservadora / a banda ``hitl`` (o loop não pode morrer por um id estranho).

Decisões documentadas (valores nomeados — a pesquisa externa pode afiná-los):

* ``attack`` num alvo **sapiente** é ``destructive`` mesmo quando hostil
  (``HOSTILE_SAPIENT_CLASS``): ARQUITETURA §6 diz que sapientes "nunca são alvo
  sem aprovação humana explícita". Quem preferir a exceção de legítima defesa
  contra agressores explícitos muda essa constante para ``irreversible`` (a
  exigir >= 0.95); sapientes não hostis continuam ``destructive`` sempre.
* ``rest``/``sleep`` são ``reversible`` (mapeamento do loop); descansar num sítio
  não defendido é um risco *situacional* que o gate de segurança do gerador de
  candidatos já cobre (``rest`` só sem ameaça).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Mapping

from .bands import (
    BAND_AUTO,
    BAND_HITL,
    DEFAULT_AUTO_THRESHOLD,
    DEFAULT_HITL_THRESHOLD,
    band_for_choice_or_score,
    band_for_noul,
    cap_band,
)

log = logging.getLogger("system_one_dwarf.jev.risk")

__all__ = [
    "RISK_REVERSIBLE",
    "RISK_COSTLY",
    "RISK_IRREVERSIBLE",
    "RISK_DESTRUCTIVE",
    "RISK_CLASSES",
    "POLICY_FLOORS",
    "DESTRUCTIVE_AUTO_DEFAULT",
    "UNKNOWN_VERB_CLASS",
    "HOSTILE_SAPIENT_CLASS",
    "MIN_SUPPORT_TO_LOWER",
    "LOWEST_AUTO_THRESHOLD",
    "INJECTION_NOUL_THRESHOLD",
    "SAPIENT_NAME_WORDS",
    "VERB_CLASSES",
    "RiskPolicy",
    "RiskVerdict",
    "DEFAULT_POLICY",
    "classify_action",
    "risk_class",
    "risk_rank",
    "policy_floor",
    "auto_allowed",
    "thresholds_for",
    "class_thresholds",
    "auto_threshold_for_action",
    "has_injection_signal",
    "band_ceiling",
    "band_for_action",
    "target_risk",
]

# ------------------------------------------------------------------ vocabulary

RISK_REVERSIBLE = "reversible"
RISK_COSTLY = "costly"
RISK_IRREVERSIBLE = "irreversible"
RISK_DESTRUCTIVE = "destructive"

#: Da menos para a mais severa (a ordem é o ``risk_rank``).
RISK_CLASSES: tuple[str, str, str, str] = (
    RISK_REVERSIBLE,
    RISK_COSTLY,
    RISK_IRREVERSIBLE,
    RISK_DESTRUCTIVE,
)
_RANK = {name: rank for rank, name in enumerate(RISK_CLASSES)}

#: Pisos de ``auto`` por classe (``docs/calibracao.md`` §5.3). O piso do
#: ``reversible`` é o ``DEFAULT_AUTO_THRESHOLD`` dos limiares globais.
POLICY_FLOORS: dict[str, float] = {
    RISK_REVERSIBLE: 0.90,
    RISK_COSTLY: 0.95,
    RISK_IRREVERSIBLE: 0.95,
    RISK_DESTRUCTIVE: 0.97,
}

#: ``destructive`` com ``auto`` desligado por omissão (política "ou auto desligado").
DESTRUCTIVE_AUTO_DEFAULT = False

#: Classe de um verbo que não consta de :data:`VERB_CLASSES` (fail-safe).
UNKNOWN_VERB_CLASS = RISK_IRREVERSIBLE

#: Classe de um ``attack`` num sapiente que é um **agressor explícito**
#: (``category == "threat"`` ou ``hostile``). Um sapiente não hostil — ou um id
#: sem contexto — é sempre ``destructive``, seja qual for este valor.
HOSTILE_SAPIENT_CLASS = RISK_DESTRUCTIVE

#: Suporte mínimo (amostras da classe) para uma entrada ``verified`` poder BAIXAR
#: o limiar abaixo do piso. 3× o mínimo de 30 que qualquer ajuste por dados exige.
MIN_SUPPORT_TO_LOWER = 100

#: Mesmo verificada, uma classe nunca desce abaixo disto (sanidade: a banda
#: ``hitl`` começa em 0.50 e ``auto`` tem de ser claramente mais exigente).
LOWEST_AUTO_THRESHOLD = 0.80

#: ``p`` do ``noul`` ``has_injection`` a partir do qual há injeção (0.5 = "mais
#: provável que sim"; é um sinal de redução de autonomia, por isso a favor do alarme).
INJECTION_NOUL_THRESHOLD = 0.5

#: Espelho de ``agent.candidates.SAPIENT_WORDS`` (a camada ``jev`` não importa o
#: agente; um teste garante que esta lista **contém** a do gerador de candidatos).
SAPIENT_NAME_WORDS: tuple[str, ...] = (
    "human", "dwarf", "elf", "goblin", "hobgoblin", "kobold", "orc", "troll",
    "gnome", "gremlin", "necromancer", "vampire", "ghost", "demon", "devil",
    "werebeast", "were-", "nightwraith", "phantom", "siren", "merperson",
    # extras (papéis de pessoa e plurais irregulares): só alargam a classe
    # ``destructive``, a direção segura.
    "dwarves", "elves", "werewolf", "villager", "merchant", "farmer", "hunter",
    "peasant", "soldier", "guard", "bandit", "adventurer", "traveler", "traveller",
    "person",
)

#: Verbo (já normalizado) → classe. Estende-se por dados, não por ``if``.
VERB_CLASSES: dict[str, str] = {
    **{verb: RISK_REVERSIBLE for verb in (
        "explore", "wait", "navigate", "navigate_to", "goto", "move", "walk", "talk",
        "rest", "sleep", "pick_up", "pickup", "look", "ignore", "keep", "freeze",
        "hide", "search", "shout", "turn_back", "break_camp", "climb", "train",
        "other", "none",
    )},
    **{verb: RISK_COSTLY for verb in (
        "eat", "drink", "drink_potion", "flee", "hunt", "trade", "apply", "throw",
        "fish", "build", "dig", "take", "press", "click",
    )},
    **{verb: RISK_IRREVERSIBLE for verb in ("attack", "drop", "bury", "cross")},
    **{verb: RISK_DESTRUCTIVE for verb in (
        "steal", "murder", "kill", "betray", "assassinate",
    )},
}

_TRUE_TEXT = ("1", "true", "yes", "y", "on")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return not math.isnan(float(value)) and float(value) != 0.0
    return isinstance(value, str) and value.strip().lower() in _TRUE_TEXT


# ---------------------------------------------------------------------- policy


@dataclass(frozen=True)
class RiskPolicy:
    """Todos os números da política num só sítio (injetável, sem mágicos inline).

    ``floors`` mapeia classe → piso de ``auto``; ``destructive_auto`` liga o
    ``auto`` da classe destrutiva (a >= ``floors["destructive"]``);
    ``min_support_to_lower``/``lowest_auto_threshold`` governam quando uma
    tabela *verificada* pode baixar um limiar.
    """

    floors: Mapping[str, float] = field(default_factory=lambda: dict(POLICY_FLOORS))
    destructive_auto: bool = DESTRUCTIVE_AUTO_DEFAULT
    unknown_class: str = UNKNOWN_VERB_CLASS
    hostile_sapient_class: str = HOSTILE_SAPIENT_CLASS
    min_support_to_lower: int = MIN_SUPPORT_TO_LOWER
    lowest_auto_threshold: float = LOWEST_AUTO_THRESHOLD

    def floor(self, risk_class: str) -> float:
        value = self.floors.get(risk_class) if isinstance(self.floors, Mapping) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isnan(value):
            value = POLICY_FLOORS.get(risk_class, POLICY_FLOORS[RISK_DESTRUCTIVE])
        return min(1.0, max(0.0, float(value)))


DEFAULT_POLICY = RiskPolicy()


def _policy(policy: Any) -> RiskPolicy:
    """Uma política inválida (``None``, lixo) nunca derruba o loop: usa a omissão."""
    return policy if isinstance(policy, RiskPolicy) else DEFAULT_POLICY


@dataclass(frozen=True)
class RiskVerdict:
    """Classe de risco + porquê (para o log/UI: "porque é que o auto não vale")."""

    risk_class: str
    reason: str
    verb: str = ""
    sapient_target: bool = False
    flagged: bool = False


def risk_rank(risk_class: Any) -> int:
    """0 = ``reversible`` … 3 = ``destructive``; classe inválida ⇒ a mais severa."""
    return _RANK.get(str(risk_class), len(RISK_CLASSES) - 1)


def _coerce_class(value: Any, policy: RiskPolicy) -> str:
    return value if isinstance(value, str) and value in _RANK else _safe_class(policy)


def _safe_class(policy: RiskPolicy) -> str:
    return policy.unknown_class if policy.unknown_class in _RANK else RISK_IRREVERSIBLE


def policy_floor(risk_class: Any, policy: RiskPolicy = DEFAULT_POLICY) -> float:
    """Piso de ``auto`` da classe (classe inválida ⇒ piso da classe conservadora)."""
    policy = _policy(policy)
    return policy.floor(_coerce_class(risk_class, policy))


def target_risk(risk_class: Any, policy: RiskPolicy = DEFAULT_POLICY) -> float:
    """Risco-alvo da classe = ``1 - piso`` (0.10 / 0.05 / 0.05 / 0.03).

    É a ponte entre a política ("confiança >= 0.95") e os dados: o limiar
    calibrado de uma classe é o menor ``t`` cujo limite superior de erro nas
    amostras ``confidence >= t`` não passa deste valor.
    """
    policy = _policy(policy)
    return round(1.0 - policy_floor(risk_class, policy), 6)


def auto_allowed(risk_class: Any, policy: RiskPolicy = DEFAULT_POLICY) -> bool:
    """A classe pode chegar a ``auto``? (``destructive`` só com opt-in explícito.)"""
    policy = _policy(policy)
    return _coerce_class(risk_class, policy) != RISK_DESTRUCTIVE or bool(policy.destructive_auto)


# --------------------------------------------------------------- classificação


def _norm(value: Any) -> str:
    return " ".join(str(value).strip().lower().replace("-", "_").split()) if isinstance(value, str) else ""


def _get(action: Any, name: str, default: Any = None) -> Any:
    if isinstance(action, Mapping):
        return action.get(name, default)
    return getattr(action, name, default)


def _verb_of(action: Any) -> str:
    """Verbo semântico: ``verb`` explícito, senão o prefixo do id (``attack:x``)."""
    if isinstance(action, str):
        text = _norm(action)
        return text.split(":", 1)[0].strip() if text else ""
    verb = _norm(_get(action, "verb", ""))
    if verb:
        return verb
    ident = _norm(_get(action, "id", "") or _get(action, "action_id", ""))
    return ident.split(":", 1)[0].strip() if ident else ""


def _is_sapient_text(text: str) -> bool:
    return bool(text) and any(word in text for word in SAPIENT_NAME_WORDS)


def _target_texts(action: Any) -> str:
    """Nome/raça/alvo do ataque, em minúsculas, para a deteção de sapiente."""
    parts: list[str] = []
    if isinstance(action, str):
        _, _, tail = action.partition(":")
        parts.append(tail)
    else:
        params = _get(action, "params", None)
        if not isinstance(params, Mapping):
            params = {}
        for source in (action, params):
            for key in ("target", "target_name", "name", "race"):
                value = _get(source, key)
                if isinstance(value, str):
                    parts.append(value)
        ident = _get(action, "id", "")
        if isinstance(ident, str) and ":" in ident:
            parts.append(ident.split(":", 1)[1])
    return _norm(" ".join(parts)).replace("_", " ")


def _flag(action: Any, key: str) -> Any:
    value = _get(action, key)
    params = _get(action, "params", None)
    if value is None and isinstance(params, Mapping):
        value = params.get(key)
    return value


def classify_action(action: Any, *, policy: RiskPolicy = DEFAULT_POLICY) -> RiskVerdict:
    """Classe de risco de uma macro-ação (``str``, mapping ou ``MacroAction``).

    Nunca levanta: entrada vazia/estranha dá a classe conservadora do verbo
    desconhecido. Uma anotação (``destructive``/``risk_class`` em ``params`` ou no
    objeto) só pode **subir** a classe calculada.
    """
    policy = _policy(policy)
    try:
        verb = _verb_of(action)
        if not verb:
            return RiskVerdict(_safe_class(policy), "empty or unreadable action", "")
        base = VERB_CLASSES.get(verb)
        reason = f"verb '{verb}'"
        sapient = False
        if base is None:
            base = _safe_class(policy)
            reason = f"unknown verb '{verb}' (conservative default)"
        elif verb == "attack":
            sapient = _is_sapient_text(_target_texts(action)) or _norm(_flag(action, "kind")) == "sapient"
            if sapient:
                # só um agressor explícito (``category == "threat"`` ou ``hostile``)
                # pode beneficiar da constante da política; um sapiente não
                # hostil — ou um id sem contexto — é sempre ``destructive``.
                aggressor = _truthy(_flag(action, "hostile")) or _norm(_get(action, "category", "")) == "threat"
                base = _coerce_class(policy.hostile_sapient_class, policy) if aggressor else RISK_DESTRUCTIVE
                reason = "attack on a hostile sapient target" if aggressor else "attack on a sapient target"
            else:
                reason = "attack on a non-sapient target"
        flagged = False
        level = base
        if _truthy(_flag(action, "destructive")):
            level, flagged, reason = RISK_DESTRUCTIVE, True, "flagged destructive"
        annotated = _norm(_flag(action, "risk_class") or "")
        if annotated in _RANK and _RANK[annotated] > _RANK[level]:
            level, flagged, reason = annotated, True, f"annotated {annotated}"
        return RiskVerdict(level, reason, verb, sapient, flagged)
    except Exception:  # pragma: no cover - defesa: o loop nunca morre por aqui
        log.warning("risk classification failed for %r", action, exc_info=True)
        return RiskVerdict(_safe_class(policy), "classification error (conservative default)")


def risk_class(action: Any, *, policy: RiskPolicy = DEFAULT_POLICY) -> str:
    """Só a classe (ver :func:`classify_action` para o porquê)."""
    return classify_action(action, policy=policy).risk_class


# ------------------------------------------------------------------ limiares


def _clean_unit(value: Any, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isnan(float(value)):
        return default
    return min(1.0, max(0.0, float(value)))


def _entry_values(entry: Any) -> tuple[float | None, bool, int]:
    """``(threshold, verified, support)`` de uma entrada de calibração (tolerante)."""
    if entry is None:
        return None, False, 0
    if isinstance(entry, bool):
        return None, False, 0
    if isinstance(entry, (int, float)):
        return float(entry), False, 0
    if isinstance(entry, Mapping):
        threshold, verified, support = entry.get("threshold"), entry.get("verified"), entry.get("support")
    else:
        threshold = getattr(entry, "threshold", None)
        verified = getattr(entry, "verified", False)
        support = getattr(entry, "support", 0)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or math.isnan(float(threshold)):
        threshold = None
    count = support if isinstance(support, int) and not isinstance(support, bool) else 0
    return (float(threshold) if threshold is not None else None), verified is True, max(0, count)


def _calibrated_entry(calibration: Any, risk_class_name: str) -> Any:
    if calibration is None:
        return None
    getter = getattr(calibration, "entry_for", None)
    if callable(getter):
        try:
            return getter(risk_class_name)
        except Exception:  # pragma: no cover - tabela estranha ⇒ piso da política
            return None
    if isinstance(calibration, Mapping):
        classes = calibration.get("classes")
        source = classes if isinstance(classes, Mapping) else calibration
        return source.get(risk_class_name)
    return None


def thresholds_for(
    risk_class_name: Any,
    calibration: Any = None,
    *,
    base: float = DEFAULT_AUTO_THRESHOLD,
    policy: RiskPolicy = DEFAULT_POLICY,
) -> float:
    """Limiar de ``auto`` da classe: ``max(base, piso, calibrado)``.

    ``base`` é o limiar global configurado (``LoopConfig.auto_threshold``); o
    resultado nunca fica abaixo dele nem do piso da classe. A única descida
    possível é a de uma entrada ``verified`` com suporte >=
    ``policy.min_support_to_lower`` (nunca em ``destructive``, nunca abaixo de
    ``policy.lowest_auto_threshold``). Devolve sempre um ``float`` em ``[0, 1]``.
    """
    policy = _policy(policy)
    klass = _coerce_class(risk_class_name, policy)
    floor = max(_clean_unit(base, DEFAULT_AUTO_THRESHOLD), policy.floor(klass))
    threshold, verified, support = _entry_values(_calibrated_entry(calibration, klass))
    if threshold is None:
        return floor
    threshold = min(1.0, max(0.0, threshold))
    if threshold >= floor:
        return threshold
    if klass == RISK_DESTRUCTIVE or not verified or support < policy.min_support_to_lower:
        return floor
    return min(floor, max(threshold, _clean_unit(policy.lowest_auto_threshold, LOWEST_AUTO_THRESHOLD)))


def class_thresholds(
    calibration: Any = None,
    *,
    base: float = DEFAULT_AUTO_THRESHOLD,
    policy: RiskPolicy = DEFAULT_POLICY,
) -> dict[str, float]:
    """Limiar efetivo das quatro classes (útil para UI/CLI: o que o loop vai usar)."""
    return {
        klass: thresholds_for(klass, calibration, base=base, policy=policy)
        for klass in RISK_CLASSES
    }


def auto_threshold_for_action(
    action: Any,
    base: float = DEFAULT_AUTO_THRESHOLD,
    calibration: Any = None,
    *,
    policy: RiskPolicy = DEFAULT_POLICY,
) -> float:
    """Limiar de ``auto`` da classe da ação (ver :func:`thresholds_for`)."""
    return thresholds_for(risk_class(action, policy=policy), calibration, base=base, policy=policy)


# ---------------------------------------------------------- injeção e bandas


def has_injection_signal(value: Any, *, threshold: float = INJECTION_NOUL_THRESHOLD) -> bool:
    """O sinal ``has_injection`` está ligado? (``bool``, ``p`` do ``noul`` ou ``Answer``.)

    ``None``/ilegível ⇒ ``False``: sem evidência não se inventa injeção (a banda
    já cai em ``hitl`` quando a chamada falha). Um ``p`` >= ``threshold`` liga o
    sinal — assimetria deliberada, porque o sinal só **reduz** autonomia.
    """
    if isinstance(value, bool):
        return value
    inner = value
    if isinstance(inner, Mapping):
        inner = inner.get("noul", inner.get("value"))
    elif not isinstance(inner, (int, float, str)) and inner is not None:
        inner = getattr(inner, "value", None)  # ``Answer`` do cliente
    if isinstance(inner, bool):
        return inner
    if isinstance(inner, (int, float)):
        return not math.isnan(float(inner)) and float(inner) >= float(threshold)
    return isinstance(inner, str) and inner.strip().lower() in _TRUE_TEXT


def band_ceiling(
    action: Any = None,
    *,
    has_injection: bool = False,
    policy: RiskPolicy = DEFAULT_POLICY,
) -> str:
    """Teto de banda: ``hitl`` com injeção ou classe sem ``auto``, senão ``auto``."""
    policy = _policy(policy)
    if has_injection or not auto_allowed(risk_class(action, policy=policy), policy):
        return BAND_HITL
    return BAND_AUTO


def band_for_action(
    confidence: Any,
    action: Any = None,
    *,
    kind: str = "choice",
    auto: float = DEFAULT_AUTO_THRESHOLD,
    hitl: float = DEFAULT_HITL_THRESHOLD,
    calibration: Any = None,
    has_injection: bool = False,
    policy: RiskPolicy = DEFAULT_POLICY,
) -> str:
    """Banda final de uma resposta, com o limiar da **classe** da ação.

    É o substituto de ``band_for_choice_or_score(confidence, config.auto,
    config.hitl)`` quando se conhece a ação: usa o limiar da classe, aplica o
    teto de injeção/classe e cai em ``hitl`` (nunca levanta) se algo for inválido.
    ``kind="noul"`` lê ``confidence`` como ``p`` e usa ``|2p - 1|``.
    """
    policy = _policy(policy)
    try:
        klass = risk_class(action, policy=policy)
        threshold = thresholds_for(klass, calibration, base=auto, policy=policy)
        hitl_threshold = min(_clean_unit(hitl, DEFAULT_HITL_THRESHOLD), threshold)
        if str(kind).lower() == "noul":
            band = band_for_noul(confidence, auto=threshold, hitl=hitl_threshold)
        else:
            band = band_for_choice_or_score(confidence, auto=threshold, hitl=hitl_threshold)
        ceiling = band_ceiling(action, has_injection=bool(has_injection), policy=policy)
        return cap_band(band, ceiling)
    except Exception:
        log.warning("could not band %r for %r; falling back to hitl", confidence, action, exc_info=True)
        return BAND_HITL
