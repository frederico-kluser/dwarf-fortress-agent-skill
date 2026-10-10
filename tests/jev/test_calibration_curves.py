"""Métricas de calibração, seleção de limiar, tabela por snapshot e ``scripts/calibrate.py``.

Os números das métricas são calculados **à mão** (cada teste mostra a conta), a
seleção de limiar cobre o ramo de suporte insuficiente e a tabela cobre
ida-e-volta, chave de modelo ausente e snapshot resolvido diferente.
"""
from __future__ import annotations

import importlib.util
import json
import math
import pathlib
import sys

import pytest

from system_one_dwarf.jev import calibration as cal
from system_one_dwarf.jev import risk
from system_one_dwarf.jev.calibration import Sample

ROOT = pathlib.Path(__file__).resolve().parents[2]


def load_cli():
    spec = importlib.util.spec_from_file_location("calibrate_cli", ROOT / "scripts" / "calibrate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------- amostras


def test_sample_coercion_drops_invalid_input():
    items = [
        (0.9, True), [0.5, False], {"confidence": 0.7, "correct": 1}, Sample(0.3, True),
        (1.2, True), (-0.1, True), (float("nan"), True), ("x", True), (0.5, "yes"), (True, True),
        None, "junk", (0.5,),
    ]
    kept = cal.coerce_samples(items)
    assert [(s.confidence, s.correct) for s in kept] == [(0.9, True), (0.5, False), (0.7, True), (0.3, True)]
    assert cal.coerce_samples(None) == []


def test_score_answer_follows_the_jev_eval_semantics():
    # noul: previsto p>=0.5, confiança max(p, 1-p)
    assert cal.score_answer("noul", True, 0.9) == (True, 0.9, True)
    assert cal.score_answer("noul", False, 0.2) == (True, 0.8, True)
    assert cal.score_answer("noul", True, 0.5) == (True, 0.5, True)
    correct, confidence, _ = cal.score_answer("noul", False, 0.7)
    assert correct is False and confidence == pytest.approx(0.7)
    # choice: igualdade de chave; confidence ausente/0 => 0.5 indisponível
    assert cal.score_answer("choice", "a", "a", 0.8) == (True, 0.8, True)
    assert cal.score_answer("choice", "a", "b", 0.8) == (False, 0.8, True)
    assert cal.score_answer("choice", "a", "a", None) == (True, 0.5, False)
    assert cal.score_answer("choice", "a", "a", 0.0) == (True, 0.5, False)
    # score: |previsto - esperado| <= tolerância (0.5 por omissão)
    assert cal.score_answer("score", 1, 1.4, 0.9)[0] is True
    assert cal.score_answer("score", 1, 1.6, 0.9)[0] is False
    assert cal.score_answer("score", 1, 2.0, 0.9, tolerance=1.0)[0] is True
    # sem valor utilizável: não conta
    for args in (("noul", True, None), ("noul", True, "x"), ("noul", "yes", 0.9), ("choice", "a", None),
                 ("choice", "a", 3), ("score", 1, None), ("weird", 1, 1)):
        assert cal.score_answer(*args) is None


# ----------------------------------------------------------- ECE / reliability

HAND = [(0.95, True), (0.95, True), (0.95, False), (0.55, True), (0.55, False), (0.15, False)]


def test_reliability_bins_by_hand():
    bins = cal.reliability_bins(HAND)
    assert [(b.lower, b.upper, b.count) for b in bins] == [(0.1, 0.2, 1), (0.5, 0.6, 2), (0.9, 1.0, 3)]
    assert [b.accuracy for b in bins] == [0.0, 0.5, pytest.approx(2 / 3)]
    assert [b.mean_confidence for b in bins] == [pytest.approx(0.15), pytest.approx(0.55), pytest.approx(0.95)]
    assert [round(b.gap, 4) for b in bins] == [0.15, 0.05, 0.2833]


def test_ece_by_hand():
    # (1/6)*0.15 + (2/6)*0.05 + (3/6)*(0.95 - 2/3) = 0.025 + 0.016667 + 0.141667
    assert cal.ece(HAND) == pytest.approx(0.183333, abs=1e-6)
    assert cal.ece([(0.9, True), (0.9, False)]) == pytest.approx(0.4)        # |0.5 - 0.9|
    assert cal.ece([(0.8, True)] * 8 + [(0.8, False)] * 2) == pytest.approx(0.0)  # perfeitamente calibrado
    assert cal.ece([]) is None


def test_confidence_one_falls_in_the_last_bin_like_jev_mjs():
    bins = cal.reliability_bins([(1.0, True), (0.0, False)])
    assert [(b.lower, b.upper) for b in bins] == [(0.0, 0.1), (0.9, 1.0)]


def test_ece_label_thresholds():
    assert cal.ece_label(0.05) == "good"
    assert cal.ece_label(0.051) == "acceptable"
    assert cal.ece_label(0.10) == "acceptable"
    assert cal.ece_label(0.1001) == "bad"
    assert cal.ece_label(None) is None


def test_brier_accuracy_and_mean_confidence_by_hand():
    # (0.05² + 0.05² + 0.95² + 0.45² + 0.55² + 0.15²) / 6
    assert cal.brier(HAND) == pytest.approx(0.239167, abs=1e-6)
    assert cal.accuracy(HAND) == pytest.approx(0.5)
    assert cal.mean_confidence(HAND) == pytest.approx(4.1 / 6)  # (3×0.95 + 2×0.55 + 0.15) / 6
    assert cal.brier([]) is None and cal.accuracy([]) is None


# ------------------------------------------------------- risco-cobertura


def test_risk_coverage_curve_groups_ties_and_is_monotonic():
    curve = cal.risk_coverage_curve(HAND)
    assert [(p.threshold, p.accepted, p.errors) for p in curve] == [(0.95, 3, 1), (0.55, 5, 2), (0.15, 6, 3)]
    assert [p.coverage for p in curve] == [pytest.approx(0.5), pytest.approx(5 / 6), 1.0]
    assert [round(p.risk, 4) for p in curve] == [0.3333, 0.4, 0.5]
    assert curve[-1].coverage == 1.0
    assert all(a.coverage < b.coverage for a, b in zip(curve, curve[1:]))
    assert cal.risk_coverage_curve([]) == []


def test_aurc_by_hand():
    curve = cal.risk_coverage_curve([(0.9, True), (0.8, True), (0.7, False), (0.6, True)])
    # pontos (cov, risk): (.25, 0) (.5, 0) (.75, 1/3) (1.0, 1/4)
    expected = 0.25 * 0 + 0.25 * 0 + 0.25 * (0 + 1 / 3) / 2 + 0.25 * (1 / 3 + 0.25) / 2
    assert cal.aurc(curve) == pytest.approx(expected)
    assert cal.aurc([]) is None


# ------------------------------------------------------------------- limites


def test_wilson_upper_known_values():
    assert cal.wilson_upper(5, 100) == pytest.approx(0.11175, abs=1e-4)
    assert cal.wilson_upper(0, 30) == pytest.approx(0.1135, abs=1e-3)
    assert cal.wilson_upper(0, 0) == 1.0
    assert cal.wilson_upper(10, 10) == 1.0
    assert cal.wilson_upper(1, 100) < cal.wilson_upper(5, 100) < cal.wilson_upper(20, 100)
    assert cal.wilson_upper(5, 1000) < cal.wilson_upper(5, 100)  # mais dados, limite mais apertado


def test_bounds_order():
    errors, n = 4, 80
    point = cal.risk_upper_bound(errors, n, "point")
    wilson = cal.risk_upper_bound(errors, n, "wilson")
    hoeffding = cal.risk_upper_bound(errors, n, "hoeffding")
    assert point == pytest.approx(0.05)
    assert point < wilson < hoeffding
    assert cal.hoeffding_upper(0, 0) == 1.0 and cal.risk_upper_bound(0, 0, "point") == 1.0


# -------------------------------------------------------------- seleção


def block(conf, right, wrong):
    return [(conf, True)] * right + [(conf, False)] * wrong


def test_select_threshold_picks_the_smallest_passing_grid_point():
    samples = block(0.97, 60, 0) + block(0.90, 30, 5) + block(0.70, 10, 10)
    choice = cal.select_threshold(samples, 0.10)
    # 0.90: 95 aceites, 5 erros, Wilson ≈ 0.115 > 0.10  → falha;  0.91: 60 aceites, 0 erros, ≈ 0.060 → passa
    assert choice.reason == "ok"
    assert choice.threshold == pytest.approx(0.91)
    assert (choice.accepted, choice.errors, choice.n) == (60, 0, 115)
    assert choice.upper_bound == pytest.approx(0.0602, abs=1e-3)
    assert choice.coverage == pytest.approx(60 / 115)
    assert cal.threshold_for_target_risk(samples, 0.10) == pytest.approx(0.91)


def test_select_threshold_exact_values_without_grid():
    samples = block(0.97, 60, 0) + block(0.90, 30, 5)
    choice = cal.select_threshold(samples, 0.10, grid=None)
    assert choice.threshold == pytest.approx(0.97)  # menor confiança distinta que passa


def test_low_support_falls_back_to_none():
    few = block(0.99, 29, 0)
    choice = cal.select_threshold(few, 0.10, min_support=30)
    assert choice.threshold is None and choice.reason == "insufficient_support" and choice.n == 29
    assert cal.threshold_for_target_risk(few, 0.10) is None
    assert cal.select_threshold([], 0.1).reason == "no_samples"
    # 30 amostras: já há suporte, mas com risco-alvo de 5 % o Wilson (≈ 11 %) não prova nada
    thirty = block(0.99, 30, 0)
    assert cal.select_threshold(thirty, 0.05).reason == "unattainable"
    assert cal.select_threshold(thirty, 0.12).threshold == pytest.approx(0.50)


def test_min_support_limits_how_high_the_threshold_can_go():
    samples = block(0.99, 20, 0) + block(0.80, 40, 0)
    choice = cal.select_threshold(samples, 0.20, min_support=30)
    # só acima de 0.80 o conjunto aceite cai abaixo de 30; 0.50..0.80 aceitam 60
    assert choice.threshold == pytest.approx(0.50)
    assert choice.accepted == 60


def test_unattainable_when_errors_dominate():
    noisy = block(0.9, 20, 20) + block(0.6, 20, 20)
    choice = cal.select_threshold(noisy, 0.05)
    assert choice.threshold is None and choice.reason == "unattainable"


def test_bound_choice_changes_the_answer():
    samples = block(0.95, 70, 3)
    point = cal.select_threshold(samples, 0.05, bound="point")
    wilson = cal.select_threshold(samples, 0.05, bound="wilson")
    hoeffding = cal.select_threshold(samples, 0.05, bound="hoeffding")
    assert point.threshold is not None            # 3/73 ≈ 4.1 % ≤ 5 %
    assert wilson.threshold is None               # Wilson ≈ 11 %
    assert hoeffding.threshold is None


def test_threshold_candidates_never_go_below_the_hitl_floor():
    samples = block(0.30, 100, 0)
    assert cal.select_threshold(samples, 0.1).threshold is None   # nada ≥ 0.50
    # com o piso em 0.2 o menor candidato que aceita as 100 amostras é o próprio piso
    assert cal.select_threshold(samples, 0.1, min_threshold=0.2).threshold == pytest.approx(0.20)


# ---------------------------------------------------------------- relatórios


def labeled(conf, correct, klass=None, role="JEV-A", question="action", qtype="choice"):
    return Sample(conf, correct, role=role, qtype=qtype, question=question, risk_class=klass)


def test_build_report_groups_by_role_question_and_class():
    samples = (
        [labeled(0.97, True, "reversible") for _ in range(60)]
        + [labeled(0.6, False, "irreversible") for _ in range(35)]
        + [labeled(0.9, True, role="JEV-D", question="ask_human", qtype="noul") for _ in range(4)]
    )
    report = cal.build_report(samples, model="typesafe/jev-1.13", report_date="2026-10-10",
                              eval_fingerprint="sha256:abc")
    assert report["overall"]["n"] == 99
    assert set(report["by_role"]) == {"JEV-A", "JEV-D"}
    assert set(report["by_question"]) == {"JEV-A/action", "JEV-D/ask_human"}
    assert set(report["by_class"]) == set(risk.RISK_CLASSES)
    reversible = report["by_class"]["reversible"]
    assert reversible["n"] == 60 and reversible["target_risk"] == pytest.approx(0.10)
    assert reversible["selection"]["reason"] == "ok"
    assert reversible["selection"]["threshold"] == pytest.approx(0.50)  # 60 acertos aos 0.97: passa já em 0.50
    irreversible = report["by_class"]["irreversible"]
    assert irreversible["selection"]["threshold"] is None and irreversible["accuracy"] == 0.0
    assert report["by_class"]["destructive"]["selection"]["reason"] == "no_samples"
    assert report["model"] == "typesafe/jev-1.13" and report["eval_fingerprint"] == "sha256:abc"
    assert json.dumps(report)  # serializável


def test_build_report_targets_override_and_empty_input():
    report = cal.build_report([labeled(0.9, True, "reversible") for _ in range(40)], targets={"reversible": 0.5})
    assert report["by_class"]["reversible"]["target_risk"] == 0.5
    empty = cal.build_report([])
    assert empty["overall"]["n"] == 0 and empty["overall"]["ece"] is None
    assert all(row["selection"]["threshold"] is None for row in empty["by_class"].values())


# -------------------------------------------------------- fingerprint do eval


def test_fingerprint_is_order_independent_and_ignores_notes():
    a = {"name": "a", "state": "s", "questions": {"q": {"type": "noul"}}, "expected": {"q": True}, "notes": "x"}
    b = {"name": "b", "state": "t", "questions": {}, "expected": {}, "notes": "y"}
    base = cal.fingerprint_cases([a, b])
    assert base.startswith("sha256:") and len(base) == 7 + 64
    assert cal.fingerprint_cases([b, a]) == base
    assert cal.fingerprint_cases([{**a, "notes": "changed"}, b]) == base
    assert cal.fingerprint_cases([{**a, "expected": {"q": False}}, b]) != base
    assert cal.fingerprint_cases([{**a, "state": "s2"}, b]) != base


# -------------------------------------------------------------------- tabela


def make_entry(model="typesafe/jev-1.13", **overrides):
    samples = [labeled(0.97, True, "reversible") for _ in range(120)]
    report = cal.build_report(samples, model=model, report_date="2026-10-10",
                              resolved_model=overrides.pop("resolved_model", None),
                              source=overrides.pop("source", "eval"))
    return cal.ModelCalibration.from_report(report, **overrides)


def test_table_round_trip_is_lossless_and_deterministic(tmp_path):
    table = cal.CalibrationTable()
    table.put(make_entry())
    table.put(make_entry("typesafe/jev-1.14", resolved_model="typesafe/jev-1.14-20261001"))
    path = table.save(tmp_path / "calibration.json")
    first = path.read_bytes()
    loaded = cal.CalibrationTable.load(path)
    assert loaded.to_dict() == table.to_dict()
    assert loaded.issues == []
    loaded.save(path)
    assert path.read_bytes() == first          # determinístico
    doc = json.loads(first)
    assert doc["format"] == 1 and list(doc["models"]) == ["typesafe/jev-1.13", "typesafe/jev-1.14"]
    entry = doc["models"]["typesafe/jev-1.13"]
    assert entry["report_date"] == "2026-10-10" and entry["samples"]["total"] == 120
    assert entry["classes"]["reversible"]["support"] == 120
    assert not list(tmp_path.glob("*.tmp"))    # escrita atómica não deixa lixo


def test_lookup_matches_misses_and_detects_stale_snapshots(tmp_path):
    table = cal.CalibrationTable()
    table.put(make_entry(resolved_model="typesafe/jev-1.13-20260917"))
    hit = table.lookup("typesafe/jev-1.13", "typesafe/jev-1.13-20260917")
    assert hit.matched and not hit.calibration.is_default
    assert table.lookup("typesafe/jev-1.13").matched          # resolvido desconhecido: não invalida
    miss = table.lookup("typesafe/jev-1.14")
    assert miss.status == "missing" and miss.calibration.is_default
    assert miss.calibration.classes == {} and miss.calibration.model == "typesafe/jev-1.14"
    stale = table.lookup("typesafe/jev-1.13", "typesafe/jev-1.13-20261101")
    assert stale.status == "stale_resolved" and stale.calibration.is_default
    # chave exata: um alias nunca herda a calibração do snapshot fixo
    assert table.lookup("~typesafe/jev-latest").status == "missing"
    assert table.lookup(" typesafe/jev-1.13 ").matched          # só espaços são ignorados
    assert cal.CalibrationTable().lookup("anything").status == "empty"


def test_model_key_miss_yields_the_policy_floors(tmp_path):
    table = cal.CalibrationTable()
    table.put(make_entry())
    path = table.save(tmp_path / "t.json")
    calibration = cal.load_calibration(path, "typesafe/jev-9.99")
    assert calibration.is_default
    assert risk.class_thresholds(calibration) == {c: risk.POLICY_FLOORS[c] for c in risk.RISK_CLASSES}


def test_load_calibration_never_raises(tmp_path):
    for target in (None, tmp_path / "missing.json", tmp_path):
        assert cal.load_calibration(target, "m").is_default
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert cal.load_calibration(bad, "m").is_default
    assert cal.CalibrationTable.load(bad).issues
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"format": 99, "models": {}}), encoding="utf-8")
    table = cal.CalibrationTable.load(wrong)
    assert table.models == {} and "unsupported" in table.issues[0]
    weird = tmp_path / "weird.json"
    weird.write_text(json.dumps({"format": 1, "models": {"m": {"classes": {"reversible": "x", "bogus": {}}}}}),
                     encoding="utf-8")
    assert cal.CalibrationTable.load(weird).models["m"].classes["reversible"].threshold is None


