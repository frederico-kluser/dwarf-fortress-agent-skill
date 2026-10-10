"""Avaliação online do golden set: pontuação, métricas por papel e comparação com o baseline.

Parte pura e testável com um cliente falso: ``evaluate`` corre os casos contra
``client.decide_request`` (um pedido por caso, erros isolados), pontua cada
resposta como o ``jev.mjs eval`` (:func:`system_one_dwarf.jev.calibration.score_answer`)
e agrega por papel e por pergunta; ``compare_baseline`` marca as regressões
(queda de accuracy acima da tolerância, subida de ECE) só em papéis com suporte
suficiente. A CLI (``scripts/golden.py run --online``) trata da chave, do cliente
real e do relatório.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import golden_core as core
from system_one_dwarf.jev import calibration as cal

__all__ = [
    "ACCURACY_TOLERANCE", "ECE_TOLERANCE", "MIN_GATE_SUPPORT", "MAX_ERROR_FRACTION", "SESSION_ID",
    "evaluate", "compare_baseline", "load_baseline", "update_baseline",
]

#: Quanto a accuracy de um papel pode cair face ao baseline (docs/calibracao.md §8: 3 p.p.).
ACCURACY_TOLERANCE = 0.03
#: Quanto o ECE de um papel pode subir face ao baseline.
ECE_TOLERANCE = 0.05
#: Respostas mínimas de um papel para o seu delta contar como regressão.
MIN_GATE_SUPPORT = 30
#: Fração de casos com erro de chamada acima da qual a corrida é inválida (exit 3).
MAX_ERROR_FRACTION = 0.2
SESSION_ID = "golden-regression"


def score_case(case: Mapping[str, Any], request: Mapping[str, Any], result: Any,
                tolerance: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    answers = getattr(result, "answers", {}) or {}
    for qid, label in case["expected"].items():
        body = (request.get("questions") or {}).get(qid) or {}
        answer = answers.get(qid)
        row = {"id": case["id"], "role": case["role"], "question": qid, "qtype": body.get("type"),
               "expected": label}
        scored = None
        if answer is not None:
            scored = cal.score_answer(body.get("type"), label, getattr(answer, "value", None),
                                      getattr(answer, "confidence", None), tolerance=tolerance)
        if scored is None:
            rows.append({**row, "skipped": True})
            continue
        correct, confidence, available = scored
        rows.append({**row, "predicted": getattr(answer, "value", None), "correct": correct,
                     "confidence": round(confidence, 4), "available": available,
                     "band": getattr(answer, "band", None)})
    return rows


def evaluate(cases: Sequence[Mapping[str, Any]], client: Any, *, tolerance: float = cal.SCORE_TOLERANCE,
             concurrency: int = 1) -> dict[str, Any]:
    """Corre os casos contra ``client.decide_request`` (um pedido por caso; erros isolados)."""

    def one(case: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str | None, str | None]:
        try:
            request = core.rebuild_request(case)
            result = client.decide_request(request, session_id=SESSION_ID)
        except Exception as exc:  # noqa: BLE001 - um caso falhado não derruba a corrida
            return [], f"{case['id']}: {type(exc).__name__}: {exc}", None
        return score_case(case, request, result, tolerance), None, getattr(result, "model", None)

    if concurrency > 1:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            outcomes = list(pool.map(one, cases))
    else:
        outcomes = [one(case) for case in cases]
    rows = [row for out in outcomes for row in out[0]]
    errors = [out[1] for out in outcomes if out[1]]
    resolved = next((out[2] for out in outcomes if out[2]), None)
    samples = [
        cal.Sample(confidence=r["confidence"], correct=r["correct"], role=r["role"], qtype=r["qtype"],
                   question=r["question"], case=r["id"], source="golden", available=r["available"])
        for r in rows if not r.get("skipped")
    ]
    by_question = {
        name: cal.group_metrics(group)
        for name, group in sorted(_group(samples, lambda s: f"{s.role}/{s.question}").items())
    }
    return {
        "cases": len(cases), "errors": errors, "resolved_model": resolved, "rows": rows,
        "skipped": sum(1 for r in rows if r.get("skipped")),
        "roles": {name: cal.group_metrics(group)
                  for name, group in sorted(_group(samples, lambda s: s.role).items())},
        "questions": by_question,
    }


def _group(samples: Sequence[Any], key: Callable[[Any], Any]) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    for sample in samples:
        out.setdefault(str(key(sample)), []).append(sample)
    return out


def compare_baseline(roles: Mapping[str, Mapping[str, Any]], baseline_roles: Mapping[str, Any], *,
                     tolerance: float = ACCURACY_TOLERANCE, ece_tolerance: float = ECE_TOLERANCE,
                     min_support: int = MIN_GATE_SUPPORT) -> list[dict[str, Any]]:
    """Regressões por papel: accuracy caiu mais do que ``tolerance`` ou o ECE subiu."""
    found: list[dict[str, Any]] = []
    for role, now in sorted(roles.items()):
        before = baseline_roles.get(role)
        if not isinstance(before, Mapping) or int(now.get("n") or 0) < min_support:
            continue
        acc_now, acc_then = now.get("accuracy"), before.get("accuracy")
        if isinstance(acc_now, (int, float)) and isinstance(acc_then, (int, float)):
            if acc_then - acc_now > tolerance + 1e-9:
                found.append({"role": role, "metric": "accuracy", "baseline": acc_then,
                              "current": acc_now, "drop": round(acc_then - acc_now, 4),
                              "tolerance": tolerance})
        ece_now, ece_then = now.get("ece"), before.get("ece")
        if isinstance(ece_now, (int, float)) and isinstance(ece_then, (int, float)):
            if ece_now - ece_then > ece_tolerance + 1e-9:
                found.append({"role": role, "metric": "ece", "baseline": ece_then, "current": ece_now,
                              "rise": round(ece_now - ece_then, 4), "tolerance": ece_tolerance})
    return found


def load_baseline(path: Path, split: str) -> tuple[str, dict[str, Any]]:
    """``(estado, papéis)``: ``unset`` sem baseline útil para este *split*."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "missing", {}
    section = (data.get("splits") or {}).get(split) if isinstance(data, dict) else None
    if not isinstance(section, dict) or not isinstance(section.get("roles"), dict):
        return "unset", {}
    return "set", section["roles"]


def update_baseline(path: Path, split: str, report: Mapping[str, Any]) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        data = {}
    splits = data.get("splits") if isinstance(data.get("splits"), dict) else {}
    splits[split] = {"roles": report["roles"], "answers": report["answers"],
                     "resolved_model": report.get("resolved_model"), "date": report["date"]}
    doc = {"format": 1, "status": "set", "model": report["model"], "date": report["date"],
           "splits": splits}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
