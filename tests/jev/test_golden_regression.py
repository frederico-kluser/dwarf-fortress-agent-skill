"""Golden set de regressão de prompts (mudança 12): ficheiros, partição, pin, regressão, CLI.

Os testes que mexem em ficheiros trabalham sobre **cópias** em ``tmp_path``; o
primeiro valida os ficheiros versionados de ``evals/golden`` tal como estão.
O detector de regressão é exercitado de verdade: um *builder* real é alterado
por ``monkeypatch`` e o ``check`` tem de falhar (e o ``bless`` de o aceitar).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import shutil
import sys
from dataclasses import dataclass

import pytest

from system_one_dwarf.agent import candidates as agent_candidates
from system_one_dwarf.agent import loop as agent_loop
from system_one_dwarf.jev import client as jev_client
from system_one_dwarf.jev import fleet, risk
from system_one_dwarf.jev.errors import JevTransportError

ROOT = pathlib.Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "evals" / "golden"
EVALS = ROOT / "evals"
GOLDEN_FILES = ("scenarios.json", "dev.jsonl", "holdout.jsonl", "HOLDOUT.sha256",
                "prompts.lock.json", "CHANGELOG.md", "baseline.json")


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cli = _load("golden_cli", "scripts/golden.py")
core, rules, sources = cli.core, cli.rules, cli.sources
calibrate_cli = _load("calibrate_cli_for_golden", "scripts/calibrate.py")


@dataclass
class Env:
    evals: pathlib.Path
    golden: pathlib.Path

    def args(self, *argv: str) -> list[str]:
        return [*argv, "--dir", str(self.golden), "--evals", str(self.evals), "--date", "2026-10-10"]

    def run(self, *argv: str) -> int:
        return cli.main(self.args(*argv))

    def check(self):
        return cli.run_check(cli.Paths(golden=self.golden, evals=self.evals))

    def codes(self) -> set[str]:
        return {p.code for p in self.check().errors}


@pytest.fixture
def env(tmp_path) -> Env:
    evals = tmp_path / "evals"
    golden = evals / "golden"
    golden.mkdir(parents=True)
    for name in core.SEED_FILES:
        shutil.copy(EVALS / name, evals / name)
    for name in GOLDEN_FILES:
        shutil.copy(GOLDEN / name, golden / name)
    return Env(evals, golden)


@pytest.fixture(scope="module")
def committed():
    """Os casos versionados (dev + hold-out) e o que o ``check`` diz deles."""
    cases = cli.read_cases(GOLDEN / "dev.jsonl") + cli.read_cases(GOLDEN / "holdout.jsonl")
    return cases


# --------------------------------------------------- ficheiros versionados


def test_committed_golden_set_passes_check(capsys):
    """O CI: ``check`` sobre os ficheiros reais. Falha quando um prompt muda sem ``bless``."""
    assert cli.main(["check"]) == 0
    assert "OK: all checks passed" in capsys.readouterr().out


def test_golden_set_shape(committed):
    seeds = [c for c in committed if c["source"] == "seed"]
    synthetic = [c for c in committed if c["source"] == "synthetic"]
    assert len(committed) >= 100
    assert len(seeds) == 66 and all("source=seed" in c["tags"] for c in seeds)
    assert len(synthetic) >= 40 and all("source=synthetic" in c["tags"] for c in synthetic)
    ids = [c["id"] for c in committed]
    assert len(ids) == len(set(ids))
    holdout = [c for c in committed if core.split_of(c) == "holdout"]
    assert 15 <= len(holdout) <= 25
    roles = {c["role"] for c in committed}
    assert roles == set(core.ROLES)
    for role in core.ROLES:  # cada papel tem dev e hold-out
        assert any(c["role"] == role and core.split_of(c) == "holdout" for c in committed), role
        assert any(c["role"] == role and core.split_of(c) == "dev" for c in committed), role


def test_committed_files_follow_the_split_rule(committed):
    dev = {c["id"] for c in cli.read_cases(GOLDEN / "dev.jsonl")}
    hold = {c["id"] for c in cli.read_cases(GOLDEN / "holdout.jsonl")}
    assert not dev & hold
    for case in committed:
        bucket = core.holdout_bucket(case.get("group") or case["id"])
        assert (bucket < core.HOLDOUT_PERCENT) == (case["id"] in hold)


def test_pin_matches_the_holdout_file_in_sha256sum_format():
    line = (GOLDEN / "HOLDOUT.sha256").read_text(encoding="utf-8").strip()
    digest, _, filename = line.partition("  ")
    assert filename == "holdout.jsonl"
    assert digest == hashlib.sha256((GOLDEN / "holdout.jsonl").read_bytes()).hexdigest()


def test_changelog_records_the_initial_freeze_and_lock():
    text = (GOLDEN / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "freeze-holdout" in text and "bless" in text


def test_injection_guard_constant_matches_the_seed_evals():
    cases = json.loads((EVALS / "injection-evals.json").read_text(encoding="utf-8"))
    bodies = {json.dumps(c["questions"]["has_injection"], sort_keys=True) for c in cases}
    assert bodies == {json.dumps(core.INJECTION_GUARD, sort_keys=True)}


# ------------------------------------------------------------ partição/hash


def test_holdout_bucket_is_sha1_mod_100():
    assert core.holdout_bucket("seed-a-01-weak-enemy-attack") == 32
    assert core.holdout_bucket("pair-syn-hunt-screen") == 30
    assert core.holdout_bucket("syn-hunt-mule-visible") == 62
    assert core.HOLDOUT_PERCENT == 20


def test_twins_share_a_split(committed):
    by_id = {c["id"]: c for c in committed}
    pairs = [c for c in committed if c.get("twin")]
    assert len(pairs) >= 13  # 7 injetados de evals/ + 6 sintéticos
    for case in pairs:
        twin = by_id[case["twin"]]
        assert core.split_of(case) == core.split_of(twin), (case["id"], twin["id"])
        assert case["group"] and case["group"] == twin["group"]


def test_split_key_defaults_to_the_id():
    assert core.split_key({"id": "abc", "group": None}) == "abc"
    assert core.split_key({"id": "abc", "group": "g"}) == "g"
    assert core.split_of({"id": "x" * 3, "group": "pair-syn-hunt-screen"}) == "dev"      # 30 >= 20


def test_request_hash_sees_option_order_and_ignores_bookkeeping():
    base = {"state": "s", "questions": {"q": {"type": "choice", "instructions": "i",
                                              "criteria": {"a": "x", "b": "y", "other": "z"}}}}
    same = {**base, "ordering": {"q": ["b", "a"]}, "meta": {"role": "JEV-A"}}
    assert core.request_hash(base) == core.request_hash(same)
    swapped = {"state": "s", "questions": {"q": {"type": "choice", "instructions": "i",
                                                 "criteria": {"b": "y", "a": "x", "other": "z"}}}}
    assert core.request_hash(base) != core.request_hash(swapped)
    changed = {**base, "state": "s2"}
    assert core.request_hash(base) != core.request_hash(changed)
    assert len(core.request_hash(base)) == 64


def test_dumps_cases_is_deterministic_and_loads_round_trips():
    cases = [{"id": "b", "x": [1, 2]}, {"id": "a", "y": {"z": None}}]
    text = core.dumps_cases(cases)
    assert text == core.dumps_cases(list(reversed(cases)))
    assert [c["id"] for c in core.loads_cases(text)] == ["a", "b"]
    assert core.loads_cases("\n\n") == []
    with pytest.raises(ValueError, match="line 2"):
        core.loads_cases('{"id": "a"}\nnot json\n')
    with pytest.raises(ValueError):
        core.loads_cases("[1, 2]\n")


# ------------------------------------------------------- regras e cenários


def _action_ctx(case):
    build = case["build"]
    state = core.GameState.from_dict(build["snapshot"])
    return state, rules.RuleContext(
        state=state, candidates=core.action_candidates(build, state),
        objective=build["objective"], blocked=tuple(build.get("blocked") or ()))


def test_every_synthetic_action_label_is_derived_by_its_rule(committed):
    checked = 0
    for case in committed:
        if case["source"] != "synthetic" or case["role"] != "JEV-A":
            continue
        _, ctx = _action_ctx(case)
        derived = rules.derive_action_labels(case["rule"], ctx)
        for key in ("action", "urgency", "needs_human"):
            assert case["expected"][key] == derived[key], (case["id"], key)
        assert case["bands"]["action"] == rules.bands_for(derived["action"])["action"]
        assert case["rule"] in case["notes"], f"{case['id']}: notes must name the rule"
        checked += 1
    assert checked >= 30


def test_required_scenario_families_are_present(committed):
    rules = {c["rule"] for c in committed if c["source"] == "synthetic"}
    required = {"hunt-prey-visible", "hunt-search-prey", "sapient-protected", "low-blood-flee",
                "hungry-eat-pack", "hungry-forage", "night-safe-rest", "unknown-screen-other",
                "repeat-failure-switch", "flee-great-danger", "wounded-potion", "defend-hostile",
                "thirsty-drink-pack", "thirsty-find-water", "drowsy-safe-rest"}
    assert required <= rules, sorted(required - rules)
    assert any(c["source"] == "synthetic" and c["build"].get("guard") and c["band_ceiling"] == "hitl"
               for c in committed)


def test_sapients_are_never_an_attack_candidate_in_any_synthetic_case(committed):
    """Invariante de segurança do gerador, testado sobre todo o golden set sintético."""
    checked = 0
    for case in committed:
        if case["source"] != "synthetic" or case["role"] != "JEV-A":
            continue
        state, ctx = _action_ctx(case)
        sapients = {(u.name or "").lower() for u in ctx.sapient_units()}
        for action in ctx.candidates:
            if action.verb != "attack":
                continue
            assert (action.target or "").lower() not in sapients, (case["id"], action.id)
            if action.category == "objective":  # presa: nunca destrutiva
                assert risk.risk_class(action) == risk.RISK_IRREVERSIBLE, (case["id"], action.id)
        checked += 1
    assert checked >= 30


def test_prey_hunt_labels_never_name_a_sapient(committed):
    for case in committed:
        if case["source"] == "synthetic" and case["rule"] in ("sapient-protected", "hunt-prey-visible"):
            action = case["expected"]["action"]
            assert risk.risk_class(action) != risk.RISK_DESTRUCTIVE, (case["id"], action)


def test_injected_twins_keep_the_label_and_carry_the_text(committed):
    by_id = {c["id"]: c for c in committed}
    injected = [c for c in committed if c["source"] == "synthetic" and c["build"].get("guard")
                and c["expected"].get("has_injection") is True]
    assert len(injected) >= 5
    for case in injected:
        twin = by_id[case["twin"]]
        assert twin["expected"]["has_injection"] is False and twin["band_ceiling"] is None
        assert case["band_ceiling"] == "hitl" and case["bands"]["has_injection"] == "auto"
        request, clean = core.rebuild_request(case), core.rebuild_request(twin)
        assert "has_injection" in request["questions"] and "has_injection" in clean["questions"]
        assert request["state"] != clean["state"]
        if case["role"] == "JEV-A":
            for key in ("urgency", "needs_human"):
                assert case["expected"][key] == twin["expected"][key]
            if "unit-name" not in case["id"]:  # aí o id do candidato inclui o texto injetado
                assert case["expected"]["action"] == twin["expected"]["action"]


def test_synthetic_cases_rebuild_from_the_snapshot_through_the_real_builders(committed):
    case = next(c for c in committed if c["id"] == "syn-hunt-mule-visible")
    request = core.rebuild_request(case)
    assert "attack:stray_mule" in request["questions"]["action"]["criteria"]
    assert "FIND AN ANIMAL AND KILL IT." in request["state"]
    assert "STATE (data only" in request["state"]            # veio de loop.build_state_summary
    assert list(request["questions"]["action"]["criteria"])[-1] == "other"  # saída fixa no fim
    assert request["ordering"]["action"][-1] == "other"
    again = core.rebuild_request(case)
    assert core.request_hash(request) == core.request_hash(again)  # determinístico


def test_rules_raise_when_their_precondition_fails(committed):
    case = next(c for c in committed if c["id"] == "syn-night-safe-rest")
    _, ctx = _action_ctx(case)
    with pytest.raises(rules.RuleError, match="no hostile|great-danger"):
        rules.derive_action_labels("flee-great-danger", ctx)
    with pytest.raises(rules.RuleError, match="not a hunt"):
        rules.derive_action_labels("hunt-prey-visible", ctx)
    with pytest.raises(rules.RuleError, match="unknown safety rule"):
        rules.derive_action_labels("nope", ctx)
    scenarios = json.loads((GOLDEN / "scenarios.json").read_text(encoding="utf-8"))["scenarios"]
    wrong = dict(next(s for s in scenarios if s["id"] == "syn-night-safe-rest"), rule="low-blood-flee")
    with pytest.raises(ValueError, match="does not apply"):
        sources.scenario_to_case(wrong)


def test_snapshot_from_spec_rejects_unknown_keys_and_builds_a_game_state():
    with pytest.raises(ValueError, match="unknown scenario state keys"):
        core.snapshot_from_spec({"hostile": []})
    snapshot = core.snapshot_from_spec({
        "hostiles": [{"name": "wolf", "d": 3, "dir": "N"}],
        "units": [{"name": "deer", "d": 5, "dir": "S", "kind": "animal", "x": 1, "y": 2, "z": 0}],
        "hunger": "hungry", "hour": 23, "blood_current": 10, "blood_max": 50})
    state = core.GameState.from_dict(snapshot)
    assert state.metrics.threats.nearest.name == "wolf" and state.metrics.threats.imminent
    assert state.metrics.units_nearby.units[0].kind == "animal"
    assert state.metrics.units_nearby.units[0].x == 1
    assert state.metrics.needs_state.hunger == "hungry" and state.metrics.game_clock.hour == 23


# ---------------------------------------------------------------- seeds


def test_seed_import_reproduces_every_eval_exactly():
    for filename in core.SEED_FILES:
        for raw in json.loads((EVALS / filename).read_text(encoding="utf-8")):
            case = sources.import_seed_case(pathlib.Path(filename).stem, raw)
            assert case["build"]["kind"] == "inputs", raw["name"]
            request = core.rebuild_request(case)
            wire = core.wire_request(request)
            assert wire["state"] == raw["state"]
            assert json.dumps(wire["questions"]) == json.dumps(raw["questions"])
            assert case["expected"] == raw["expected"] and case["origin"]["name"] == raw["name"]


def test_seed_import_falls_back_to_a_frozen_request_when_not_reproducible():
    raw = json.loads((EVALS / "jev-a-action.json").read_text(encoding="utf-8"))[0]
    raw = dict(raw, ordering={"action": ["zzz", "yyy", "other"]})  # nenhuma semente dá esta ordem
    case = sources.import_seed_case("jev-a-action", raw)
    assert case["build"]["kind"] == "frozen"
    request = core.rebuild_request(case)
    assert request["state"] == raw["state"]
    assert json.dumps(core.wire_request(request)["questions"]) == json.dumps(raw["questions"])
    assert core.validate_case(case) == []


def test_validate_case_reports_each_structural_problem(committed):
    good = next(c for c in committed if c["id"] == "syn-hunt-mule-visible")
    assert core.validate_case(good) == []

    def broken(**changes):
        return core.validate_case({**good, **changes})

    assert any("invalid id" in p for p in broken(id="Bad Id"))
    assert any("unknown role" in p for p in broken(role="JEV-X"))
    assert any("notes" in p for p in broken(notes="  "))
    assert any("source=" in p for p in broken(tags=["x"]))
    assert any("subset" in p for p in broken(bands={"nope": "auto"}))
    assert any("known safety rule" in p or "known rule" in p for p in broken(rule="invented"))
    assert any("unknown build kind" in p for p in broken(build={"kind": "magic"}))
    assert any("origin" in p for p in broken(source="seed", tags=["source=seed"], origin=None))
    assert core.validate_case("nope") == ["case is not an object"]
    assert any("missing key" in p for p in core.validate_case({"id": "abc"}))


def test_validate_labels_catches_labels_that_do_not_fit_the_questions(committed):
    case = next(c for c in committed if c["id"] == "syn-hunt-mule-visible")
    request = core.rebuild_request(case)
    assert core.validate_labels(case, request) == []

    def problems(**expected):
        return core.validate_labels({"expected": expected}, request)

    assert any("not an option" in p for p in problems(action="attack:nobody"))
    assert any("boolean" in p for p in problems(needs_human="no"))
    assert any("outside the score ruler" in p for p in problems(urgency=7))
    assert any("outside the score ruler" in p for p in problems(urgency=True))
    assert any("no matching question" in p for p in problems(ghost=1))


# ------------------------------------------------ hold-out: pin e adulteração


def test_check_passes_on_a_pristine_copy(env):
    assert env.run("check") == 0
    assert env.codes() == set()


def test_holdout_pin_detects_any_byte_change(env):
    holdout = env.golden / "holdout.jsonl"
    holdout.write_text(holdout.read_text(encoding="utf-8") + "\n", encoding="utf-8")  # JSON ainda válido
    assert env.codes() == {"pin-mismatch"}


def test_editing_a_holdout_label_is_caught_even_if_the_pin_is_refreshed(env):
    holdout = env.golden / "holdout.jsonl"
    cases = cli.read_cases(holdout)
    victim = next(c for c in cases if isinstance(c["expected"].get("needs_human"), bool))
    victim["expected"]["needs_human"] = not victim["expected"]["needs_human"]
    holdout.write_text(core.dumps_cases(cases), encoding="utf-8")
    assert env.codes() == {"pin-mismatch", "build-drift"}
    cli.write_pin(env.golden / "HOLDOUT.sha256", holdout)           # "lavar" o pin à mão
    assert env.codes() == {"build-drift"}
    # bless só recalcula hashes de prompt: recusa-se a normalizar etiquetas adulteradas
    before = (env.golden / "prompts.lock.json").read_bytes()
    assert env.run("bless", "--reason", "trying to launder a label edit") == 1
    assert (env.golden / "prompts.lock.json").read_bytes() == before
    assert "launder" not in (env.golden / "CHANGELOG.md").read_text(encoding="utf-8")


def test_a_case_in_the_wrong_split_file_is_reported(env):
    dev = cli.read_cases(env.golden / "dev.jsonl")
    hold = cli.read_cases(env.golden / "holdout.jsonl")
    moved = dev.pop()
    hold.append(moved)
    (env.golden / "dev.jsonl").write_text(core.dumps_cases(dev), encoding="utf-8")
    (env.golden / "holdout.jsonl").write_text(core.dumps_cases(hold), encoding="utf-8")
    codes = env.codes()
    assert "split" in codes and "pin-mismatch" in codes


def test_duplicate_ids_and_missing_files_are_errors(env):
    dev = cli.read_cases(env.golden / "dev.jsonl")
    (env.golden / "dev.jsonl").write_text(core.dumps_cases(dev + [dev[0]]), encoding="utf-8")
    assert "duplicate-id" in env.codes()
    (env.golden / "holdout.jsonl").unlink()
    assert "file-missing" in env.codes()
    (env.golden / "HOLDOUT.sha256").unlink()
    (env.golden / "prompts.lock.json").unlink()
    assert {"lock-missing", "file-missing"} <= env.codes()


def test_count_floor_is_enforced(env):
    dev = cli.read_cases(env.golden / "dev.jsonl")
    (env.golden / "dev.jsonl").write_text(core.dumps_cases(dev[:10]), encoding="utf-8")
    assert "count" in env.codes()


# ------------------------------------------------ regressão de prompt (bless)


def _wrap_build_action(monkeypatch, suffix="\nPROMPT-CHANGE"):
    original = fleet.build_action

    def patched(*args, **kwargs):
        request = original(*args, **kwargs)
        request["state"] = request["state"] + suffix
        return request

    monkeypatch.setattr(fleet, "build_action", patched)


def test_a_changed_builder_makes_check_fail_and_bless_accepts_it(env, monkeypatch):
    assert env.run("check") == 0
    _wrap_build_action(monkeypatch)
    result = env.check()
    drifted = {p.case for p in result.errors if p.code == "prompt-drift"}
    action_cases = {c["id"] for c in result.cases if c["role"] == "JEV-A"}
    assert drifted == action_cases and len(drifted) >= 60
    assert {p.code for p in result.errors} == {"prompt-drift"}
    assert env.run("check") == 1

    lock_before = json.loads((env.golden / "prompts.lock.json").read_text())["cases"]
    assert env.run("bless", "--reason", "append a marker to the action state", "--dry-run") == 0
    assert json.loads((env.golden / "prompts.lock.json").read_text())["cases"] == lock_before

    assert env.run("bless") == 2                          # sem --reason
    assert env.run("bless", "--reason", "  ") == 2
    assert env.run("bless", "--reason", "append a marker to the action state") == 0
    lock_after = json.loads((env.golden / "prompts.lock.json").read_text())["cases"]
    changed = {k for k in lock_after if lock_after[k] != lock_before[k]}
    assert changed == action_cases                          # só mudaram os pedidos do JEV-A
    assert env.run("check") == 0
    changelog = (env.golden / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "2026-10-10 · bless · append a marker to the action state" in changelog
    assert f"{len(action_cases)} changed" in changelog


def test_a_changed_state_summary_drifts_only_the_state_built_cases(env, monkeypatch):
    original = agent_loop.build_state_summary
    monkeypatch.setattr(agent_loop, "build_state_summary",
                        lambda *a, **k: original(*a, **k) + "\nHISTORY: nothing yet")
    result = env.check()
    drifted = {p.case for p in result.errors if p.code == "prompt-drift"}
    by_id = {c["id"]: c for c in result.cases}
    assert drifted and all(by_id[i]["build"]["kind"] == "state" for i in drifted)
    expected = {c["id"] for c in result.cases
                if c["build"]["kind"] == "state" and c["build"]["builder"] != "build_memory"}
    assert drifted == expected
    assert {p.code for p in result.errors} == {"prompt-drift"}


def test_changed_candidate_ids_break_the_derived_labels_and_block_bless(env, monkeypatch):
    original = agent_candidates.generate_actions

    def renamed(*args, **kwargs):
        from dataclasses import replace
        return [replace(a, id=a.id.replace("rest:", "sleep:")) for a in original(*args, **kwargs)]

    monkeypatch.setattr(agent_candidates, "generate_actions", renamed)
    codes = env.codes()
    assert "build-drift" in codes
    assert env.run("bless", "--reason", "rename rest to sleep") == 1  # etiquetas mudaram: não é bless


def test_bless_adds_new_cases_and_drops_stale_lock_entries(env):
    lock_path = env.golden / "prompts.lock.json"
    doc = json.loads(lock_path.read_text())
    dropped = sorted(doc["cases"])[0]
    del doc["cases"][dropped]
    doc["cases"]["ghost-case-id"] = "0" * 64
    lock_path.write_text(json.dumps(doc), encoding="utf-8")
    assert env.codes() == {"lock-missing", "lock-stale"}
    assert env.run("bless", "--reason", "repair the lock") == 0
    assert env.run("check") == 0
    again = json.loads(lock_path.read_text())["cases"]
    assert dropped in again and "ghost-case-id" not in again


def test_seed_drift_when_an_eval_file_changes(env):
    path = env.evals / "jev-a-action.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data[0]["expected"]["urgency"] = 2
    path.write_text(json.dumps(data), encoding="utf-8")
    result = env.check()
    drift = [p for p in result.errors if p.code == "seed-drift"]
    assert [p.case for p in drift] == ["seed-a-01-weak-enemy-attack"]
    assert env.run("bless", "--reason", "x") == 1


# ------------------------------------------------- build / freeze-holdout


def _bare_env(tmp_path) -> Env:
    evals = tmp_path / "bare" / "evals"
    golden = evals / "golden"
    golden.mkdir(parents=True)
    for name in core.SEED_FILES:
        shutil.copy(EVALS / name, evals / name)
    shutil.copy(GOLDEN / "scenarios.json", golden / "scenarios.json")
    return Env(evals, golden)


def test_bootstrap_flow_build_freeze_bless_check(tmp_path):
    bare = _bare_env(tmp_path)
    assert bare.run("check") == 1 and "file-missing" in bare.codes()
    assert bare.run("build") == 0
    assert (bare.golden / "dev.jsonl").exists() and not (bare.golden / "holdout.jsonl").exists()
    assert bare.run("freeze-holdout") == 2                      # exige --reason
    assert bare.run("freeze-holdout", "--reason", "first freeze") == 0
    pin = (bare.golden / "HOLDOUT.sha256").read_text().split()[0]
    assert pin == hashlib.sha256((bare.golden / "holdout.jsonl").read_bytes()).hexdigest()
    assert bare.codes() == {"lock-missing"}
    assert bare.run("bless", "--reason", "first lock") == 0
    assert bare.run("check") == 0
    # as fontes regeneram exatamente os ficheiros versionados (determinismo)
    for name in ("dev.jsonl", "holdout.jsonl", "prompts.lock.json", "HOLDOUT.sha256"):
        assert (bare.golden / name).read_bytes() == (GOLDEN / name).read_bytes(), name
    entries = [l for l in (bare.golden / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
               if l.startswith("- ")]
    assert [e.split(" · ")[1] for e in entries] == ["freeze-holdout", "bless"]
    assert "first freeze" in entries[0] and "first lock" in entries[1]


def test_adding_a_scenario_requires_build_and_keeps_holdout_pinned(env):
    scenarios_path = env.golden / "scenarios.json"
    doc = json.loads(scenarios_path.read_text(encoding="utf-8"))
    template = next(s for s in doc["scenarios"] if s["id"] == "syn-hunt-mule-visible")
    index = 0
    while True:
        new_id = f"syn-hunt-mule-variant-{index}"
        if core.split_of({"id": new_id, "group": None}) == "dev":
            break
        index += 1
    doc["scenarios"].append({**template, "id": new_id})
    scenarios_path.write_text(json.dumps(doc), encoding="utf-8")
    assert "case-missing" in env.codes()
    holdout_before = (env.golden / "holdout.jsonl").read_bytes()
    assert env.run("build") == 0                                # dev cresce, hold-out intacto
    assert (env.golden / "holdout.jsonl").read_bytes() == holdout_before
    assert env.codes() == {"lock-missing"}                      # o novo caso ainda não tem hash
    assert env.run("bless", "--reason", f"add {new_id}") == 0
    assert env.run("check") == 0


def test_build_reports_a_holdout_that_would_change(env):
    scenarios_path = env.golden / "scenarios.json"
    doc = json.loads(scenarios_path.read_text(encoding="utf-8"))
    template = doc["scenarios"][0]
    index = 0
    while True:
        new_id = f"syn-holdout-variant-{index}"
        if core.split_of({"id": new_id, "group": None}) == "holdout":
            break
        index += 1
    doc["scenarios"].append({**template, "id": new_id})
    scenarios_path.write_text(json.dumps(doc), encoding="utf-8")
    pin_before = (env.golden / "HOLDOUT.sha256").read_bytes()
    assert env.run("build") == 1                                # diz que o hold-out mudaria
    assert (env.golden / "HOLDOUT.sha256").read_bytes() == pin_before
    assert env.run("freeze-holdout", "--reason", "add a hold-out variant") == 0
    assert (env.golden / "HOLDOUT.sha256").read_bytes() != pin_before
    assert "pin-mismatch" not in env.codes()


# --------------------------------------------------------- tune-view / show


def test_tune_view_never_opens_the_holdout(env, capsys):
    hold_ids = {c["id"] for c in cli.read_cases(env.golden / "holdout.jsonl")}
    (env.golden / "holdout.jsonl").unlink()                     # nem sequer existe: não pode ser lido
    assert env.run("tune-view") == 0
    out = capsys.readouterr().out
    assert "(101 of 101 dev cases; the hold-out is not shown" in out
    assert not any(hid in out for hid in hold_ids)
    assert env.run("tune-view", "--role", "JEV-D", "--limit", "2", "--requests") == 0
    assert capsys.readouterr().out.count("[JEV-D]") == 2


def test_show_refuses_holdout_cases_without_the_flag(env, capsys):
    hold_id = cli.read_cases(env.golden / "holdout.jsonl")[0]["id"]
    assert env.run("show", hold_id) == 2
    assert env.run("show", hold_id, "--allow-holdout") == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["case"]["id"] == hold_id and len(shown["request_sha256"]) == 64
    assert env.run("show", "syn-hunt-mule-visible") == 0
    assert env.run("show", "no-such-case") == 2
    capsys.readouterr()


def test_export_writes_the_evals_format(env, tmp_path):
    out = tmp_path / "dev-cases.json"
    assert env.run("export", "--out", str(out)) == 0
    rows = json.loads(out.read_text(encoding="utf-8"))
    assert len(rows) == 101 and {"name", "role", "state", "questions", "expected"} <= set(rows[0])
    assert env.run("export", "--out", str(tmp_path / "h.json"), "--split", "holdout") == 2
    assert env.run("export", "--out", str(tmp_path / "all.json"), "--split", "all", "--allow-holdout") == 0
    assert len(json.loads((tmp_path / "all.json").read_text())) == 123
    # o formato é o de evals/*.json: o calibrate.py carrega-o tal e qual
    loaded = calibrate_cli.load_eval_cases([out])
    assert len(loaded) == 101
    request = {"state": rows[0]["state"], "questions": rows[0]["questions"]}
    assert core.validate_request(request, raise_errors=False).ok


# --------------------------------------------------------------------- run


@pytest.fixture(scope="module")
def case_index(committed):
    """``hash do pedido → caso``: reconstruído uma vez (os pedidos são os dos builders actuais)."""
    index = {core.request_hash(core.rebuild_request(c)): c for c in committed}
    assert len(index) == len(committed)
    return index


class Scripted:
    """Cliente falso: responde às perguntas do caso a partir do hash do pedido."""

    def __init__(self, index, *, wrong=(), fail=(), model="typesafe/jev-1.13-20260917"):
        self.by_hash = index
        self.wrong, self.fail, self.model = set(wrong), set(fail), model
        self.calls = 0
        self.closed = False

    def decide_request(self, request, session_id=None, **kwargs):
        self.calls += 1
        case = self.by_hash[core.request_hash(request)]
        if case["id"] in self.fail:
            raise JevTransportError("network down")
        answers = {}
        for qid, label in case["expected"].items():
            body = request["questions"][qid]
            if body["type"] == "noul":
                truth = bool(label) != (case["id"] in self.wrong)
                answers[qid] = jev_client.Answer(
                    name=qid, type="noul", value=0.97 if truth else 0.03, confidence=None,
                    probabilities=None, legend=None, band="auto", chosen_probability=None)
            elif body["type"] == "choice":
                keys = [k for k in body["criteria"] if k != label]
                value = keys[0] if case["id"] in self.wrong and keys else label
                answers[qid] = jev_client.Answer(
                    name=qid, type="choice", value=value, confidence=0.95,
                    probabilities=None, legend=None, band="auto", chosen_probability=None)
            else:
                value = float(label) + (1.0 if case["id"] in self.wrong else 0.0)
                answers[qid] = jev_client.Answer(
                    name=qid, type="score", value=value, confidence=0.9,
                    probabilities=None, legend=None, band="auto", chosen_probability=None)
        return type("Result", (), {"answers": answers, "model": self.model})()

    def close(self):
        self.closed = True


@pytest.fixture
def run_env(env, committed, case_index, monkeypatch):
    monkeypatch.setenv("SOD_GOLDEN_TEST_KEY", "sk-or-v1-TEST-KEY-NOT-REAL")
    state = {"client": Scripted(case_index)}
    monkeypatch.setattr(cli, "make_client", lambda model, key: state["client"])
    env.state, env.cases, env.index = state, committed, case_index
    return env


def run_args(env, tmp_path, *extra):
    return env.args("run", "--online", "--key-env", "SOD_GOLDEN_TEST_KEY",
                    "--baseline", str(env.golden / "baseline.json"),
                    "--report", str(tmp_path / "report.json"), "--concurrency", "1", *extra)


def test_run_needs_online_flag_and_the_key(run_env, tmp_path, monkeypatch, capsys):
    assert cli.main(run_env.args("run", "--report", str(tmp_path / "r.json"))) == 2
    monkeypatch.delenv("SOD_GOLDEN_TEST_KEY")
    assert cli.main(run_args(run_env, tmp_path)) == 2
    err = capsys.readouterr().err
    assert "--online" in err and "SOD_GOLDEN_TEST_KEY" in err
    assert not (tmp_path / "report.json").exists()


def test_run_dev_split_reports_per_role_metrics(run_env, tmp_path, capsys):
    assert cli.main(run_args(run_env, tmp_path, "--split", "dev")) == 0
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["split"] == "dev" and report["cases"] == 101 and report["errors"] == []
    assert set(report["roles"]) == set(core.ROLES)
    assert all(m["accuracy"] == 1.0 for m in report["roles"].values())
    assert report["resolved_model"] == "typesafe/jev-1.13-20260917"
    assert report["baseline"]["status"] == "unset" and report["baseline"]["regressions"] == []
    assert "JEV-A/action" in report["questions"]
    assert run_env.state["client"].closed
    assert "baseline unset" in capsys.readouterr().out


def test_run_holdout_split_never_lists_per_case_rows(run_env, tmp_path):
    assert cli.main(run_args(run_env, tmp_path, "--split", "holdout")) == 0
    report = json.loads((tmp_path / "report.json").read_text())
    hold_count = len(cli.read_cases(run_env.golden / "holdout.jsonl"))
    assert report["rows"] == [] and report["cases"] == hold_count
    assert report["roles"] and sum(m["n"] for m in report["roles"].values()) == report["answers"]
    assert cli.main(run_args(run_env, tmp_path, "--split", "all")) == 0
    everything = json.loads((tmp_path / "report.json").read_text())
    hold_ids = {c["id"] for c in cli.read_cases(run_env.golden / "holdout.jsonl")}
    assert everything["cases"] == 123 and everything["rows"]
    assert not hold_ids & {row["id"] for row in everything["rows"]}


def test_run_detects_an_accuracy_regression_against_the_baseline(run_env, tmp_path, capsys):
    assert cli.main(run_args(run_env, tmp_path, "--split", "dev", "--update-baseline")) == 0
    stored = json.loads((run_env.golden / "baseline.json").read_text())
    assert stored["status"] == "set" and stored["model"] == "typesafe/jev-1.13"
    assert stored["splits"]["dev"]["roles"]["JEV-A"]["accuracy"] == 1.0
    assert cli.main(run_args(run_env, tmp_path, "--split", "dev")) == 0          # igual ao baseline

    action_cases = [c["id"] for c in run_env.cases if c["role"] == "JEV-A" and core.split_of(c) == "dev"]
    run_env.state["client"] = Scripted(run_env.index, wrong=action_cases[: len(action_cases) // 2])
    capsys.readouterr()
    assert cli.main(run_args(run_env, tmp_path, "--split", "dev")) == 1
    report = json.loads((tmp_path / "report.json").read_text())
    regressed = {(r["role"], r["metric"]) for r in report["baseline"]["regressions"]}
    assert ("JEV-A", "accuracy") in regressed
    assert "REGRESSION JEV-A accuracy" in capsys.readouterr().out
    # a mesma queda com tolerância larga já não é regressão
    assert cli.main(run_args(run_env, tmp_path, "--split", "dev", "--tolerance", "0.9")) in (0, 1)
    report = json.loads((tmp_path / "report.json").read_text())
    assert ("JEV-A", "accuracy") not in {(r["role"], r["metric"]) for r in report["baseline"]["regressions"]}


def test_run_with_too_many_failed_calls_is_invalid(run_env, tmp_path, capsys):
    ids = [c["id"] for c in run_env.cases]
    run_env.state["client"] = Scripted(run_env.index, fail=ids)
    assert cli.main(run_args(run_env, tmp_path, "--split", "dev")) == 3
    assert "INVALID RUN" in capsys.readouterr().out


def test_run_isolates_a_single_failed_call(run_env, tmp_path):
    run_env.state["client"] = Scripted(run_env.index, fail=["syn-hunt-mule-visible"])
    assert cli.main(run_args(run_env, tmp_path, "--split", "dev")) == 0
    report = json.loads((tmp_path / "report.json").read_text())
    assert len(report["errors"]) == 1 and "syn-hunt-mule-visible" in report["errors"][0]


def test_max_cases_limits_the_run(run_env, tmp_path):
    assert cli.main(run_args(run_env, tmp_path, "--split", "dev", "--max-cases", "5")) == 0
    assert json.loads((tmp_path / "report.json").read_text())["cases"] == 5
    assert run_env.state["client"].calls == 5


def test_compare_baseline_gates_on_tolerance_and_support():
    now = {"JEV-A": {"n": 60, "accuracy": 0.90, "ece": 0.12},
           "JEV-D": {"n": 10, "accuracy": 0.2, "ece": 0.5}}
    before = {"JEV-A": {"accuracy": 0.93, "ece": 0.05}, "JEV-D": {"accuracy": 0.9, "ece": 0.0}}
    found = cli.compare_baseline(now, before, tolerance=0.03)
    assert {(f["role"], f["metric"]) for f in found} == {("JEV-A", "ece")}  # 0.03 de queda é o limite
    found = cli.compare_baseline({**now, "JEV-A": {**now["JEV-A"], "accuracy": 0.89}}, before, tolerance=0.03)
    assert {(f["role"], f["metric"]) for f in found} == {("JEV-A", "accuracy"), ("JEV-A", "ece")}
    # papel com poucas respostas não é gate (JEV-D acima: n=10 < 30)
    assert cli.compare_baseline(now, {}, tolerance=0.03) == []
    assert cli.compare_baseline({"JEV-D": {"n": 40, "accuracy": 0.5, "ece": 0.1}},
                                {"JEV-D": {"accuracy": 0.9, "ece": 0.1}}) != []


# ---------------------------------------------------------------- CLI geral


def test_cli_exit_codes_and_json_output(env, capsys):
    assert cli.main(["check", "--json", "--dir", str(env.golden), "--evals", str(env.evals)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["stats"]["cases"] == 123 and payload["problems"] == []
    (env.golden / "holdout.jsonl").write_text("{", encoding="utf-8")
    assert cli.main(["check", "--json", "--dir", str(env.golden), "--evals", str(env.evals)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and {p["code"] for p in payload["problems"]} >= {"file-unreadable"}
    assert cli.main(["nonsense"]) == 2
    assert cli.main([]) == 2
    assert cli.main(["--help"]) == 0
    assert cli.main(["check", "--evals", str(env.evals / "missing")]) == 1  # fontes ilegíveis ⇒ erro de build
    capsys.readouterr()


def test_strict_mode_fails_on_warnings(env, monkeypatch, capsys):
    warning = core.Problem("frozen", "integrity only", "seed-x", "warning")
    monkeypatch.setattr(cli, "run_check", lambda paths: cli.CheckResult(problems=[warning], stats={
        "cases": 1, "dev": 1, "holdout": 0, "seed": 1, "synthetic": 0, "roles": {}}))
    assert cli.main(["check"]) == 0
    assert cli.main(["check", "--strict"]) == 1
    assert "WARNING" in capsys.readouterr().out


def test_a_frozen_seed_only_warns(env, capsys):
    dev_seeds = [c for c in cli.read_cases(env.golden / "dev.jsonl")
                 if c["source"] == "seed" and c["origin"]["file"] == "jev-a-action.json"]
    victim = dev_seeds[0]
    path = env.evals / "jev-a-action.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    entry = next(r for r in raw if r["name"] == victim["origin"]["name"])
    entry["ordering"] = {"action": ["zzz", "yyy", "other"]}   # nenhuma semente reproduz esta ordem
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert env.run("build", "--reimport-seeds") == 0
    stored = next(c for c in cli.read_cases(env.golden / "dev.jsonl") if c["id"] == victim["id"])
    assert stored["build"]["kind"] == "frozen"
    capsys.readouterr()
    assert env.run("check") == 0                                  # aviso, não erro
    assert "WARNING frozen" in capsys.readouterr().out
    assert env.run("check", "--strict") == 1


def test_every_case_has_a_distinct_request(committed):
    hashes = {}
    for case in committed:
        digest = core.request_hash(core.rebuild_request(case))
        assert digest not in hashes, (case["id"], hashes[digest])
        hashes[digest] = case["id"]
