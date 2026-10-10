"""Calibração das bandas do Jev: métricas, limiares por risco e tabela por *snapshot*.

*Porquê.* Os limiares ``auto``/``hitl`` só são seguros se a confiança que o
modelo reporta corresponder à frequência real de acerto — e isso vale para **um
snapshot** do modelo, não para o seguinte (ARQUITETURA §9: "limiares calibrados
por snapshot — trocar de snapshot exige recalibrar"). Este módulo é a parte
**pura** (sem rede, sem relógio escondido, sem estado global):

* métricas — :func:`ece`, :func:`reliability_bins`, :func:`brier`,
  :func:`risk_coverage_curve` (confiança decrescente × erro acumulado);
* :func:`threshold_for_target_risk` — o **menor** limiar cujo limite superior de
  risco (Wilson por omissão) cabe no alvo; com suporte insuficiente devolve
  ``None`` ("manter o default conservador");
* :func:`build_report` — por classe de risco, por papel e por pergunta;
* :class:`CalibrationTable` — JSON **chaveado pelo snapshot** do modelo (data do
  relatório, contagens, impressão digital do eval); :func:`load_calibration`
  devolve os defaults conservadores quando o modelo configurado não tem entrada
  (é isto que impõe "snapshot novo ⇒ recalibrar");
* :func:`samples_from_decisions` — rótulos (confiança, acertou?) do log real.

Escala: ``Sample.confidence`` é a **probabilidade de acerto**, como no
``jev.mjs eval`` (docs/calibracao.md §4): ``choice``/``score`` usam a
``confidence`` reportada (ausente ⇒ 0.5, a "moeda ao ar") e ``noul`` usa
``max(p, 1-p)``. Os limiares por classe aplicam-se à pergunta ``action``
(``choice``), onde esta escala e a do gate coincidem.

Proxy dos rótulos reais (:func:`label_decisions`): *aprovada* ou *executada sem
aborto* contam como acerto; *rejeitada* ou *abortada* como erro. É enviesado:
só o que passou o limiar executa (censura), humanos aprovam o plausível (viés de
automação), uma rejeição pode ser de timing e um aborto de *guard* é muitas
vezes o ambiente. Por isso complementa os cenários rotulados de ``evals/`` — e
o limiar derivado fica sujeito à regra "só sobe, a não ser que verificado".
"""
from __future__ import annotations

import bisect
import hashlib
import json
import logging
import math
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .risk import POLICY_FLOORS, RISK_CLASSES, risk_class, target_risk

log = logging.getLogger("system_one_dwarf.jev.calibration")

__all__ = [
    "DEFAULT_BINS", "DEFAULT_MIN_SUPPORT", "DEFAULT_Z", "DEFAULT_DELTA", "DEFAULT_GRID",
    "DEFAULT_MIN_THRESHOLD", "DEFAULT_BOUND", "BOUNDS", "ECE_GOOD", "ECE_ACCEPTABLE",
    "SCORE_TOLERANCE", "UNAVAILABLE_CONFIDENCE", "PAIRING_SLACK_S", "MAX_TABLE_BYTES",
    "TABLE_FORMAT",
    "SOURCE_DEFAULTS", "SOURCE_FAKE",
    "Sample", "ReliabilityBin", "CurvePoint", "ThresholdChoice", "ClassCalibration",
    "ModelCalibration", "CalibrationTable", "CalibrationLookup", "LabelingResult",
    "coerce_samples", "score_answer", "reliability_bins", "ece", "ece_label", "brier",
    "accuracy", "mean_confidence", "wilson_upper", "hoeffding_upper", "risk_upper_bound",
    "risk_coverage_curve", "aurc", "select_threshold", "threshold_for_target_risk",
    "group_metrics", "build_report", "fingerprint_cases", "load_calibration",
    "label_decisions", "samples_from_decisions",
]

# ----------------------------------------------------------------- constantes
DEFAULT_BINS = 10            #: contentores do ECE (os 10 do ``jev.mjs eval``)
DEFAULT_MIN_SUPPORT = 30     #: n mínimo por classe/papel para QUALQUER mudança por dados
DEFAULT_Z = 1.96             #: z de Wilson (95 % bilateral; conservador)
DEFAULT_DELTA = 0.05         #: δ de Hoeffding
DEFAULT_GRID = 0.01          #: grelha dos limiares candidatos (``None`` = valores exatos)
DEFAULT_MIN_THRESHOLD = 0.50  #: nenhum ``auto`` abaixo do piso da banda ``hitl``
DEFAULT_BOUND = "wilson"
BOUNDS: tuple[str, ...] = ("wilson", "hoeffding", "point")
ECE_GOOD = 0.05              #: ECE ≤ isto é "bom" (docs/calibracao.md §4)
ECE_ACCEPTABLE = 0.10        #: ECE ≤ isto é "aceitável"; acima é "mau"
SCORE_TOLERANCE = 0.5        #: tolerância do ``score`` (nível exato numa régua de 3)
UNAVAILABLE_CONFIDENCE = 0.5  #: confiança de uma resposta sem ``confidence``
PAIRING_SLACK_S = 5.0        #: folga (s) no emparelhamento decisão ↔ aprovação
MAX_TABLE_BYTES = 5_000_000  #: maior tabela de calibração aceite (memória limitada)
TABLE_FORMAT = 1
SOURCE_DEFAULTS = "defaults"
SOURCE_FAKE = "fake-model"
_EPS = 1e-9
_TEXT_FIELDS = ("role", "qtype", "question", "risk_class", "case", "source")