def test_fake_model_entries_are_ignored_unless_allowed():
    table = cal.CalibrationTable()
    table.put(make_entry("fake/jev-fake", source=cal.SOURCE_FAKE))
    assert table.lookup("fake/jev-fake").status == "fake_ignored"
    assert table.lookup("fake/jev-fake", allow_fake=True).matched


def test_table_entry_feeds_risk_thresholds_only_upwards():
    # classe reversible com 120 acertos a 0.97 recomenda 0.50 — abaixo do piso: ignorado
    entry = make_entry()
    assert entry.entry_for("reversible").threshold == pytest.approx(0.50)
    assert risk.thresholds_for("reversible", entry) == pytest.approx(0.90)
    # a mesma entrada verificada (suporte 120 ≥ 100) baixa, mas nunca abaixo de 0.80
    verified = make_entry(verified=["reversible"])
    assert verified.entry_for("reversible").verified is True
    assert risk.thresholds_for("reversible", verified) == pytest.approx(risk.LOWEST_AUTO_THRESHOLD)
    # uma entrada que recomenda subir é sempre aplicada
    noisy = cal.build_report(
        [labeled(0.99, True, "irreversible")] * 60 + [labeled(0.97, False, "irreversible")] * 12
        + [labeled(0.99, True, "irreversible")] * 40, model="m", report_date="2026-10-10")
    raised = cal.ModelCalibration.from_report(noisy)
    threshold = raised.entry_for("irreversible").threshold
    assert threshold is not None and threshold > 0.97
    assert risk.thresholds_for("irreversible", raised) == pytest.approx(threshold)


