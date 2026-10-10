#!/usr/bin/env python3
"""CLI do golden set de regressão de prompts (ARQUITETURA, mudança 12).

O golden set são os 66 cenários de ``evals/`` (``source=seed``) mais os cenários
sintéticos de ``evals/golden/scenarios.json`` (etiquetas derivadas de regras de
segurança explícitas), com um **hold-out** determinístico (``sha1(chave) % 100 <
20``, ~20 casos) fixado por hash. Tudo o que não é ``run`` corre offline.

Comandos::

    check            esquema, ids únicos, partição, pin do hold-out, regeneração
                     das fontes e regressão de prompt (pedido reconstruído com os
                     builders ACTUAIS == hash em prompts.lock.json)
    bless --reason   recalcula os hashes depois de uma mudança INTENCIONAL de
                     prompt (só o lock; nunca toca em etiquetas) e regista-a no
                     CHANGELOG.md
    build            regenera dev.jsonl a partir das fontes (evals/*.json +
                     scenarios.json); diz se o hold-out mudaria
    freeze-holdout   regenera holdout.jsonl, escreve o pin HOLDOUT.sha256 e
                     regista a razão — o ÚNICO comando que altera o hold-out
    run --online     avalia contra o modelo real (gasta tokens; precisa da
                     variável de ambiente da chave) e compara com o baseline
    tune-view        mostra só os casos de dev (o hold-out fica invisível)
    show ID          um caso + o pedido reconstruído (hold-out só com
                     --allow-holdout)
    export           casos no formato de ``evals/*.json`` (para ``jev.mjs eval``
                     ou ``scripts/calibrate.py``); por omissão só dev

Códigos de saída: 0 ok · 1 verificação falhou/regressão · 2 uso ou ambiente
(ficheiro ausente, chave em falta, ``--online`` omitido) · 3 corrida inválida
(demasiados erros de rede).

Disciplina (docs/calibracao.md §14): afina-se o prompt com ``tune-view`` (dev);
o hold-out só se reporta no fim (``run --split holdout``), sem linhas por caso.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = ROOT / "evals" / "golden"
EVALS_DIR = ROOT / "evals"
for _path in (ROOT / "src", GOLDEN_DIR):  # os módulos do golden set vivem junto dos dados
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

_previous_bytecode = sys.dont_write_bytecode
sys.dont_write_bytecode = True  # evals/golden é diretório de dados: sem __pycache__
try:
    import golden_core as core  # noqa: E402
    import golden_rules as rules  # noqa: E402,F401
    import golden_run  # noqa: E402
    import golden_sources as sources  # noqa: E402
finally:
    sys.dont_write_bytecode = _previous_bytecode

from golden_run import (  # noqa: E402,F401  (re-exportados: a CLI e os testes usam-nos daqui)
    ACCURACY_TOLERANCE, MAX_ERROR_FRACTION, compare_baseline, evaluate, load_baseline,
)

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_INVALID = 0, 1, 2, 3

DEV_FILE = "dev.jsonl"
HOLDOUT_FILE = "holdout.jsonl"
PIN_FILE = "HOLDOUT.sha256"
LOCK_FILE = "prompts.lock.json"
CHANGELOG_FILE = "CHANGELOG.md"
BASELINE_FILE = "baseline.json"
SCENARIOS_FILE = "scenarios.json"

DEFAULT_CONCURRENCY = 4
DEFAULT_MODEL = "typesafe/jev-1.13"
DEFAULT_KEY_ENV = "SOD_OPENROUTER_KEY"

#: Códigos do ``check`` que ``bless`` pode resolver (todos os outros bloqueiam-no).
BLESSABLE = frozenset({"prompt-drift", "lock-missing", "lock-stale"})


# ---------------------------------------------------------------- caminhos / IO


@dataclass(frozen=True)
class Paths:
    golden: Path = GOLDEN_DIR
    evals: Path = EVALS_DIR

    @property
    def dev(self) -> Path:
        return self.golden / DEV_FILE

    @property
    def holdout(self) -> Path:
        return self.golden / HOLDOUT_FILE

    @property
    def pin(self) -> Path:
        return self.golden / PIN_FILE

    @property
    def lock(self) -> Path:
        return self.golden / LOCK_FILE

    @property
    def changelog(self) -> Path:
        return self.golden / CHANGELOG_FILE

    @property
    def baseline(self) -> Path:
        return self.golden / BASELINE_FILE

    @property
    def scenarios(self) -> Path:
        return self.golden / SCENARIOS_FILE


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_cases(path: Path) -> list[dict[str, Any]]:
    return core.loads_cases(path.read_text(encoding="utf-8"))


def read_lock(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(cases, dict):
        raise ValueError("lock file has no 'cases' object")
    return {str(k): str(v) for k, v in cases.items()}


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_lock(path: Path, hashes: Mapping[str, str]) -> None:
    doc = {"format": 1, "algorithm": "sha256", "cases": dict(sorted(hashes.items()))}
    write_text(path, json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def read_pin(path: Path) -> str | None:
    try:
        first = path.read_text(encoding="utf-8").split()
    except OSError:
        return None
    return first[0].lower() if first else None


def write_pin(path: Path, holdout_path: Path) -> str:
    digest = sha256_file(holdout_path)
    write_text(path, f"{digest}  {holdout_path.name}\n")  # formato do `sha256sum -c`
    return digest


def append_changelog(paths: Paths, command: str, reason: str, date: str, detail: str) -> None:
    header = (
        "# Golden set — changelog\n\n"
        "Cada linha regista uma alteração INTENCIONAL (`bless` = prompt mudou de propósito; "
        "`freeze-holdout` = o hold-out mudou). Sem linha aqui, a mudança não é legítima.\n\n"
    )
    existing = paths.changelog.read_text(encoding="utf-8") if paths.changelog.exists() else header
    if not existing.endswith("\n"):
        existing += "\n"
    clean = " ".join(reason.split())
    write_text(paths.changelog, existing + f"- {date} · {command} · {clean} — {detail}\n")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ------------------------------------------------------------------- check


@dataclass
class CheckResult:
    problems: list[Any] = field(default_factory=list)
    hashes: dict[str, str] = field(default_factory=dict)
    cases: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def errors(self) -> list[Any]:
        return [p for p in self.problems if p.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors


def _problem(result: CheckResult, code: str, message: str, case: str | None = None,
             severity: str = "error") -> None:
    result.problems.append(core.Problem(code=code, message=message, case=case, severity=severity))


def _diff_keys(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[str]:
    return sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))


def run_check(paths: Paths) -> CheckResult:
    """Todas as verificações offline; nunca levanta (o que falha vira um achado)."""
    result = CheckResult()
    loaded: dict[str, list[dict[str, Any]]] = {}
    for label, path in (("dev", paths.dev), ("holdout", paths.holdout)):
        try:
            loaded[label] = read_cases(path)
        except FileNotFoundError:
            _problem(result, "file-missing", f"{path.name} does not exist (run build / freeze-holdout)")
            loaded[label] = []
        except (OSError, ValueError) as exc:
            _problem(result, "file-unreadable", f"{path.name}: {exc}")
            loaded[label] = []
    dev, hold = loaded["dev"], loaded["holdout"]
    cases = dev + hold
    result.cases = cases

    # 1. esquema + ids únicos + partição
    seen: dict[str, str] = {}
    for label, group in (("dev", dev), ("holdout", hold)):
        for case in group:
            cid = str(case.get("id"))
            for message in core.validate_case(case):
                _problem(result, "schema", message, cid)
            if cid in seen:
                _problem(result, "duplicate-id", f"id also present in {seen[cid]}", cid)
            seen[cid] = label
            if isinstance(case.get("id"), str) and core.split_of(case) != label:
                _problem(result, "split", f"belongs to {core.split_of(case)} by the deterministic "
                         f"split but sits in {label}.jsonl", cid)
    if len(cases) < core.MIN_CASES:
        _problem(result, "count", f"{len(cases)} cases, the golden set needs at least {core.MIN_CASES}")

    # 2. pin do hold-out
    pin = read_pin(paths.pin)
    if not paths.holdout.exists():
        pass  # já reportado como file-missing
    elif pin is None:
        _problem(result, "pin-missing", f"{PIN_FILE} is missing (run freeze-holdout --reason ...)")
    elif sha256_file(paths.holdout) != pin:
        _problem(result, "pin-mismatch",
                 f"{HOLDOUT_FILE} does not match {PIN_FILE}: the hold-out was edited; restore it or "
                 "run freeze-holdout --reason ... (an explicit, logged decision)")

    # 3. fontes regeneradas (build-drift / seed-drift)
    try:
        stored_seeds = {str(c.get("id")): c for c in cases if c.get("source") == "seed"}
        expected_cases = {c["id"]: c for c in sources.build_sources(
            paths.evals, paths.scenarios, known=stored_seeds)}
    except Exception as exc:  # noqa: BLE001
        expected_cases = {}
        _problem(result, "build-error", f"could not regenerate the cases from the sources: {exc}")
    have = {str(c.get("id")): c for c in cases}
    for cid, raw in expected_cases.items():
        want = json.loads(core.canonical_json(raw))  # mesma forma que a lida do disco (JSON)
        got = have.get(cid)
        if got is None:
            _problem(result, "case-missing", "present in the sources but not in the golden files "
                     "(run build / freeze-holdout)", cid)
        elif got != want:
            keys = _diff_keys(got, want)
            if want.get("source") == "seed" and "origin" in keys:
                _problem(result, "seed-drift", f"the seed changed in evals/{want['origin']['file']} "
                         "since it was imported; re-run build/freeze-holdout deliberately", cid)
            else:
                _problem(result, "build-drift", f"stored case differs from the regenerated one "
                         f"({', '.join(keys)}); rules or builders changed — run build/freeze-holdout", cid)
    if expected_cases:
        for cid in have.keys() - expected_cases.keys():
            _problem(result, "case-orphan", "in the golden files but in no source", cid)

    # 4. reconstrução (builders actuais), etiquetas e hash do prompt
    for case in cases:
        cid = str(case.get("id"))
        try:
            request = core.rebuild_request(case)
        except Exception as exc:  # noqa: BLE001
            _problem(result, "rebuild-error", f"cannot rebuild the request: {exc}", cid)
            continue
        build = case.get("build") or {}
        needs_validation = bool(build.get("guard")) or build.get("kind") == "frozen"
        for message in core.validate_labels(case, request, revalidate=needs_validation):
            _problem(result, "labels", message, cid)
        if build.get("kind") == "frozen":
            _problem(result, "frozen", "frozen request: integrity only, no prompt regression "
                     "(its builder inputs could not be recovered)", cid, severity="warning")
        result.hashes[cid] = core.request_hash(request)

    # 5. regressão de prompt contra o lock
    try:
        lock = read_lock(paths.lock)
    except FileNotFoundError:
        lock = None
        _problem(result, "lock-missing", f"{LOCK_FILE} does not exist (run bless --reason ...)")
    except (OSError, ValueError) as exc:
        lock = None
        _problem(result, "lock-missing", f"{LOCK_FILE} unreadable: {exc}")
    if lock is not None:
        for cid, digest in sorted(result.hashes.items()):
            if cid not in lock:
                _problem(result, "lock-missing", "no hash in the lock (new case; run bless)", cid)
            elif lock[cid] != digest:
                _problem(result, "prompt-drift", "the request built by the CURRENT builders differs "
                         "from the locked one (prompt regression; if intentional run bless --reason)", cid)
        for cid in sorted(lock.keys() - have.keys()):
            _problem(result, "lock-stale", "lock entry of a case that no longer exists", cid)

    roles: dict[str, int] = {}
    for case in cases:
        roles[str(case.get("role"))] = roles.get(str(case.get("role")), 0) + 1
    result.stats = {
        "cases": len(cases), "dev": len(dev), "holdout": len(hold),
        "seed": sum(1 for c in cases if c.get("source") == "seed"),
        "synthetic": sum(1 for c in cases if c.get("source") == "synthetic"),
        "roles": dict(sorted(roles.items())),
    }
    return result


def cmd_check(args: argparse.Namespace) -> int:
    paths = _paths(args)
    result = run_check(paths)
    stats = result.stats
    if args.json:
        print(json.dumps({"ok": result.ok, "stats": stats,
                          "problems": [p.to_dict() for p in result.problems]},
                         ensure_ascii=False, indent=1))
    else:
        print(f"golden set: {stats['cases']} cases (dev {stats['dev']}, hold-out {stats['holdout']}) "
              f"· seed {stats['seed']} · synthetic {stats['synthetic']} · roles {stats['roles']}")
        for problem in result.problems:
            print(problem.line())
        print("OK: all checks passed" if result.ok else f"FAILED: {len(result.errors)} error(s)")
    failing = result.errors or (result.problems if args.strict else [])
    return EXIT_FAIL if failing else EXIT_OK


# -------------------------------------------------- bless / build / freeze


def cmd_bless(args: argparse.Namespace) -> int:
    paths = _paths(args)
    reason = " ".join((args.reason or "").split())
    if not reason:
        print("bless needs --reason <text> (why did the prompt change on purpose?)", file=sys.stderr)
        return EXIT_USAGE
    result = run_check(paths)
    blocking = [p for p in result.errors if p.code not in BLESSABLE]
    if blocking:
        print("bless refused: fix these first (bless only re-hashes prompts, never labels):")
        for problem in blocking:
            print("  " + problem.line())
        return EXIT_FAIL
    try:
        old = read_lock(paths.lock)
    except (OSError, ValueError):
        old = {}
    new = dict(result.hashes)
    added = sorted(new.keys() - old.keys())
    removed = sorted(old.keys() - new.keys())
    changed = sorted(k for k in new.keys() & old.keys() if new[k] != old[k])
    detail = f"{len(new)} cases · {len(changed)} changed, {len(added)} new, {len(removed)} removed"
    if args.dry_run:
        print("dry-run: " + detail)
        for cid in changed + added:
            print("  would update " + cid)
        return EXIT_OK
    write_lock(paths.lock, new)
    append_changelog(paths, "bless", reason, args.date or today(), detail)
    print(f"blessed: {detail}")
    return EXIT_OK


def _stored_seeds(paths: Paths, args: argparse.Namespace) -> dict[str, Any]:
    """Seeds já guardados (para não refazer a procura de sementes), salvo ``--reimport-seeds``."""
    if getattr(args, "reimport_seeds", False):
        return {}
    stored: dict[str, Any] = {}
    for path in (paths.dev, paths.holdout):
        try:
            stored.update({str(c["id"]): c for c in read_cases(path) if c.get("source") == "seed"})
        except (OSError, ValueError, KeyError):
            continue
    return stored


def cmd_build(args: argparse.Namespace) -> int:
    paths = _paths(args)
    try:
        cases = sources.build_sources(paths.evals, paths.scenarios, known=_stored_seeds(paths, args))
    except Exception as exc:  # noqa: BLE001
        print(f"build failed: {exc}", file=sys.stderr)
        return EXIT_FAIL
    dev = [c for c in cases if core.split_of(c) == "dev"]
    hold = [c for c in cases if core.split_of(c) == "holdout"]
    text = core.dumps_cases(dev)
    unchanged = paths.dev.exists() and paths.dev.read_text(encoding="utf-8") == text
    if not unchanged:
        write_text(paths.dev, text)
    print(f"dev.jsonl: {len(dev)} cases ({'unchanged' if unchanged else 'written'}); "
          f"hold-out would hold {len(hold)} cases")
    hold_text = core.dumps_cases(hold)
    if not paths.holdout.exists() or paths.holdout.read_text(encoding="utf-8") != hold_text:
        print("hold-out differs from the regenerated one: run freeze-holdout --reason ... "
              "(deliberately) to change it")
        return EXIT_FAIL if paths.holdout.exists() else EXIT_OK
    return EXIT_OK


def cmd_freeze(args: argparse.Namespace) -> int:
    paths = _paths(args)
    reason = " ".join((args.reason or "").split())
    if not reason:
        print("freeze-holdout needs --reason <text> (why is the hold-out changing?)", file=sys.stderr)
        return EXIT_USAGE
    try:
        cases = sources.build_sources(paths.evals, paths.scenarios, known=_stored_seeds(paths, args))
    except Exception as exc:  # noqa: BLE001
        print(f"freeze-holdout failed: {exc}", file=sys.stderr)
        return EXIT_FAIL
    hold = [c for c in cases if core.split_of(c) == "holdout"]
    write_text(paths.holdout, core.dumps_cases(hold))
    digest = write_pin(paths.pin, paths.holdout)
    append_changelog(paths, "freeze-holdout", reason, args.date or today(),
                     f"{len(hold)} cases · sha256 {digest[:12]}")
    print(f"hold-out frozen: {len(hold)} cases · sha256 {digest}")
    return EXIT_OK


# ----------------------------------------------------------- vista / export


def _dev_cases(paths: Paths) -> list[dict[str, Any]]:
    return sorted(read_cases(paths.dev), key=lambda c: str(c["id"]))


def cmd_tune_view(args: argparse.Namespace) -> int:
    """Só dev: o hold-out nem é aberto (``holdout.jsonl`` não é lido aqui)."""
    paths = _paths(args)
    try:
        cases = _dev_cases(paths)
    except (OSError, ValueError) as exc:
        print(f"cannot read dev cases: {exc}", file=sys.stderr)
        return EXIT_USAGE
    shown = 0
    for case in cases:
        if args.role and case.get("role") != args.role:
            continue
        if args.rule and case.get("rule") != args.rule:
            continue
        shown += 1
        print(f"{case['id']}  [{case['role']}]  rule={case.get('rule') or '-'}  tags={','.join(case.get('tags', []))}")
        print(f"    expected: {json.dumps(case['expected'], ensure_ascii=False)}")
        print(f"    notes: {case.get('notes', '')}")
        if args.requests:
            request = core.rebuild_request(case)
            print("    state: " + request["state"].replace("\n", "\n           "))
        if args.limit and shown >= args.limit:
            break
    print(f"({shown} of {len(cases)} dev cases; the hold-out is not shown while tuning)")
    return EXIT_OK


def cmd_show(args: argparse.Namespace) -> int:
    paths = _paths(args)
    try:
        cases = {c["id"]: c for c in _dev_cases(paths)}
        if args.id not in cases and args.allow_holdout:
            cases.update({c["id"]: c for c in read_cases(paths.holdout)})
    except (OSError, ValueError) as exc:
        print(f"cannot read cases: {exc}", file=sys.stderr)
        return EXIT_USAGE
    case = cases.get(args.id)
    if case is None:
        print(f"no dev case {args.id!r} (hold-out cases need --allow-holdout)", file=sys.stderr)
        return EXIT_USAGE
    request = core.rebuild_request(case)
    print(json.dumps({"case": case, "request": core.wire_request(request),
                      "request_sha256": core.request_hash(request)}, ensure_ascii=False, indent=1))
    return EXIT_OK


def export_cases(cases: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Casos no formato de ``evals/*.json`` (``name``/``state``/``questions``/``expected``)."""
    rows = []
    for case in cases:
        request = core.rebuild_request(case)
        wire = core.wire_request(request)
        rows.append({
            "name": case["id"], "role": case["role"], "state": wire["state"],
            "questions": wire["questions"], "expected": dict(case["expected"]),
            "bands": dict(case.get("bands") or {}), "notes": case.get("notes", ""),
        })
    return rows