# --------------------------------------------------------------------- amostras


@dataclass(frozen=True)
class Sample:
    """Uma resposta rotulada: confiança (prob. de acerto), acertou?, e contexto."""

    confidence: float
    correct: bool
    role: str | None = None
    qtype: str | None = None
    question: str | None = None
    risk_class: str | None = None
    case: str | None = None
    source: str | None = None
    #: ``False`` quando a confiança foi o *fallback* 0.5 (não reportada).
    available: bool = True

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"confidence": round(self.confidence, 6), "correct": self.correct}
        out.update({k: getattr(self, k) for k in _TEXT_FIELDS if getattr(self, k) is not None})
        if not self.available:
            out["available"] = False
        return out

    @classmethod
    def from_any(cls, item: Any) -> "Sample | None":
        """``Sample``, ``(confiança, acertou[, ...])`` ou mapping; inválido ⇒ ``None``."""
        if isinstance(item, Sample):
            return item
        extra: Mapping[str, Any] = {}
        if isinstance(item, Mapping):
            confidence = item.get("confidence", item.get("conf"))
            correct, extra = item.get("correct", item.get("is_correct")), item
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            confidence, correct = item[0], item[1]
        else:
            return None
        value = _number(confidence)
        if value is None or not 0.0 <= value <= 1.0:
            return None
        if isinstance(correct, bool):
            label = correct
        elif isinstance(correct, int) and correct in (0, 1):
            label = bool(correct)
        else:
            return None
        text = {k: (str(extra[k]) if extra.get(k) is not None else None) for k in _TEXT_FIELDS}
        return cls(value, label, available=extra.get("available", True) is not False, **text)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isnan(float(value)):
        return None
    return float(value)


def coerce_samples(items: Iterable[Any] | None) -> list[Sample]:
    """Converte e **descarta** o inválido (NaN, fora de [0,1], rótulo estranho)."""
    return [s for s in (Sample.from_any(item) for item in items or ()) if s is not None]


def score_answer(
    qtype: Any, expected: Any, value: Any, confidence: Any = None, *,
    tolerance: float = SCORE_TOLERANCE,
) -> tuple[bool, float, bool] | None:
    """Corrige uma resposta como o ``jev.mjs eval`` → ``(acertou, confiança, disponível)``.

    ``noul``: previsto ``p >= 0.5``, confiança ``max(p, 1-p)``; ``choice``:
    igualdade da chave; ``score``: ``|previsto - esperado| <= tolerância``.
    ``confidence`` ausente/0/inválida ⇒ 0.5 com ``disponível=False``. Sem valor
    utilizável ⇒ ``None`` (não conta, nem como erro).
    """
    kind = str(qtype)
    if kind == "noul":
        p = _number(value)
        if p is None or not 0.0 <= p <= 1.0 or not isinstance(expected, bool):
            return None
        return (p >= 0.5) == expected, max(p, 1.0 - p), True
    reported = _number(confidence)
    available = reported is not None and 0.0 < reported <= 1.0
    conf = reported if available else UNAVAILABLE_CONFIDENCE
    if kind == "choice":
        if not isinstance(value, str) or not isinstance(expected, str):
            return None
        return value == expected, float(conf), available
    if kind == "score":
        got, want = _number(value), _number(expected)
        if got is None or want is None:
            return None
        return abs(got - want) <= float(tolerance), float(conf), available
    return None


# -------------------------------------------------------------------- métricas


@dataclass(frozen=True)
class ReliabilityBin:
    """Um contentor do diagrama de fiabilidade."""

    lower: float
    upper: float
    count: int
    mean_confidence: float
    accuracy: float

    @property
    def gap(self) -> float:
        return abs(self.accuracy - self.mean_confidence)

    def to_dict(self) -> dict[str, Any]:
        return {"bin": f"{self.lower:.1f}-{self.upper:.1f}", "count": self.count,
                "mean_confidence": round(self.mean_confidence, 4),
                "accuracy": round(self.accuracy, 4), "gap": round(self.gap, 4)}