# ------------------------------------------------- amostras reais (decisões)


def decision(id_, action, confidence, *, ts, status=None, issues=None, agent="JEV-A"):
    row = {"id": id_, "ts": ts, "agent": agent, "answers": {"action": action},
           "confidence": confidence, "band": "auto"}
    if status:
        row["status"] = status
    if issues:
        row["issues"] = issues
    return row


def approval(action, status, *, ts, decided_at=None):
    row = {"id": f"q-{action}-{ts}", "action_id": action, "status": status, "ts": ts}
    if decided_at:
        row["decided_at"] = decided_at
    return row


def test_label_decisions_proxy_rules():
    decisions = [
        decision("d1", "explore:north", 0.95, ts="2026-10-10T10:00:00Z", status="executed"),     # executed_ok
        decision("d2", "attack:stray_mule", 0.80, ts="2026-10-10T10:01:00Z", status="question"),  # approved
        decision("d3", "flee:s", 0.70, ts="2026-10-10T10:02:00Z", status="question"),            # rejected
        decision("d4", "eat:bread", 0.92, ts="2026-10-10T10:03:00Z", status="executed",
                 issues=["guard:threat_appeared"]),                                               # aborted
        decision("d5", "wait:one_turn", None, ts="2026-10-10T10:04:00Z", status="executed"),     # sem confiança
        decision("d6", "talk:hunter", 0.60, ts="2026-10-10T10:05:00Z", status="question"),       # sem desfecho
        decision("d7", "explore:east", 0.9, ts="2026-10-10T10:06:00Z", status="error"),          # erro
        decision("d8", "x", 0.9, ts="2026-10-10T10:07:00Z", status="executed", agent="JEV-D"),   # outro papel
    ]
    approvals = [
        approval("attack:stray_mule", "approved", ts="2026-10-10T10:01:01Z", decided_at="2026-10-10T10:03:00Z"),
        approval("flee:s", "rejected", ts="2026-10-10T10:02:01Z", decided_at="2026-10-10T10:02:30Z"),
        approval("talk:hunter", "pending", ts="2026-10-10T10:05:01Z"),
    ]
    result = cal.label_decisions(decisions, approvals)
    by_case = {s.case: s for s in result.samples}
    assert {k: v.correct for k, v in by_case.items()} == {"d1": True, "d2": True, "d3": False, "d4": False}
    assert by_case["d1"].risk_class == "reversible" and by_case["d2"].risk_class == "irreversible"
    assert by_case["d3"].risk_class == "costly" and by_case["d3"].confidence == 0.70
    assert result.labels == {"aborted": 1, "approved": 1, "executed_ok": 1, "rejected": 1}
    assert result.skipped["confidence_unavailable"] == 1
    assert result.skipped["role_not_selected"] == 1
    assert result.skipped["status_error"] == 1
    assert result.skipped["unresolved"] == 1
    assert result.skipped["approval_undecided"] == 1
    assert cal.samples_from_decisions(decisions, approvals) == result.samples