def cmd_export(args: argparse.Namespace) -> int:
    paths = _paths(args)
    if args.split != "dev" and not args.allow_holdout:
        print("exporting the hold-out needs --allow-holdout (it defeats tuning discipline)",
              file=sys.stderr)
        return EXIT_USAGE
    try:
        cases = _dev_cases(paths) if args.split == "dev" else (
            read_cases(paths.holdout) if args.split == "holdout"
            else _dev_cases(paths) + read_cases(paths.holdout))
    except (OSError, ValueError) as exc:
        print(f"cannot read cases: {exc}", file=sys.stderr)
        return EXIT_USAGE
    rows = export_cases(sorted(cases, key=lambda c: str(c["id"])))
    write_text(Path(args.out), json.dumps(rows, ensure_ascii=False, indent=1) + "\n")
    print(f"exported {len(rows)} cases to {args.out}")
    return EXIT_OK


# --------------------------------------------------------------------- run


def make_client(model: str, key: str) -> Any:
    """Cliente Jev real (os testes substituem esta função por um fake)."""
    from system_one_dwarf.jev.client import JevClient  # noqa: PLC0415

    return JevClient(key, model=model)


def cmd_run(args: argparse.Namespace) -> int:
    if not args.online:
        print("run spends tokens and needs the real model: pass --online explicitly "
              "(offline verification is `check`)", file=sys.stderr)
        return EXIT_USAGE
    key = os.environ.get(args.key_env, "")
    if not key.strip():
        print(f"environment variable {args.key_env} is not set (the key is never printed)",
              file=sys.stderr)
        return EXIT_USAGE
    paths = _paths(args)
    try:
        dev = _dev_cases(paths) if args.split in ("dev", "all") else []
        hold = read_cases(paths.holdout) if args.split in ("holdout", "all") else []
    except (OSError, ValueError) as exc:
        print(f"cannot read cases: {exc}", file=sys.stderr)
        return EXIT_USAGE
    cases = sorted(dev + hold, key=lambda c: str(c["id"]))
    if args.max_cases:
        cases = cases[: args.max_cases]
    client = make_client(args.model, key)
    try:
        outcome = evaluate(cases, client, concurrency=args.concurrency)
    finally:
        closer = getattr(client, "close", None)
        if callable(closer):
            closer()
    held_ids = {str(c["id"]) for c in hold}
    visible_rows = [r for r in outcome["rows"] if r["id"] not in held_ids]
    state, baseline_roles = load_baseline(Path(args.baseline), args.split)
    regressions = compare_baseline(outcome["roles"], baseline_roles, tolerance=args.tolerance)
    report = {
        "format": 1, "model": args.model, "resolved_model": outcome["resolved_model"],
        "split": args.split, "date": args.date or today(), "cases": outcome["cases"],
        "answers": sum(1 for r in outcome["rows"] if not r.get("skipped")),
        "skipped": outcome["skipped"], "errors": outcome["errors"],
        "roles": outcome["roles"], "questions": outcome["questions"],
        "baseline": {"status": state, "path": str(args.baseline), "regressions": regressions,
                     "tolerance": args.tolerance},
        # hold-out: nunca linhas por caso (ver quais falham convida ao overfit)
        "rows": visible_rows,
    }
    if args.report:
        write_text(Path(args.report), json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    for role, metrics in outcome["roles"].items():
        print(f"{role}: n={metrics['n']} accuracy={metrics['accuracy']} ece={metrics['ece']} "
              f"({metrics['ece_label']})")
    for item in regressions:
        print(f"REGRESSION {item['role']} {item['metric']}: {item['baseline']} -> {item['current']}")
    if state != "set":
        print(f"baseline {state}: no comparison (use --update-baseline once the run is accepted)")
    if args.update_baseline:
        golden_run.update_baseline(Path(args.baseline), args.split, report)
        print(f"baseline updated for split {args.split!r}")
    if outcome["cases"] and len(outcome["errors"]) / outcome["cases"] > MAX_ERROR_FRACTION:
        print(f"INVALID RUN: {len(outcome['errors'])} of {outcome['cases']} cases failed to answer")
        return EXIT_INVALID
    return EXIT_FAIL if regressions else EXIT_OK


# --------------------------------------------------------------------- CLI


def _paths(args: argparse.Namespace) -> Paths:
    return Paths(golden=Path(args.dir) if args.dir else GOLDEN_DIR,
                 evals=Path(args.evals) if args.evals else EVALS_DIR)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="golden.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--dir", help="golden directory (default: evals/golden)")
        p.add_argument("--evals", help="evals directory with the seed files (default: evals)")
        p.add_argument("--date", help="override the date written to the changelog (YYYY-MM-DD)")

    p = sub.add_parser("check", help="offline verification (CI)")
    common(p)
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--strict", action="store_true", help="warnings fail too")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("bless", help="accept an intentional prompt change (re-hash)")
    common(p)
    p.add_argument("--reason", required=False, help="why the prompt changed (written to the changelog)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_bless)

    p = sub.add_parser("build", help="regenerate dev.jsonl from the sources")
    common(p)
    p.add_argument("--reimport-seeds", action="store_true",
                   help="redo the seed import (shuffle-seed search) instead of reusing the stored one")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("freeze-holdout", help="regenerate and pin the hold-out (explicit decision)")
    common(p)
    p.add_argument("--reason", required=False)
    p.add_argument("--reimport-seeds", action="store_true")
    p.set_defaults(func=cmd_freeze)

    p = sub.add_parser("run", help="evaluate against the real model (needs --online and a key)")
    common(p)
    p.add_argument("--online", action="store_true", help="explicit consent to spend tokens")
    p.add_argument("--split", choices=("dev", "holdout", "all"), default="dev")
    p.add_argument("--report", help="write the JSON report here")
    p.add_argument("--baseline", default=str(GOLDEN_DIR / BASELINE_FILE))
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--key-env", default=DEFAULT_KEY_ENV)
    p.add_argument("--tolerance", type=float, default=ACCURACY_TOLERANCE)
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    p.add_argument("--max-cases", type=int, default=0)
    p.add_argument("--update-baseline", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("tune-view", help="print dev cases only (hold-out stays invisible)")
    common(p)
    p.add_argument("--role", choices=core.ROLES)
    p.add_argument("--rule")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--requests", action="store_true", help="also print the rebuilt state")
    p.set_defaults(func=cmd_tune_view)

    p = sub.add_parser("show", help="one case and its rebuilt request")
    common(p)
    p.add_argument("id")
    p.add_argument("--allow-holdout", action="store_true")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("export", help="cases in the evals/*.json format")
    common(p)
    p.add_argument("--out", required=True)
    p.add_argument("--split", choices=("dev", "holdout", "all"), default="dev")
    p.add_argument("--allow-holdout", action="store_true")
    p.set_defaults(func=cmd_export)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse usa 2 para erros de uso — o nosso EXIT_USAGE
        return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE
    try:
        return int(args.func(args))
    except BrokenPipeError:  # pragma: no cover
        return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