def reliability_bins(samples: Iterable[Any], bins: int = DEFAULT_BINS) -> list[ReliabilityBin]:
    """Contentores ``[0, 0.1) … [0.9, 1.0]`` (1.0 cai no último, como no ``jev.mjs``)."""
    count = max(1, int(bins))
    grouped: list[list[Sample]] = [[] for _ in range(count)]
    for sample in coerce_samples(samples):
        grouped[min(count - 1, int(sample.confidence * count))].append(sample)
    return [
        ReliabilityBin(
            lower=index / count, upper=(index + 1) / count, count=len(rows),
            mean_confidence=sum(s.confidence for s in rows) / len(rows),
            accuracy=sum(1 for s in rows if s.correct) / len(rows),
        )
        for index, rows in enumerate(grouped) if rows
    ]


def ece(samples: Iterable[Any], bins: int = DEFAULT_BINS) -> float | None:
    """*Expected Calibration Error*: ``Σ (n_b/n)·|acc_b − conf_b|`` (``None`` sem amostras)."""
    items = coerce_samples(samples)
    if not items:
        return None
    return sum(b.count * b.gap for b in reliability_bins(items, bins)) / len(items)


def ece_label(value: float | None) -> str | None:
    """``good`` ≤ 0.05 < ``acceptable`` ≤ 0.10 < ``bad`` (docs/calibracao.md §4)."""
    if value is None:
        return None
    if value <= ECE_GOOD + _EPS:
        return "good"
    return "acceptable" if value <= ECE_ACCEPTABLE + _EPS else "bad"


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def brier(samples: Iterable[Any]) -> float | None:
    """Brier binário: média de ``(confiança − acertou)²`` (``None`` sem amostras)."""
    return _mean([(s.confidence - (1.0 if s.correct else 0.0)) ** 2 for s in coerce_samples(samples)])


def accuracy(samples: Iterable[Any]) -> float | None:
    return _mean([1.0 if s.correct else 0.0 for s in coerce_samples(samples)])


def mean_confidence(samples: Iterable[Any]) -> float | None:
    return _mean([s.confidence for s in coerce_samples(samples)])


# ------------------------------------------------------- limites e curva risco


def wilson_upper(errors: int, n: int, z: float = DEFAULT_Z) -> float:
    """Limite superior de Wilson da taxa de erro (``n = 0`` ⇒ 1.0: nada se prova)."""
    if n <= 0:
        return 1.0
    p = min(1.0, max(0.0, errors / n))
    z2 = z * z
    spread = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))
    return min(1.0, (p + z2 / (2.0 * n) + spread) / (1.0 + z2 / n))


def hoeffding_upper(errors: int, n: int, delta: float = DEFAULT_DELTA) -> float:
    """Limite de Hoeffding: ``p + sqrt(ln(1/δ) / 2n)`` (mais folgado que Wilson)."""
    if n <= 0:
        return 1.0
    return min(1.0, errors / n + math.sqrt(math.log(1.0 / delta) / (2.0 * n)))


def risk_upper_bound(
    errors: int, n: int, bound: str = DEFAULT_BOUND, *, z: float = DEFAULT_Z,
    delta: float = DEFAULT_DELTA,
) -> float:
    """Limite superior do risco; ``point`` é a taxa observada (não conservador)."""
    if bound == "hoeffding":
        return hoeffding_upper(errors, n, delta)
    if bound == "point":
        return errors / n if n > 0 else 1.0
    return wilson_upper(errors, n, z)


@dataclass(frozen=True)
class CurvePoint:
    """Aceitar tudo com ``confidence >= threshold``: cobertura × risco."""

    threshold: float
    accepted: int
    errors: int
    coverage: float
    risk: float | None
    upper_bound: float | None

    def to_dict(self) -> dict[str, Any]:
        return {"threshold": round(self.threshold, 4), "accepted": self.accepted,
                "errors": self.errors, "coverage": round(self.coverage, 4),
                "risk": None if self.risk is None else round(self.risk, 4),
                "upper_bound": None if self.upper_bound is None else round(self.upper_bound, 4)}


def risk_coverage_curve(
    samples: Iterable[Any], *, bound: str = DEFAULT_BOUND, z: float = DEFAULT_Z,
) -> list[CurvePoint]:
    """Curva risco-cobertura: um ponto por confiança distinta, da maior para a menor.

    Empates formam **um** ponto (não se aceita metade de um empate); a cobertura
    é crescente ao longo da lista e o último ponto cobre tudo.
    """
    items = sorted(coerce_samples(samples), key=lambda s: -s.confidence)
    points: list[CurvePoint] = []
    accepted = errors = index = 0
    while index < len(items):
        value = items[index].confidence
        while index < len(items) and items[index].confidence == value:
            accepted += 1
            errors += 0 if items[index].correct else 1
            index += 1
        points.append(CurvePoint(value, accepted, errors, accepted / len(items), errors / accepted,
                                 risk_upper_bound(errors, accepted, bound, z=z)))
    return points


