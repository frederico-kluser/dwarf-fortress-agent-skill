#!/usr/bin/env python3
"""CLI de calibração do Jev (ARQUITETURA, mudança 06): curvas risco-cobertura e tabela por snapshot.

Mede se a confiança que o modelo reporta corresponde à frequência real de
acerto, por **classe de risco** da ação e por papel, e escreve uma tabela de
calibração **chaveada pelo snapshot** do modelo (``typesafe/jev-1.13``). Sem
entrada para o modelo configurado, o loop usa os pisos conservadores da política
(``jev/risk.py``): snapshot novo ⇒ recalibrar.

Comandos::

    report         --evals DIR|FILE... (--results FILE | --fake-model) --out FILE
                   [--table calibration.json] [--model ID] [--samples FILE]
                   [--mark-verified CLASS,...] [--min-support 30] [--bound wilson]
    from-decisions --decisions FILE --approvals FILE --out FILE [--table ...]
    online         --model typesafe/jev-1.13 --key-env SOD_OPENROUTER_KEY
                   [--evals DIR|FILE] [--out results.json]  (gasta tokens; sem testes)
    thresholds     --table calibration.json --model ID [--base 0.90]

``--fake-model`` usa um modelo falso **determinístico** só para exercitar o
pipeline em CI: a entrada que escreve fica marcada ``source=fake-model`` e é
ignorada pelo carregador (``allow_fake`` é opt-in). ``report --results`` aceita
tanto a saída de ``online`` como a de ``jev.mjs eval`` (as ``rows``).

Códigos de saída: 0 ok · 1 sem amostras / nada a escrever · 2 uso ou ambiente ·
3 dados de entrada inválidos. A chave nunca é impressa.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "src",):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from system_one_dwarf.jev import calibration as cal  # noqa: E402
from system_one_dwarf.jev import risk  # noqa: E402

EXIT_OK, EXIT_EMPTY, EXIT_USAGE, EXIT_DATA = 0, 1, 2, 3

DEFAULT_MODEL = "typesafe/jev-1.13"
FAKE_MODEL_ID = "fake/jev-fake"
DEFAULT_KEY_ENV = "SOD_OPENROUTER_KEY"
DEFAULT_EVALS = ROOT / "evals"
#: Exatidão do modelo falso e fração de respostas sem ``confidence`` (a "moeda ao ar").
FAKE_ACCURACY = 0.85
FAKE_UNAVAILABLE_FRACTION = 0.10
DEFAULT_CONCURRENCY = 4
ACTION_QUESTION = "action"


class DataError(ValueError):
    """Entrada ilegível/inválida (exit 3), com mensagem pronta para o operador."""


# ------------------------------------------------------------------ casos / IO


def _read_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DataError(f"cannot read {path}: {exc}") from None
    try:
        return json.loads(text)
    except ValueError as exc:
        raise DataError(f"{path} is not valid JSON: {exc}") from None


def load_eval_cases(sources: Iterable[str | os.PathLike[str]]) -> list[dict[str, Any]]:
    """Casos ``{name, role, state, questions, expected}`` de ficheiros ``*.json``/dirs.

    Um diretório contribui com os seus ``*.json`` de topo (sem recursão); cada
    ficheiro é um *array* de casos. Nomes repetidos são um erro (as linhas de
    resultado seriam ambíguas).
    """
    files: list[Path] = []
    for source in sources:
        path = Path(source)
        if path.is_dir():
            files.extend(sorted(path.glob("*.json")))
        elif path.exists():
            files.append(path)
        else:
            raise DataError(f"evals source {path} does not exist")
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in files:
        data = _read_json(path)
        if not isinstance(data, list):
            continue  # p.ex. calibration.json ou relatórios guardados em evals/
        for item in data:
            if not (isinstance(item, dict) and isinstance(item.get("name"), str)
                    and isinstance(item.get("questions"), dict) and isinstance(item.get("expected"), dict)):
                continue
            if item["name"] in seen:
                raise DataError(f"duplicate case name {item['name']!r} (in {path.name})")
            seen.add(item["name"])
            cases.append(item)
    if not cases:
        raise DataError("no eval cases found in " + ", ".join(str(s) for s in sources))
    return cases


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# -------------------------------------------------------- linhas de resultado


def _row(case: Mapping[str, Any], qid: str, qtype: str, expected: Any, predicted: Any,
         confidence: Any) -> dict[str, Any] | None:
    scored = cal.score_answer(qtype, expected, predicted, confidence)
    if scored is None:
        return None
    correct, conf, available = scored
    return {"name": case["name"], "role": case.get("role"), "qid": qid, "type": qtype,
            "expected": expected, "predicted": predicted, "correct": correct,
            "confidence": round(conf, 4), "available": available}


class FakeModel:
    """Modelo falso determinístico (hash de semente|caso|pergunta). **Só para CI.**

    Acerta ``accuracy`` das respostas; quando acerta a confiança é alta
    (0.70–0.99), quando falha é média (0.50–0.90): fica ligeiramente
    sobre-confiante de propósito, para que ECE/curvas tenham o que medir.
    """

    def __init__(self, seed: int = 0, accuracy: float = FAKE_ACCURACY,
                 unavailable: float = FAKE_UNAVAILABLE_FRACTION) -> None:
        self.seed = int(seed)
        self.accuracy = float(accuracy)
        self.unavailable = float(unavailable)

    def _u(self, *parts: Any) -> tuple[float, float, float]:
        digest = hashlib.sha256("|".join(str(p) for p in (self.seed, *parts)).encode()).digest()
        return tuple(int.from_bytes(digest[i:i + 4], "big") / 2**32 for i in (0, 4, 8))  # type: ignore[return-value]

    def answer(self, case: Mapping[str, Any], qid: str, body: Mapping[str, Any],
               expected: Any) -> tuple[Any, Any]:
        """``(valor, confidence)`` que o modelo falso daria a esta pergunta."""
        u_right, u_conf, u_unavail = self._u(case["name"], qid)
        right = u_right < self.accuracy
        conf = 0.70 + 0.29 * u_conf if right else 0.50 + 0.40 * u_conf
        qtype = body.get("type")
        if qtype == "noul":
            truth = bool(expected)
            toward_true = truth if right else not truth
            return (conf if toward_true else 1.0 - conf), None
        confidence: float | None = None if u_unavail < self.unavailable else round(conf, 3)
        if qtype == "choice":
            if right:
                return expected, confidence
            options = sorted(k for k in (body.get("criteria") or {}) if k != expected)
            return (options[int(u_conf * len(options)) % len(options)] if options else expected), confidence
        levels = len(body.get("criteria") or [])
        value = float(expected)
        if not right:
            value = value + 1.0 if value + 1.0 <= max(levels - 1, 0) else value - 1.0
        return value, confidence


def fake_rows(cases: Sequence[Mapping[str, Any]], model: FakeModel) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        for qid, expected in case["expected"].items():
            body = case["questions"].get(qid)
            if not isinstance(body, Mapping):
                continue
            value, confidence = model.answer(case, qid, body, expected)
            row = _row(case, qid, str(body.get("type")), expected, value, confidence)
            if row:
                rows.append(row)
    return rows


def make_client(model: str, key: str) -> Any:
    """Cliente Jev real (os testes substituem esta função por um fake)."""
    from system_one_dwarf.jev.client import JevClient  # noqa: PLC0415

    return JevClient(key, model=model)


def online_rows(cases: Sequence[Mapping[str, Any]], client: Any, *, concurrency: int = 1,
                ) -> tuple[list[dict[str, Any]], list[str], str | None]:
    """Corre os casos no cliente (um pedido por caso); ``(linhas, erros, snapshot resolvido)``."""

    def one(case: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str | None, str | None]:
        try:
            result = client.decide_request({"state": case["state"], "questions": case["questions"]})
        except Exception as exc:  # noqa: BLE001 - um caso falhado não derruba a corrida
            return [], f"{case['name']}: {type(exc).__name__}: {exc}", None
        out: list[dict[str, Any]] = []
        for qid, expected in case["expected"].items():
            body = case["questions"].get(qid)
            answer = (getattr(result, "answers", {}) or {}).get(qid)
            if answer is None or not isinstance(body, Mapping):
                continue
            row = _row(case, qid, str(body.get("type")), expected,
                       getattr(answer, "value", None), getattr(answer, "confidence", None))
            if row:
                out.append({**row, "band": getattr(answer, "band", None)})
        return out, None, getattr(result, "model", None)

    if concurrency > 1:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            outcomes = list(pool.map(one, cases))
    else:
        outcomes = [one(case) for case in cases]
    rows = [row for out in outcomes for row in out[0]]
    errors = [out[1] for out in outcomes if out[1]]
    resolved = next((out[2] for out in outcomes if out[2]), None)
    return rows, errors, resolved


def rows_to_samples(rows: Iterable[Mapping[str, Any]], roles: Mapping[str, str],
                    *, source: str) -> list[cal.Sample]:
    """Linhas (``online``/``jev.mjs eval``) → amostras; a ação escolhida dá a classe de risco."""
    samples: list[cal.Sample] = []
    for row in rows:
        if not isinstance(row, Mapping) or row.get("correct") is None:
            continue
        name = str(row.get("name") or row.get("case") or "")
        qid = str(row.get("qid") or row.get("question") or "")
        confidence = row.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            confidence = cal.UNAVAILABLE_CONFIDENCE
        is_action = qid == ACTION_QUESTION and row.get("type", "choice") == "choice"
        predicted = row.get("predicted")
        item = cal.Sample.from_any({
            "confidence": float(confidence), "correct": bool(row.get("correct")),
            "role": row.get("role") or roles.get(name), "qtype": row.get("type"),
            "question": qid or None, "case": name or None, "source": source,
            "risk_class": risk.risk_class(predicted) if is_action and isinstance(predicted, str) else None,
            "available": row.get("available", True) is not False,
        })
        if item is not None:
            samples.append(item)
    return samples


# ----------------------------------------------------------------- comandos


def _settings(args: argparse.Namespace) -> dict[str, Any]:
    bound = args.bound if args.bound in cal.BOUNDS else cal.DEFAULT_BOUND
    return {"min_support": int(args.min_support), "bound": bound, "z": args.z}


def _print_summary(report: Mapping[str, Any]) -> None:
    overall = report["overall"]
    print(f"model {report.get('model')} · source {report.get('source')} · n={overall['n']} "
          f"accuracy={overall['accuracy']} ece={overall['ece']} ({overall['ece_label']}) "
          f"brier={overall['brier']}")
    for role, metrics in report["by_role"].items():
        print(f"  {role}: n={metrics['n']} accuracy={metrics['accuracy']} ece={metrics['ece']}")
    for klass, row in report["by_class"].items():
        choice = row["selection"]
        print(f"  class {klass}: n={row['n']} floor={row['policy_floor']} "
              f"target_risk={row['target_risk']} -> threshold={choice['threshold']} ({choice['reason']})")


def _finish_report(report: dict[str, Any], args: argparse.Namespace,
                   samples: Sequence[cal.Sample]) -> int:
    out = Path(args.out)
    write_json(out, {**report, "samples": [s.to_dict() for s in samples]}
               if getattr(args, "keep_samples", False) else report)
    _print_summary(report)
    if args.table:
        marked = [c for c in (args.mark_verified or "").split(",") if c.strip()]
        unknown = [c for c in marked if c.strip() not in risk.RISK_CLASSES]
        if unknown:
            print(f"--mark-verified: unknown risk class(es) {unknown}", file=sys.stderr)
            return EXIT_USAGE
        table = cal.CalibrationTable.load(args.table)
        entry = cal.ModelCalibration.from_report(report, verified=[c.strip() for c in marked])
        table.put(entry)
        table.save(args.table)
        print(f"calibration table updated: {args.table} (model {entry.model!r})")
    print(f"report written: {out}")
    return EXIT_OK


def cmd_report(args: argparse.Namespace) -> int:
    if bool(args.results) == bool(args.fake_model):
        print("report needs exactly one of --results FILE (a recorded run) or --fake-model",
              file=sys.stderr)
        return EXIT_USAGE
    try:
        cases = load_eval_cases(args.evals or [DEFAULT_EVALS])
        roles = {c["name"]: str(c.get("role")) for c in cases}
        resolved = args.resolved_model
        if args.fake_model:
            rows, source = fake_rows(cases, FakeModel(seed=args.seed)), cal.SOURCE_FAKE
            model = args.model or FAKE_MODEL_ID
        else:
            data = _read_json(Path(args.results))
            rows = data.get("rows") if isinstance(data, dict) else data
            if not isinstance(rows, list):
                raise DataError(f"{args.results} has no 'rows' array")
            source = "eval"
            model = args.model or (data.get("model") if isinstance(data, dict) else None) or DEFAULT_MODEL
            resolved = resolved or (data.get("resolved_model") if isinstance(data, dict) else None)
        samples = rows_to_samples(rows, roles, source=source)
        for extra in args.samples or []:
            payload = _read_json(Path(extra))
            items = payload.get("samples") if isinstance(payload, dict) else payload
            samples.extend(cal.coerce_samples(items if isinstance(items, list) else []))
    except DataError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_DATA
    if not samples:
        print("no scorable answers: nothing to report", file=sys.stderr)
        return EXIT_EMPTY
    report = cal.build_report(
        samples, model=model, resolved_model=resolved, source=source,
        eval_fingerprint=cal.fingerprint_cases(cases), report_date=args.date or today(),
        **_settings(args),
    )
    return _finish_report(report, args, samples)


def cmd_from_decisions(args: argparse.Namespace) -> int:
    try:
        raw_decisions = _read_json(Path(args.decisions))
        raw_approvals = _read_json(Path(args.approvals))
    except DataError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_DATA
    decisions = raw_decisions.get("decisions") if isinstance(raw_decisions, dict) else raw_decisions
    approvals = raw_approvals.get("approvals") if isinstance(raw_approvals, dict) else raw_approvals
    if not isinstance(decisions, list) or not isinstance(approvals, list):
        print("--decisions and --approvals must be JSON arrays (or objects holding one)",
              file=sys.stderr)
        return EXIT_DATA
    labeled = cal.label_decisions(decisions, approvals, roles=tuple(args.roles.split(",")))
    print(f"labeled {len(labeled.samples)} decision(s); labels {labeled.labels}; skipped {labeled.skipped}")
    if not labeled.samples:
        return EXIT_EMPTY
    report = cal.build_report(
        labeled.samples, model=args.model, source="decisions",
        report_date=args.date or today(), **_settings(args),
    )
    report["labeling"] = {"labels": labeled.labels, "skipped": labeled.skipped,
                          "proxy": "approved/executed-ok=correct, rejected/aborted=incorrect"}
    args.keep_samples = True  # o ficheiro serve de --samples a ``report``
    return _finish_report(report, args, labeled.samples)


def cmd_online(args: argparse.Namespace) -> int:
    key = os.environ.get(args.key_env, "")
    if not key.strip():
        print(f"environment variable {args.key_env} is not set (the key is never printed)",
              file=sys.stderr)
        return EXIT_USAGE
    try:
        cases = load_eval_cases(args.evals or [DEFAULT_EVALS])
    except DataError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_DATA
    if args.max_cases:
        cases = cases[: args.max_cases]
    client = make_client(args.model, key)
    try:
        rows, errors, resolved = online_rows(cases, client, concurrency=args.concurrency)
    finally:
        closer = getattr(client, "close", None)
        if callable(closer):
            closer()
    write_json(Path(args.out), {
        "format": 1, "source": "online", "model": args.model, "resolved_model": resolved,
        "date": args.date or today(), "cases": len(cases), "errors": errors, "rows": rows,
    })
    print(f"{len(rows)} scored answers from {len(cases)} cases ({len(errors)} failed) -> {args.out}")
    print("next: scripts/calibrate.py report --results " + str(args.out) + " --out report.json "
          "--table calibration.json")
    return EXIT_OK if rows else EXIT_EMPTY


def cmd_thresholds(args: argparse.Namespace) -> int:
    table = cal.CalibrationTable.load(args.table)
    lookup = table.lookup(args.model, args.resolved_model, allow_fake=args.allow_fake)
    effective = risk.class_thresholds(lookup.calibration, base=args.base)
    payload = {"model": args.model, "status": lookup.status, "detail": lookup.detail,
               "base": args.base, "thresholds": effective,
               "destructive_auto": risk.DEFAULT_POLICY.destructive_auto,
               "table_issues": table.issues}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"model {args.model}: calibration {lookup.status}" + (f" ({lookup.detail})" if lookup.detail else ""))
        for klass in risk.RISK_CLASSES:
            note = "" if risk.auto_allowed(klass) else "  (auto disabled by policy)"
            print(f"  {klass:<12} auto >= {effective[klass]:.2f}{note}")
        for issue in table.issues:
            print(f"  table issue: {issue}")
    return EXIT_OK


# --------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="calibrate.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def stats(p: argparse.ArgumentParser) -> None:
        p.add_argument("--min-support", type=int, default=cal.DEFAULT_MIN_SUPPORT)
        p.add_argument("--bound", default=cal.DEFAULT_BOUND, choices=cal.BOUNDS)
        p.add_argument("--z", type=float, default=cal.DEFAULT_Z)
        p.add_argument("--date", help="report date (YYYY-MM-DD; default: today UTC)")

    p = sub.add_parser("report", help="calibration report (+ table entry) from recorded or fake results")
    p.add_argument("--evals", nargs="+", help="eval directory/files (default: evals/)")
    p.add_argument("--results", help="recorded rows (from `online` or `jev.mjs eval`)")
    p.add_argument("--fake-model", action="store_true", help="deterministic fake model (CI only)")
    p.add_argument("--seed", type=int, default=0, help="fake-model seed")
    p.add_argument("--samples", nargs="*", help="extra samples (output of from-decisions)")
    p.add_argument("--out", required=True)
    p.add_argument("--table", help="calibration table to update (keyed by model snapshot)")
    p.add_argument("--model", help="model id the entry is keyed by (default: typesafe/jev-1.13)")
    p.add_argument("--resolved-model", help="snapshot id the API resolved (e.g. typesafe/jev-1.13-20260917)")
    p.add_argument("--mark-verified", help="comma-separated risk classes a human verified")
    stats(p)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("from-decisions", help="labels from the real decisions log + approvals")
    p.add_argument("--decisions", required=True)
    p.add_argument("--approvals", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--table")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--mark-verified")
    p.add_argument("--roles", default="JEV-A", help="comma-separated roles to label")
    stats(p)
    p.set_defaults(func=cmd_from_decisions)

    p = sub.add_parser("online", help="run the eval cases against the real model (spends tokens)")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--key-env", default=DEFAULT_KEY_ENV)
    p.add_argument("--evals", nargs="+")
    p.add_argument("--out", default="calibration-results.json",
                   help="recorded rows for `report --results` (default: ./calibration-results.json)")
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    p.add_argument("--max-cases", type=int, default=0)
    p.add_argument("--date")
    p.set_defaults(func=cmd_online)

    p = sub.add_parser("thresholds", help="effective auto thresholds per risk class for a model")
    p.add_argument("--table", required=True)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--resolved-model")
    p.add_argument("--base", type=float, default=0.90)
    p.add_argument("--allow-fake", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_thresholds)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse usa 2 para erros de uso — o nosso EXIT_USAGE
        return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE
    try:
        return int(args.func(args))
    except DataError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_DATA
    except BrokenPipeError:  # pragma: no cover
        return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