def test_approval_consumes_one_decision_not_the_whole_flood():
    flood = [decision(f"d{i}", "attack:deer", 0.7, ts=f"2026-10-10T10:00:{i:02d}Z", status="question")
             for i in range(20)]
    released = decision("dr", "attack:deer", 0.7, ts="2026-10-10T10:05:00Z", status="executed",
                        issues=["released_by_approval"])
    approvals = [approval("attack:deer", "approved", ts="2026-10-10T10:00:00Z",
                          decided_at="2026-10-10T10:04:30Z")]
    result = cal.label_decisions(flood + [released], approvals)
    assert [s.case for s in result.samples] == ["dr"]                     # uma amostra por aprovação
    assert result.labels == {"released_by_approval": 1}
    assert result.skipped["unresolved"] == 20


def test_label_decisions_tolerates_garbage():
    result = cal.label_decisions([None, 3, {"agent": "JEV-A"}, {"agent": "JEV-A", "confidence": 2}],
                                 [None, {"status": "approved"}, "x"])
    assert result.samples == []
    assert cal.label_decisions(None, None).samples == []


# ------------------------------------------------------------------- CLI


@pytest.fixture
def cli():
    return load_cli()


def write_cases(path, names=("c1", "c2"), extra=None):
    cases = []
    for index, name in enumerate(names):
        cases.append({
            "name": name, "role": "JEV-A", "state": f"state {index}",
            "questions": {
                "action": {"type": "choice", "instructions": "Which action?",
                           "criteria": {"attack:wolf": "a", "flee:s": "b", "other": "c"}},
                "needs_human": {"type": "noul", "instructions": "Help?",
                                "criteria": {"true": "t", "false": "f"}},
            },
            "expected": {"action": "attack:wolf" if index % 2 == 0 else "flee:s", "needs_human": False},
        })
    cases.extend(extra or [])
    path.write_text(json.dumps(cases), encoding="utf-8")
    return cases