def aurc(curve: Sequence[CurvePoint]) -> float | None:
    """Área sob a curva risco-cobertura (trapézios; menor é melhor)."""
    if not curve:
        return None
    area, cover, previous = 0.0, 0.0, curve[0].risk or 0.0
    for point in curve:
        risk = point.risk or 0.0
        area += (point.coverage - cover) * (previous + risk) / 2.0
        cover, previous = point.coverage, risk
    return area


@dataclass(frozen=True)
class ThresholdChoice:
    """Resultado de :func:`select_threshold` (``threshold=None`` ⇒ manter o default)."""

    threshold: float | None
    reason: str          # ok | no_samples | insufficient_support | unattainable | invalid_target
    n: int
    accepted: int = 0
    errors: int = 0
    coverage: float | None = None
    risk: float | None = None
    upper_bound: float | None = None
    max_risk: float | None = None
    bound: str = DEFAULT_BOUND
    min_support: int = DEFAULT_MIN_SUPPORT

    def to_dict(self) -> dict[str, Any]:
        out = {k: getattr(self, k) for k in (
            "threshold", "reason", "n", "accepted", "errors", "max_risk", "bound", "min_support")}
        out.update({k: None if getattr(self, k) is None else round(getattr(self, k), 4)
                    for k in ("coverage", "risk", "upper_bound")})
        return out


def _candidates(confidences: Sequence[float], grid: float | None, minimum: float) -> list[float]:
    if grid and grid > 0:
        first, last = int(math.ceil(minimum / grid - _EPS)), int(math.floor(1.0 / grid + _EPS))
        return [round(step * grid, 10) for step in range(first, last + 1)]
    return sorted({value for value in confidences if value >= minimum - _EPS})


def select_threshold(
    samples: Iterable[Any], max_risk: float, *, min_support: int = DEFAULT_MIN_SUPPORT,
    bound: str = DEFAULT_BOUND, z: float = DEFAULT_Z, delta: float = DEFAULT_DELTA,
    grid: float | None = DEFAULT_GRID, min_threshold: float = DEFAULT_MIN_THRESHOLD,
) -> ThresholdChoice:
    """Menor limiar ``t`` com ``accepted(t) >= min_support`` e limite de risco ``<= max_risk``.

    ``accepted(t)`` = amostras com ``confidence >= t``; os candidatos são os
    pontos da grelha (``grid``) ou as confianças distintas (``grid=None``). Sem
    amostras suficientes — ou sem candidato que cumpra — não há limiar: o
    chamador mantém o piso conservador da política.
    """
    items = sorted(coerce_samples(samples), key=lambda s: s.confidence)
    n = len(items)
    base = {"n": n, "max_risk": max_risk, "bound": bound, "min_support": int(min_support)}
    if _number(max_risk) is None:
        return ThresholdChoice(None, "invalid_target", **{**base, "max_risk": None})
    if n == 0:
        return ThresholdChoice(None, "no_samples", **base)
    if n < int(min_support):
        return ThresholdChoice(None, "insufficient_support", **base)
    confidences = [s.confidence for s in items]
    suffix_errors = [0] * (n + 1)
    for index in range(n - 1, -1, -1):
        suffix_errors[index] = suffix_errors[index + 1] + (0 if items[index].correct else 1)
    evaluated = False
    for threshold in _candidates(confidences, grid, float(min_threshold)):
        start = bisect.bisect_left(confidences, threshold - _EPS)
        accepted, errors = n - start, suffix_errors[start]
        if accepted < int(min_support):
            break  # o conjunto aceite só encolhe com o limiar
        evaluated = True
        upper = risk_upper_bound(errors, accepted, bound, z=z, delta=delta)
        if upper <= float(max_risk) + _EPS:
            return ThresholdChoice(round(threshold, 6), "ok", n, accepted, errors, accepted / n,
                                   errors / accepted, upper, max_risk, bound, int(min_support))
    return ThresholdChoice(None, "unattainable" if evaluated else "insufficient_support", **base)


def threshold_for_target_risk(
    samples: Iterable[Any], max_risk: float, min_support: int = DEFAULT_MIN_SUPPORT,
    bound: str = DEFAULT_BOUND, **kwargs: Any,
) -> float | None:
    """Só o limiar de :func:`select_threshold` (``None`` = manter o default)."""
    return select_threshold(samples, max_risk, min_support=min_support, bound=bound, **kwargs).threshold


# ------------------------------------------------------------------ relatórios