def test_cli_report_with_fake_model_writes_report_and_table(cli, tmp_path, capsys):
    evals = tmp_path / "evals"
    evals.mkdir()
    write_cases(evals / "a.json", names=tuple(f"c{i}" for i in range(40)))
    out, table = tmp_path / "report.json", tmp_path / "calibration.json"
    code = cli.main(["report", "--evals", str(evals), "--fake-model", "--out", str(out),
                     "--table", str(table), "--date", "2026-10-10"])
    assert code == 0
    report = json.loads(out.read_text())
    assert report["source"] == "fake-model" and report["model"] == "fake/jev-fake"
    assert report["overall"]["n"] == 80 and report["report_date"] == "2026-10-10"
    assert report["eval_fingerprint"].startswith("sha256:")
    stored = json.loads(table.read_text())
    assert list(stored["models"]) == ["fake/jev-fake"]
    assert stored["models"]["fake/jev-fake"]["source"] == "fake-model"
    # determinístico: a mesma corrida dá os mesmos bytes
    again = tmp_path / "report2.json"
    cli.main(["report", "--evals", str(evals), "--fake-model", "--out", str(again), "--date", "2026-10-10"])
    assert again.read_bytes() == out.read_bytes()
    assert "ece" in capsys.readouterr().out


def test_cli_report_from_recorded_results_accepts_jev_mjs_rows(cli, tmp_path):
    evals = tmp_path / "evals"
    evals.mkdir()
    cases = write_cases(evals / "a.json", names=tuple(f"c{i}" for i in range(6)))
    rows = []
    for index, case in enumerate(cases):
        rows.append({"name": case["name"], "qid": "action", "type": "choice", "expected": case["expected"]["action"],
                     "predicted": case["expected"]["action"] if index else "other",
                     "correct": bool(index), "confidence": 0.9})
        rows.append({"name": case["name"], "qid": "needs_human", "type": "noul", "expected": False,
                     "predicted": False, "correct": True, "confidence": 0.8})
    results = tmp_path / "rows.json"
    results.write_text(json.dumps({"rows": rows, "model": "typesafe/jev-1.13"}), encoding="utf-8")
    out = tmp_path / "r.json"
    assert cli.main(["report", "--evals", str(evals), "--results", str(results), "--out", str(out),
                     "--date", "2026-10-10"]) == 0
    report = json.loads(out.read_text())
    assert report["model"] == "typesafe/jev-1.13" and report["source"] == "eval"
    # classe da ação PREVISTA: attack:wolf (c2, c4) / flee:s (c1, c3, c5) / other (c0)
    assert report["overall"]["n"] == 12
    assert [report["by_class"][c]["n"] for c in ("reversible", "costly", "irreversible", "destructive")] == [1, 3, 2, 0]
    assert report["by_role"]["JEV-A"]["n"] == 12                                          # papel vem do caso


def test_cli_report_usage_and_data_errors(cli, tmp_path, capsys):
    evals = tmp_path / "evals"
    evals.mkdir()
    write_cases(evals / "a.json")
    out = str(tmp_path / "o.json")
    assert cli.main(["report", "--evals", str(evals), "--out", out]) == 2                 # nem results nem fake
    assert cli.main(["report", "--evals", str(evals), "--out", out, "--fake-model",
                     "--results", "x.json"]) == 2                                          # ambos
    assert cli.main(["report", "--evals", str(tmp_path / "nope"), "--fake-model", "--out", out]) == 3
    assert cli.main(["report", "--evals", str(evals), "--results", str(tmp_path / "missing.json"),
                     "--out", out]) == 3
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cli.main(["report", "--evals", str(empty), "--fake-model", "--out", out]) == 3
    rows = tmp_path / "rows.json"
    rows.write_text(json.dumps({"rows": []}), encoding="utf-8")
    assert cli.main(["report", "--evals", str(evals), "--results", str(rows), "--out", out]) == 1
    assert cli.main(["report", "--evals", str(evals), "--fake-model", "--out", out,
                     "--table", str(tmp_path / "t.json"), "--mark-verified", "nonsense"]) == 2
    assert cli.main(["bogus"]) == 2
    assert cli.main([]) == 2
    assert cli.main(["--help"]) == 0
    capsys.readouterr()