def group_metrics(samples: Iterable[Any], bins: int = DEFAULT_BINS) -> dict[str, Any]:
    """``n``/accuracy/confiança média/ECE/Brier de um grupo (JSON-friendly)."""
    items = coerce_samples(samples)
    value = ece(items, bins)

    def rounded(number: float | None) -> float | None:
        return None if number is None else round(number, 4)

    return {
        "n": len(items), "accuracy": rounded(accuracy(items)),
        "mean_confidence": rounded(mean_confidence(items)), "ece": rounded(value),
        "ece_label": ece_label(value), "brier": rounded(brier(items)),
        "unavailable_confidence": sum(1 for s in items if not s.available),
    }


def _group(samples: Sequence[Sample], key: Any) -> dict[str, list[Sample]]:
    out: dict[str, list[Sample]] = {}
    for sample in samples:
        name = key(sample)
        if name:
            out.setdefault(name, []).append(sample)
    return out


def build_report(
    samples: Iterable[Any], *, model: str | None = None, resolved_model: str | None = None,
    source: str = "eval", eval_fingerprint: str | None = None, report_date: str | None = None,
    min_support: int = DEFAULT_MIN_SUPPORT, bound: str = DEFAULT_BOUND, z: float = DEFAULT_Z,
    grid: float | None = DEFAULT_GRID, bins: int = DEFAULT_BINS,
    targets: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Relatório completo (geral, por papel, por pergunta e **por classe de risco**).

    ``by_class`` só usa amostras com ``risk_class`` (a pergunta ``action``,
    classificada pela ação **escolhida**: o limiar aplica-se ao que se executa).
    Cada classe traz a escolha de limiar para o seu risco-alvo (``1 − piso``,
    sobreponível por ``targets``) e a curva risco-cobertura.
    """
    items = coerce_samples(samples)
    members_of = _group(items, lambda s: s.risk_class if s.risk_class in RISK_CLASSES else None)
    by_class: dict[str, Any] = {}
    for klass in RISK_CLASSES:
        members = members_of.get(klass, [])
        goal = (targets or {}).get(klass, target_risk(klass))
        choice = select_threshold(members, goal, min_support=min_support, bound=bound, z=z, grid=grid)
        curve = risk_coverage_curve(members, bound=bound, z=z)
        area = aurc(curve)
        by_class[klass] = {
            **group_metrics(members, bins), "policy_floor": POLICY_FLOORS[klass], "target_risk": goal,
            "selection": choice.to_dict(), "aurc": None if area is None else round(area, 4),
            "curve": [point.to_dict() for point in curve],
        }
    questions = _group(items, lambda s: f"{s.role}/{s.question}" if s.role and s.question else None)
    return {
        "format": TABLE_FORMAT, "model": model, "resolved_model": resolved_model, "source": source,
        "report_date": report_date, "eval_fingerprint": eval_fingerprint,
        "settings": {"bins": bins, "min_support": int(min_support), "bound": bound, "z": z, "grid": grid},
        "overall": group_metrics(items, bins),
        "by_role": {k: group_metrics(v, bins) for k, v in sorted(_group(items, lambda s: s.role).items())},
        "by_question": {k: group_metrics(v, bins) for k, v in sorted(questions.items())},
        "by_class": by_class,
        "reliability": [b.to_dict() for b in reliability_bins(items, bins)],
    }


def fingerprint_cases(cases: Iterable[Mapping[str, Any]]) -> str:
    """Impressão digital estável do conjunto de eval (``sha256:<hex>``).

    Só entra o que define a medição — ``name``, ``state``, ``questions`` e
    ``expected`` —, ordenado por ``name``; ``notes``/``bands`` podem mudar sem
    invalidar a calibração.
    """
    rows = sorted(({k: case.get(k) for k in ("name", "state", "questions", "expected")}
                   for case in cases if isinstance(case, Mapping)), key=lambda r: str(r["name"]))
    blob = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------- tabela


def _unit(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and 0.0 <= number <= 1.0 else None


@dataclass(frozen=True)
class ClassCalibration:
    """Entrada de uma classe: o limiar *recomendado* pelos dados + a prova.

    ``threshold`` pode ficar **abaixo** do piso da política; quem decide é
    :func:`system_one_dwarf.jev.risk.thresholds_for` (só baixa com ``verified``
    e suporte). ``verified`` é um ato humano explícito.
    """

    risk_class: str
    threshold: float | None
    support: int
    verified: bool = False
    max_risk: float | None = None
    observed_risk: float | None = None
    upper_bound: float | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in (
            "threshold", "support", "verified", "max_risk", "observed_risk", "upper_bound", "reason")}

    @classmethod
    def from_dict(cls, klass: str, data: Any) -> "ClassCalibration":
        d = data if isinstance(data, Mapping) else {}
        support = d.get("support")
        return cls(
            klass, _unit(d.get("threshold")),
            support if isinstance(support, int) and not isinstance(support, bool) and support > 0 else 0,
            d.get("verified") is True, _unit(d.get("max_risk")), _unit(d.get("observed_risk")),
            _unit(d.get("upper_bound")), str(d.get("reason") or ""),
        )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class ModelCalibration:
    """Calibração de **um** snapshot. Sem ``classes`` = defaults conservadores.

    ``meta`` guarda o que descreve a medição (``report_date``, ``resolved_model``,
    ``source``, ``eval_fingerprint``, ``samples``, ``metrics``, ``roles``,
    ``settings``) tal como vai para o JSON.
    """

    model: str
    classes: Mapping[str, ClassCalibration] = field(default_factory=dict)
    meta: Mapping[str, Any] = field(default_factory=dict)

    @property
    def report_date(self) -> str | None:
        return self.meta.get("report_date")

    @property
    def resolved_model(self) -> str | None:
        return self.meta.get("resolved_model")

    @property
    def source(self) -> str:
        return str(self.meta.get("source") or SOURCE_DEFAULTS)

    @property
    def eval_fingerprint(self) -> str | None:
        return self.meta.get("eval_fingerprint")

    @property
    def is_default(self) -> bool:
        return self.source == SOURCE_DEFAULTS or not self.classes

    def entry_for(self, risk_class_name: str) -> ClassCalibration | None:
        return self.classes.get(risk_class_name)

    @classmethod
    def conservative(cls, model: str) -> "ModelCalibration":
        return cls(model=str(model))

    @classmethod
    def from_report(cls, report: Mapping[str, Any], *, verified: Iterable[str] = ()) -> "ModelCalibration":
        """Entrada da tabela a partir de :func:`build_report`.

        ``verified`` lista as classes que o humano marcou (``--mark-verified``);
        só vale com suporte >= ``MIN_SUPPORT_TO_LOWER`` (imposto por ``risk``).
        """
        marked = set(verified)
        classes: dict[str, ClassCalibration] = {}
        for klass, row in (report.get("by_class") or {}).items():
            if klass in RISK_CLASSES and isinstance(row, Mapping):
                choice = row.get("selection") or {}
                classes[klass] = ClassCalibration(
                    klass, _unit(choice.get("threshold")), int(row.get("n") or 0), klass in marked,
                    _unit(row.get("target_risk")), _unit(choice.get("risk")),
                    _unit(choice.get("upper_bound")), str(choice.get("reason") or ""))
        overall, roles = report.get("overall") or {}, report.get("by_role") or {}
        meta = {
            "report_date": report.get("report_date"), "resolved_model": report.get("resolved_model"),
            "source": str(report.get("source") or "eval"),
            "eval_fingerprint": report.get("eval_fingerprint"),
            "samples": {"total": overall.get("n", 0), "by_class": {k: v.support for k, v in classes.items()},
                        "by_role": {k: (v or {}).get("n", 0) for k, v in roles.items()}},
            "metrics": {k: overall.get(k) for k in ("accuracy", "mean_confidence", "ece", "ece_label", "brier")},
            "roles": {k: dict(v) for k, v in roles.items() if isinstance(v, Mapping)},
            "settings": _mapping(report.get("settings")),
        }
        return cls(str(report.get("model") or ""), classes, meta)

    def to_dict(self) -> dict[str, Any]:
        return {**{k: self.meta.get(k) for k in (
            "report_date", "resolved_model", "source", "eval_fingerprint", "samples", "metrics",
            "roles", "settings")}, "classes": {k: v.to_dict() for k, v in sorted(self.classes.items())}}

    @classmethod
    def from_dict(cls, model: str, data: Any) -> "ModelCalibration":
        d = data if isinstance(data, Mapping) else {}
        raw = d.get("classes") if isinstance(d.get("classes"), Mapping) else {}
        classes = {k: ClassCalibration.from_dict(k, v) for k, v in raw.items() if k in RISK_CLASSES}
        meta = {k: d.get(k) for k in ("report_date", "resolved_model", "source", "eval_fingerprint")}
        meta.update({k: _mapping(d.get(k)) for k in ("samples", "metrics", "roles", "settings")})
        meta["source"] = str(meta.get("source") or "eval")
        return cls(model, classes, meta)


@dataclass(frozen=True)
class CalibrationLookup:
    """Resposta de :meth:`CalibrationTable.lookup`: nunca ``None``, com o porquê."""

    calibration: ModelCalibration
    status: str  # match | missing | stale_resolved | fake_ignored | empty
    detail: str = ""

    @property
    def matched(self) -> bool:
        return self.status == "match"


@dataclass
class CalibrationTable:
    """Tabela JSON **chaveada pelo snapshot** (``typesafe/jev-1.13``)."""

    models: dict[str, ModelCalibration] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)

    def put(self, entry: ModelCalibration) -> None:
        if not entry.model:
            raise ValueError("a calibration entry needs a model id")
        self.models[entry.model] = entry

    def lookup(self, model: str, resolved_model: str | None = None, *,
               allow_fake: bool = False) -> CalibrationLookup:
        """Entrada do modelo, ou os defaults conservadores (e porquê).

        Chave **exata** (sem prefixos nem *aliases*). Se a entrada registou o
        ``resolved_model`` com que foi calibrada e a corrida observou outro, é
        ``stale_resolved``: snapshot mudou ⇒ recalibrar ⇒ defaults.
        """
        key = str(model or "").strip()
        defaults = ModelCalibration.conservative(key)
        if not self.models:
            return CalibrationLookup(defaults, "empty", "no calibration table entries")
        entry = self.models.get(key)
        if entry is None:
            return CalibrationLookup(defaults, "missing", f"no calibration for model {key!r}")
        if entry.source == SOURCE_FAKE and not allow_fake:
            return CalibrationLookup(defaults, "fake_ignored", "entry came from the fake model")
        if entry.resolved_model and resolved_model and entry.resolved_model != resolved_model:
            return CalibrationLookup(defaults, "stale_resolved",
                                     f"calibrated on {entry.resolved_model!r} but the API resolved {resolved_model!r}")
        return CalibrationLookup(entry, "match")

    def to_dict(self) -> dict[str, Any]:
        return {"format": TABLE_FORMAT, "models": {k: v.to_dict() for k, v in sorted(self.models.items())}}

    @classmethod
    def from_dict(cls, data: Any) -> "CalibrationTable":
        table = cls()
        fmt = data.get("format") if isinstance(data, Mapping) else None
        if fmt != TABLE_FORMAT:
            table.issues.append(f"unsupported calibration table format: {fmt!r}")
        elif not isinstance(data.get("models"), Mapping):
            table.issues.append("calibration table has no 'models' object")
        else:
            for key, value in data["models"].items():
                if isinstance(key, str) and key.strip():
                    table.models[key] = ModelCalibration.from_dict(key, value)
                else:
                    table.issues.append(f"ignored calibration entry with invalid model key {key!r}")
        return table

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "CalibrationTable":
        """Lê a tabela; ficheiro ausente/ilegível ⇒ tabela vazia (+ ``issues``)."""
        target = Path(path)
        try:
            if target.stat().st_size > MAX_TABLE_BYTES:
                return cls(issues=[f"{target} is larger than {MAX_TABLE_BYTES} bytes; ignored"])
            raw = target.read_text(encoding="utf-8")
        except FileNotFoundError:
            return cls()
        except (OSError, UnicodeDecodeError) as exc:
            return cls(issues=[f"cannot read {target}: {exc}"])
        try:
            return cls.from_dict(json.loads(raw))
        except ValueError as exc:
            return cls(issues=[f"{target} is not valid JSON: {exc}"])

    def save(self, path: str | os.PathLike[str]) -> Path:
        """Escrita atómica e determinística (chaves ordenadas, ``\\n`` final)."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        handle, temp = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(text)
            os.chmod(temp, 0o644)  # dados públicos (versionáveis), não segredos
            os.replace(temp, target)
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise
        return target


def load_calibration(
    path: str | os.PathLike[str] | None, model: str, resolved_model: str | None = None, *,
    allow_fake: bool = False,
) -> ModelCalibration:
    """Calibração a usar para ``model``: a da tabela, ou os defaults conservadores.

    É o ponto de entrada do loop e **não levanta**: ficheiro ausente, JSON
    partido, modelo sem entrada ou snapshot resolvido diferente ⇒ uma
    ``ModelCalibration`` sem classes ⇒ :func:`~system_one_dwarf.jev.risk.thresholds_for`
    devolve os pisos da política.
    """
    try:
        table = CalibrationTable.load(path) if path else CalibrationTable()
        result = table.lookup(model, resolved_model, allow_fake=allow_fake)
    except Exception:  # pragma: no cover - defesa: a calibração nunca derruba o loop
        log.warning("calibration lookup failed; using conservative defaults", exc_info=True)
        return ModelCalibration.conservative(model)
    if not result.matched:
        log.debug("calibration %s: %s", result.status, result.detail)
    return result.calibration


# ------------------------------------------------------- amostras reais (log)


@dataclass(frozen=True)
class LabelingResult:
    """Amostras rotuladas + contagem do que ficou de fora (e porquê)."""

    samples: list[Sample]
    skipped: dict[str, int]
    labels: dict[str, int]


def _ts_seconds(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).timestamp()


def _action_of(decision: Mapping[str, Any]) -> str | None:
    answers = decision.get("answers")
    for source, key in ((answers, "action"), (decision, "action_id"), (answers, "choice")):
        if isinstance(source, Mapping) and isinstance(source.get(key), str) and source[key].strip():
            return source[key].strip()
    return None


def label_decisions(
    decisions: Iterable[Any], approvals: Iterable[Any] = (), *, roles: Sequence[str] = ("JEV-A",),
    slack_s: float = PAIRING_SLACK_S,
) -> LabelingResult:
    """Rotula decisões reais com o desfecho (aprovação/execução). **Proxy** — ver o topo.

    Uma amostra por decisão rotulada, sem duplicar o *flood* de re-pedidos: cada
    aprovação decidida consome **uma** decisão (a libertada, marcada
    ``released_by_approval``, ou a mais recente da mesma ``action_id`` antes da
    decisão humana). Rótulos: ``approved``/``released_by_approval``/
    ``executed_ok`` ⇒ acertou; ``rejected``/``aborted`` ⇒ errou. Sem
    ``confidence`` utilizável, ``error``, retida sem desfecho ou papel fora de
    ``roles`` ⇒ não entra (``skipped``).
    """
    skipped: dict[str, int] = {}
    labels: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    pool: list[dict[str, Any]] = []
    for position, raw in enumerate(decisions or ()):
        if not isinstance(raw, Mapping):
            skip("not_an_object")
            continue
        confidence, action = _unit(raw.get("confidence")), _action_of(raw)
        if raw.get("agent", raw.get("role")) not in roles:
            skip("role_not_selected")
        elif not confidence:  # None ou 0.0: indisponível
            skip("confidence_unavailable")
        elif action is None:
            skip("action_missing")
        else:
            issues = raw.get("issues")
            pool.append({
                "position": position, "raw": raw, "confidence": confidence, "action": action,
                "ts": _ts_seconds(raw.get("ts")), "status": str(raw.get("status") or "").lower(),
                "issues": [str(i) for i in issues] if isinstance(issues, (list, tuple)) else [],
                "used": False,
            })
    pool.sort(key=lambda row: (row["ts"] if row["ts"] is not None else math.inf, row["position"]))

    def make(row: Mapping[str, Any], correct: bool, why: str) -> Sample:
        labels[why] = labels.get(why, 0) + 1
        raw = row["raw"]
        return Sample(row["confidence"], correct, role=str(raw.get("agent", raw.get("role"))),
                      qtype="choice", question="action", risk_class=risk_class(row["action"]),
                      case=str(raw["id"]) if raw.get("id") is not None else None, source="decisions")

    decided: list[tuple[float, int, Mapping[str, Any]]] = []
    for position, item in enumerate(approvals or ()):
        status = str(item.get("status") or "").lower() if isinstance(item, Mapping) else ""
        if status not in ("approved", "rejected") or not isinstance(item.get("action_id"), str):
            skip("approval_undecided" if status == "pending" else "approval_unreadable")
            continue
        when = _ts_seconds(item.get("decided_at"))
        when = when if when is not None else _ts_seconds(item.get("ts"))
        decided.append((math.inf if when is None else when, position, item))
    decided.sort(key=lambda row: (row[0], row[1]))

    by_action: dict[str, list[dict[str, Any]]] = {}
    for row in pool:  # já ordenado por ts: cada lista mantém a ordem temporal
        by_action.setdefault(row["action"], []).append(row)

    samples: list[Sample] = []
    for when, _, approval in decided:
        action, approved = str(approval["action_id"]).strip(), str(approval["status"]).lower() == "approved"
        free = [r for r in by_action.get(action, ()) if not r["used"]]
        # a execução libertada acontece DEPOIS da decisão humana (o loop só vê a
        # aprovação no passo seguinte): o limite é por baixo; as retidas, por cima
        released = [r for r in free if "released_by_approval" in r["issues"]
                    and (r["ts"] is None or when == math.inf or r["ts"] >= when - slack_s)]
        before = [r for r in free if r["ts"] is None or when == math.inf or r["ts"] <= when + slack_s]
        chosen = released[0] if approved and released else (before[-1] if before else None)
        if chosen is None:
            skip("approval_without_decision")
            continue
        chosen["used"] = True
        released_mark = approved and "released_by_approval" in chosen["issues"]
        samples.append(make(chosen, approved,
                            "released_by_approval" if released_mark else ("approved" if approved else "rejected")))

    for row in pool:
        if row["used"]:
            continue
        if "released_by_approval" in row["issues"]:
            samples.append(make(row, True, "released_by_approval"))
        elif row["status"] == "aborted" or any(i == "aborted" or i.startswith("guard:") for i in row["issues"]):
            samples.append(make(row, False, "aborted"))
        elif row["status"] == "error":
            skip("status_error")
        elif row["status"] == "executed":
            samples.append(make(row, True, "executed_ok"))
        else:
            skip("unresolved")
    return LabelingResult(samples, dict(sorted(skipped.items())), dict(sorted(labels.items())))


def samples_from_decisions(decisions: Iterable[Any], approvals: Iterable[Any] = (), **kwargs: Any) -> list[Sample]:
    """Atalho de :func:`label_decisions`: só a lista de amostras."""
    return label_decisions(decisions, approvals, **kwargs).samples