def test_cli_rejects_duplicate_case_names(cli, tmp_path):
    evals = tmp_path / "evals"
    evals.mkdir()
    write_cases(evals / "a.json", names=("same",))
    write_cases(evals / "b.json", names=("same",))
    assert cli.main(["report", "--evals", str(evals), "--fake-model", "--out", str(tmp_path / "o.json")]) == 3


def test_cli_from_decisions(cli, tmp_path, capsys):
    decisions = [decision(f"d{i}", "explore:north", 0.95, ts=f"2026-10-10T10:00:{i:02d}Z", status="executed")
                 for i in range(5)]
    decisions.append(decision("dx", "attack:wolf", 0.8, ts="2026-10-10T10:01:00Z", status="question"))
    approvals = [approval("attack:wolf", "rejected", ts="2026-10-10T10:01:01Z", decided_at="2026-10-10T10:01:20Z")]
    dpath, apath = tmp_path / "d.json", tmp_path / "a.json"
    dpath.write_text(json.dumps(decisions), encoding="utf-8")
    apath.write_text(json.dumps({"approvals": approvals}), encoding="utf-8")
    out = tmp_path / "samples.json"
    table = tmp_path / "calibration.json"
    code = cli.main(["from-decisions", "--decisions", str(dpath), "--approvals", str(apath),
                     "--out", str(out), "--table", str(table), "--date", "2026-10-10"])
    assert code == 0
    report = json.loads(out.read_text())
    assert report["source"] == "decisions" and report["overall"]["n"] == 6
    assert report["labeling"]["labels"] == {"executed_ok": 5, "rejected": 1}
    assert len(report["samples"]) == 6
    assert json.loads(table.read_text())["models"]["typesafe/jev-1.13"]["source"] == "decisions"
    # as amostras guardadas voltam a entrar noutro relatório
    evals = tmp_path / "evals"
    evals.mkdir()
    write_cases(evals / "a.json")
    pooled = tmp_path / "pooled.json"
    assert cli.main(["report", "--evals", str(evals), "--fake-model", "--samples", str(out),
                     "--out", str(pooled)]) == 0
    assert json.loads(pooled.read_text())["overall"]["n"] == 4 + 6
    # entradas inválidas / vazias
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    assert cli.main(["from-decisions", "--decisions", str(bad), "--approvals", str(apath), "--out", str(out)]) == 3
    apath.write_text(json.dumps({"nope": 1}), encoding="utf-8")
    assert cli.main(["from-decisions", "--decisions", str(dpath), "--approvals", str(apath), "--out", str(out)]) == 3
    dpath.write_text("[]", encoding="utf-8")
    apath.write_text("[]", encoding="utf-8")
    assert cli.main(["from-decisions", "--decisions", str(dpath), "--approvals", str(apath), "--out", str(out)]) == 1
    capsys.readouterr()


def test_cli_thresholds_enforces_snapshot_miss(cli, tmp_path, capsys):
    table = cal.CalibrationTable()
    table.put(make_entry(resolved_model="typesafe/jev-1.13-20260917"))
    path = table.save(tmp_path / "t.json")
    assert cli.main(["thresholds", "--table", str(path), "--json"]) == 0
    hit = json.loads(capsys.readouterr().out)
    assert hit["status"] == "match" and hit["thresholds"]["reversible"] == 0.9
    assert cli.main(["thresholds", "--table", str(path), "--model", "typesafe/jev-2.0", "--json"]) == 0
    miss = json.loads(capsys.readouterr().out)
    assert miss["status"] == "missing"
    assert miss["thresholds"] == {c: risk.POLICY_FLOORS[c] for c in risk.RISK_CLASSES}
    assert cli.main(["thresholds", "--table", str(path), "--resolved-model", "typesafe/jev-1.13-20261101",
                     "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "stale_resolved"
    assert cli.main(["thresholds", "--table", str(path)]) == 0
    assert "destructive" in capsys.readouterr().out


class FakeAnswer:
    def __init__(self, value, confidence=None, band="auto"):
        self.value, self.confidence, self.band = value, confidence, band


class FakeResult:
    def __init__(self, answers, model="typesafe/jev-1.13-20260917"):
        self.answers, self.model = answers, model


class FakeClient:
    def __init__(self, responder):
        self.responder = responder
        self.requests = []
        self.closed = False

    def decide_request(self, request, session_id=None, **kwargs):
        self.requests.append(request)
        return self.responder(request)

    def close(self):
        self.closed = True


def test_cli_online_uses_an_injected_client_and_never_prints_the_key(cli, tmp_path, monkeypatch, capsys):
    evals = tmp_path / "evals"
    evals.mkdir()
    write_cases(evals / "a.json", names=("c0", "c1", "c2"))
    secret = "sk-or-v1-TEST-KEY-NOT-REAL-0000"

    def responder(request):
        if request["state"] == "state 1":
            raise RuntimeError("upstream exploded")
        right = "attack:wolf" if request["state"] in ("state 0", "state 2") else "flee:s"
        return FakeResult({"action": FakeAnswer(right, 0.9), "needs_human": FakeAnswer(0.1)})

    client = FakeClient(responder)
    seen = {}

    def fake_make_client(model, key):
        seen["model"], seen["key"] = model, key
        return client

    monkeypatch.setattr(cli, "make_client", fake_make_client)
    monkeypatch.setenv("SOD_TEST_KEY", secret)
    out = tmp_path / "results.json"
    code = cli.main(["online", "--model", "typesafe/jev-1.13", "--key-env", "SOD_TEST_KEY",
                     "--evals", str(evals), "--out", str(out), "--date", "2026-10-10"])
    assert code == 0
    assert seen == {"model": "typesafe/jev-1.13", "key": secret} and client.closed
    data = json.loads(out.read_text())
    assert data["resolved_model"] == "typesafe/jev-1.13-20260917" and data["cases"] == 3
    assert len(data["errors"]) == 1 and "upstream exploded" in data["errors"][0]
    assert {(r["name"], r["qid"]) for r in data["rows"]} == {
        ("c0", "action"), ("c0", "needs_human"), ("c2", "action"), ("c2", "needs_human")}
    assert all(r["correct"] for r in data["rows"])
    printed = capsys.readouterr()
    assert secret not in printed.out and secret not in printed.err and secret not in out.read_text()
    # a saída de `online` alimenta `report --results`
    report = tmp_path / "report.json"
    assert cli.main(["report", "--evals", str(evals), "--results", str(out), "--out", str(report)]) == 0
    assert json.loads(report.read_text())["resolved_model"] == "typesafe/jev-1.13-20260917"


def test_cli_online_needs_the_key_env(cli, tmp_path, monkeypatch, capsys):
    evals = tmp_path / "evals"
    evals.mkdir()
    write_cases(evals / "a.json")
    monkeypatch.delenv("SOD_MISSING_KEY", raising=False)
    assert cli.main(["online", "--key-env", "SOD_MISSING_KEY", "--evals", str(evals),
                     "--out", str(tmp_path / "o.json")]) == 2
    assert "SOD_MISSING_KEY" in capsys.readouterr().err
    assert not (tmp_path / "o.json").exists()


def test_fake_model_is_deterministic_and_scores_every_labeled_answer(cli):
    cases = [{"name": f"c{i}", "role": "JEV-A", "state": "s",
              "questions": {"u": {"type": "score", "criteria": ["a", "b", "c"]},
                            "n": {"type": "noul"}, "c": {"type": "choice", "criteria": {"x": "", "y": "", "other": ""}}},
              "expected": {"u": 1, "n": True, "c": "x"}} for i in range(50)]
    first = cli.fake_rows(cases, cli.FakeModel(seed=3))
    again = cli.fake_rows(cases, cli.FakeModel(seed=3))
    assert first == again and len(first) == 150
    other = cli.fake_rows(cases, cli.FakeModel(seed=4))
    assert first != other
    accuracy = sum(r["correct"] for r in first) / len(first)
    assert 0.7 < accuracy < 0.97
    assert all(0.0 <= r["confidence"] <= 1.0 for r in first)
    assert not any(math.isnan(r["confidence"]) for r in first)
