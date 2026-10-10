"""Testes offline de ``scripts/e2e/hunt_plan.py`` (plano de caça verificado).

O script passou a usar o ciclo gerar→testar→criticar de ``agent/planning.py``
(ARQUITETURA §8, mudanças 09/10). Estes testes garantem, sem rede, sem jogo e
sem chave: (a) o contrato antigo (flags, chaves do JSON, ``validate_plan``,
``fallback_plan``, ``snapshot_digest``) continua igual; (b) sem chave sai o
plano de *fallback* com schema válido; (c) com um LLM falso o ciclo repara um
plano mau com o *feedback* visível no 2.º prompt e o esforço viaja no objeto
aninhado ``reasoning``; (d) nenhum segredo chega à saída.
"""

from __future__ import annotations

import importlib.util
import json
import runpy
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "e2e" / "hunt_plan.py"
FAKE_KEY = "sk-or-v1-FAKE-KEY-THAT-MUST-NEVER-BE-PRINTED-1234567890"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Rede e subprocessos reais falham alto: o teste que lá chegar rebenta."""

    def refuse(*args, **kwargs):
        raise AssertionError("test_hunt_plan_cli must stay offline (no socket, no subprocess)")

    for target, name in ((socket, "socket"), (socket, "create_connection"),
                         (subprocess, "run"), (subprocess, "Popen")):
        monkeypatch.setattr(target, name, refuse)


@pytest.fixture
def hp(monkeypatch, tmp_path):
    """O script carregado, com HOME/ambiente isolados (nenhuma chave real à vista)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("SOD_OPENROUTER_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    module = load("hunt_plan_under_test")
    monkeypatch.setattr(module, "knowledge_context", lambda limit=3: "")
    return module


def snapshot(x=40, y=50, *, region="shrubland temperate", surroundings=None, units=()):
    location = {"x": x, "y": y, "z": 5, "site": "Mortalplan", "site_type": "town",
                "region": region, "tile": "ConstructedFloor",
                "surroundings": surroundings or f"outdoors, {region}"}
    if region is None and surroundings is None:
        location = {"x": x, "y": y, "z": 5}
    elif region is None:
        location["region"] = None
    return {
        "adventurer": {"name": "Usmen Thedlemones", "pos": {"x": x, "y": y, "z": 5},
                       "health": {"wounds": 0}, "kills": 0},
        "metrics": {"location": location,
                    "units_nearby": {"units": [dict(u) for u in units]},
                    "threats": {"count": 0, "level": "none", "hostiles": []},
                    "game_clock": {"tick": 1200}},
    }


def good_plan(x=40, y=50):
    return {
        "where_am_i": f"Mortalplan at {x},{y}",
        "animal_zones": [{"zone": "south scrub", "why": "game avoids floors",
                          "waypoint": [x + 15, y + 15], "biome": "shrubland temperate"},
                         {"zone": "east grass", "why": "grazers", "waypoint": [x + 40, y + 20]}],
        "route_steps": [{"action": "goto", "target": [x + 15, y + 15], "note": "leave town"},
                        {"action": "goto", "target": [x + 40, y + 20], "note": "grass edge"},
                        {"action": "wait", "target": "short", "note": "scan"}],
        "opportunistic_rule": "Attack any non-sapient wild creature on the way; "
                              "never attack humans, dwarves, elves or goblins.",
        "success_conditions": [{"kind": "kill_confirmed"}, {"kind": "steps_elapsed", "steps": 30}],
        "assumptions": ["open shrubland"],
    }


class FakeClient:
    """Estilo ``DeepSeekLLM``: textos em fila, cada pedido guardado."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def complete(self, system, user, *, reasoning="auto", max_tokens=None, temperature=None,
                 purpose=None):
        from system_one_dwarf.agent.llm import LLMResult

        self.calls.append({"system": system, "user": user, "reasoning": reasoning,
                           "max_tokens": max_tokens, "temperature": temperature,
                           "purpose": purpose})
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return LLMResult(text=answer if isinstance(answer, str) else json.dumps(answer))


def schema_errors(value, schema, path="$"):
    """Validador mínimo do subconjunto de JSON-Schema usado pelo ``PLAN_SCHEMA``."""
    if "oneOf" in schema:
        hits = [s for s in schema["oneOf"] if not schema_errors(value, s, path)]
        return [] if len(hits) == 1 else [f"{path}: {len(hits)} alternatives match"]
    errors = []
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in enum")
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: const")
    kinds = {"object": dict, "array": list, "string": str, "integer": int}
    kind = schema.get("type")
    if kind and (not isinstance(value, kinds[kind])
                 or (kind == "integer" and isinstance(value, bool))):
        return errors + [f"{path}: expected {kind}"]
    if isinstance(value, dict):
        errors += [f"{path}: missing {k}" for k in schema.get("required", []) if k not in value]
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                errors += schema_errors(value[key], sub, f"{path}.{key}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 10**9):
            errors.append(f"{path}: bad item count {len(value)}")
        for index, item in enumerate(value):
            if "items" in schema:
                errors += schema_errors(item, schema["items"], f"{path}[{index}]")
    if isinstance(value, str) and not schema.get("minLength", 0) <= len(value) <= schema.get(
            "maxLength", 10**9):
        errors.append(f"{path}: bad length")
    if isinstance(value, int) and not isinstance(value, bool):
        if not schema.get("minimum", -(10**18)) <= value <= schema.get("maximum", 10**18):
            errors.append(f"{path}: out of range")
    return errors


def plan_schema():
    from system_one_dwarf.agent import planning

    return planning.PLAN_SCHEMA


# ===================================================== contrato antigo intacto


def test_the_legacy_helper_names_and_constants_are_still_exported(hp):
    for name in ("load_module", "snapshot", "knowledge_context", "snapshot_digest",
                 "fallback_plan", "validate_plan", "call_llm", "build_plan", "main",
                 "ROOT", "BRIDGE", "DEFAULT_DF", "COALA", "SYSTEM"):
        assert hasattr(hp, name), name
    assert hp.ROOT == ROOT and hp.BRIDGE == ROOT / "dfhack" / "df_bridge.py"
    assert "ONE JSON object" in hp.SYSTEM and "animal_zones" in hp.SYSTEM


def test_validate_plan_keeps_the_legacy_normalisation_contract(hp):
    raw = {
        "where_am_i": "somewhere",
        "animal_zones": [{"zone": "z1", "why": "w", "waypoint": [1, 2]},
                         {"zone": "bad", "why": "no", "waypoint": [1.5, 2]},
                         {"waypoint": [3, 4]}, "junk", None],
        "route_steps": [{"action": "goto", "target": [5, 6], "note": "go"},
                        {"action": "goto", "target": "north"},
                        {"action": "fly", "target": [1, 1]},
                        {"action": "move"},
                        {"action": "move", "target": "ne", "note": "sweep"},
                        {"action": "wait", "target": "ignored", "note": "scan"}, 7],
        "opportunistic_rule": "",
        "source": "llm",
    }
    out = hp.validate_plan(raw)
    assert out["where_am_i"] == "somewhere"
    assert out["animal_zones"] == [{"zone": "z1", "why": "w", "waypoint": [1, 2]},
                                   {"zone": "zone", "why": "", "waypoint": [3, 4]}]
    assert out["route_steps"] == [
        {"action": "goto", "target": [5, 6], "note": "go"},
        {"action": "move", "target": "n", "note": ""},
        {"action": "move", "target": "ne", "note": "sweep"},
        {"action": "wait", "target": "short", "note": "scan"}]
    assert out["opportunistic_rule"] == "attack non-sapient creatures on the way; never sapients"
    assert out["source"] == "llm"
    assert hp.validate_plan({})["where_am_i"] == "unknown"
    assert hp.validate_plan({"source": "fallback"})["source"] == "fallback"
    for junk in (None, "x", 5, []):  # o antigo rebentava aqui
        assert hp.validate_plan(junk)["animal_zones"] == []


def test_fallback_plan_keeps_the_legacy_geometry_and_texts(hp):
    digest = {"location": {"x": 10, "y": 20, "z": 3, "site": "Mortalplan",
                           "surroundings": "outdoors, grassland"}}
    plan = hp.fallback_plan(digest)
    assert plan["where_am_i"] == "Mortalplan (outdoors, grassland) at 10,20,z3"
    assert [z["waypoint"] for z in plan["animal_zones"]] == [[22, 32], [35, 45]]
    assert [(s["action"], s["target"]) for s in plan["route_steps"]] == [
        ("goto", [22, 32]), ("goto", [35, 45]), ("wait", "short")]
    assert plan["source"] == "fallback"
    assert plan["opportunistic_rule"].startswith("attack any non-sapient creature")
    assert [c["kind"] for c in plan["success_conditions"]] == [
        "kill_confirmed", "animal_visible", "steps_elapsed"]
    # sem coordenadas inteiras o antigo usava (0, 0)
    assert hp.fallback_plan({"location": {"x": None, "y": "?"}})["animal_zones"][0][
        "waypoint"] == [12, 12]
    assert hp.fallback_plan({})["where_am_i"] == "unknown site () at 0,0"


def test_snapshot_digest_keeps_the_legacy_shape(hp):
    snap = snapshot(units=[{"name": "stray mule", "kind": "animal", "distance": 6,
                            "direction": "NE", "hostile": False, "visible": True,
                            "pos": {"x": 46, "y": 44, "z": 5}, "extra": "dropped"}])
    digest = hp.snapshot_digest(snap)
    assert set(digest) == {"adventurer", "location", "units_nearby", "threats", "clock"}
    assert digest["adventurer"] == {"name": "Usmen Thedlemones", "pos": {"x": 40, "y": 50, "z": 5},
                                    "health": {"wounds": 0}, "kills": 0}
    assert set(digest["location"]) == {"x", "y", "z", "site", "site_type", "region",
                                       "surroundings", "tile"}
    assert digest["units_nearby"] == [{"name": "stray mule", "kind": "animal", "distance": 6,
                                       "direction": "NE", "hostile": False, "visible": True,
                                       "pos": {"x": 46, "y": 44, "z": 5}}]
    assert digest["threats"] == {"count": 0, "level": "none", "hostiles": []}
    assert digest["clock"] == {"tick": 1200}
    assert hp.snapshot_digest({}) == {"adventurer": {"name": None, "pos": None, "health": None,
                                                     "kills": None},
                                      "location": {k: None for k in digest["location"]},
                                      "units_nearby": [], "threats": None, "clock": None}


# ================================================================ sem chave


def test_without_a_key_the_plan_is_the_deterministic_fallback_with_a_valid_schema(hp):
    plan = hp.build_plan(snap=snapshot(x=40, y=50), fauna_search=lambda q, limit=3: [])
    assert plan["source"] == "fallback"
    assert schema_errors(plan, plan_schema()) == []
    assert [z["waypoint"] for z in plan["animal_zones"]] == [[52, 62], [65, 75]]
    assert plan["where_am_i"] == "Mortalplan (outdoors, shrubland temperate) at 40,50,z5"
    assert plan["planning"] == {"source": "fallback", "attempts": 0, "efforts": [],
                                "issues": [], "errors": []}
    # determinístico: a mesma entrada, o mesmo plano
    again = hp.build_plan(snap=snapshot(x=40, y=50), fauna_search=lambda q, limit=3: [])
    assert again == plan


def test_without_any_data_it_still_returns_a_schema_valid_plan(hp):
    plan = hp.build_plan(snap={}, fauna_search=lambda q, limit=3: [])
    assert plan["source"] == "fallback" and schema_errors(plan, plan_schema()) == []
    assert plan["animal_zones"][0]["waypoint"] == [12, 12]


def test_main_writes_the_json_file_and_prints_the_same_text(hp, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(hp, "snapshot", lambda df_path: snapshot(x=7, y=9))
    monkeypatch.setattr(hp.planning, "fauna_hints", lambda *a, **k: [])
    out = tmp_path / "plano.json"
    assert hp.main(["--out", str(out), "--context", "stuck at [7, 9]"]) == 0
    printed = capsys.readouterr().out
    assert out.read_text(encoding="utf-8") == printed.rstrip("\n")
    plan = json.loads(printed)
    assert plan["source"] == "fallback"  # sem chave: nenhuma chamada
    assert [z["waypoint"] for z in plan["animal_zones"]] == [[19, 21], [32, 34]]
    # as chaves do contrato antigo continuam todas lá
    assert {"where_am_i", "animal_zones", "route_steps", "opportunistic_rule", "source"} <= set(plan)
    assert set(plan["animal_zones"][0]) == {"zone", "why", "waypoint"}
    assert set(plan["route_steps"][0]) == {"action", "target", "note"}
    assert plan["plan_version"] == 1 and plan["success_conditions"] and plan["assumptions"]


def test_script_entrypoint_runs_with_real_flags_and_no_llm(monkeypatch, tmp_path, capsys):
    """Como o ``hunt_watch.py`` o chama: ``--out`` + ``--context``, ``__main__``."""
    asked = []

    def fake_run(argv, **kwargs):
        asked.append(list(argv))
        stdout = json.dumps(snapshot(x=3, y=4)) if "snapshot" in argv else ""
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("SOD_OPENROUTER_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    out = tmp_path / "hunt-plan-live.json"
    monkeypatch.setattr(sys, "argv", ["hunt_plan.py", "--out", str(out), "--context",
                                      "stuck at [3, 4]"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert exit_info.value.code == 0
    plan = json.loads(out.read_text(encoding="utf-8"))
    assert plan["source"] == "fallback"
    assert [z["waypoint"] for z in plan["animal_zones"]] == [[15, 16], [28, 29]]
    assert any("snapshot" in argv for argv in asked)
    assert json.loads(capsys.readouterr().out) == plan


def test_no_llm_flag_never_calls_the_client_even_with_a_key(hp, monkeypatch, tmp_path):
    monkeypatch.setenv("SOD_OPENROUTER_KEY", FAKE_KEY)
    monkeypatch.setattr(hp, "snapshot", lambda df_path: snapshot())
    monkeypatch.setattr(hp, "make_client",
                        lambda key=None: pytest.fail("the LLM client must not even be built"))
    monkeypatch.setattr(hp.planning, "fauna_hints", lambda *a, **k: [])
    plan = hp.build_plan(offline=True)
    assert plan["source"] == "fallback" and plan["planning"]["attempts"] == 0


# =================================================== o ciclo com um LLM falso


def test_the_cycle_repairs_a_bad_plan_with_feedback_visible_in_the_second_prompt(hp):
    bad = good_plan()
    bad["opportunistic_rule"] = "Kill every goblin and human you meet."
    client = FakeClient(json.dumps(bad), json.dumps(good_plan()))
    plan = hp.build_plan(snap=snapshot(), llm=client, fauna_search=lambda q, limit=3: [])
    assert plan["source"] == "llm_repaired"
    assert plan["planning"]["attempts"] == 2
    assert plan["planning"]["efforts"] == ["medium", "high"]
    assert "sapient_target" in plan["planning"]["issues"][0]
    assert schema_errors(plan, plan_schema()) == []

    first, second = client.calls
    # o esforço escolhido pelo CÓDIGO viaja no objeto aninhado (nunca no topo)
    assert first["reasoning"] == {"effort": "medium"}
    assert second["reasoning"] == {"effort": "high"}
    assert first["purpose"] == second["purpose"] == "hunt_plan"
    assert first["max_tokens"] < second["max_tokens"]
    first_user, second_user = json.loads(first["user"]), json.loads(second["user"])
    assert "critic_feedback" not in first_user
    assert second_user["critic_feedback"][0]["code"] == "sapient_target"
    assert "goblin" in second_user["critic_feedback"][0]["message"]
    assert second_user["previous_plan"]["opportunistic_rule"].startswith("Kill every goblin")
    assert second_user["limits"]["max_leg_distance"] == 60
    assert second_user["snapshot"]["location"]["region"] == "shrubland temperate"
    assert "ONE JSON object" in first["system"]
    assert first["system"] == second["system"] == hp.SYSTEM


def test_a_good_first_answer_costs_exactly_one_call_and_is_source_llm(hp):
    client = FakeClient(f"```json\n{json.dumps(good_plan())}\n```")
    plan = hp.build_plan(snap=snapshot(), llm=client, fauna_search=lambda q, limit=3: [])
    assert plan["source"] == "llm" and len(client.calls) == 1
    assert plan["planning"]["attempts"] == 1
    assert [z["waypoint"] for z in plan["animal_zones"]] == [[55, 65], [80, 70]]


def test_exhausting_the_attempts_ends_in_the_deterministic_fallback(hp):
    bad = json.dumps({"where_am_i": "no zones, no route"})
    client = FakeClient(bad, bad, bad, bad)
    plan = hp.build_plan(snap=snapshot(), llm=client, fauna_search=lambda q, limit=3: [])
    assert plan["source"] == "fallback" and len(client.calls) == 3  # ≤ 3 gerações
    assert plan["planning"]["attempts"] == 3
    assert [z["waypoint"] for z in plan["animal_zones"]] == [[52, 62], [65, 75]]
    assert client.calls[-1]["reasoning"] == {"effort": "high"}


def test_max_attempts_flag_is_honoured(hp, monkeypatch):
    bad = json.dumps({"where_am_i": "x"})
    client = FakeClient(bad, bad, bad)
    monkeypatch.setattr(hp, "snapshot", lambda df_path: snapshot())
    monkeypatch.setattr(hp, "make_client", lambda key=None: client)
    monkeypatch.setattr(hp.planning, "fauna_hints", lambda *a, **k: [])
    assert hp.main(["--max-attempts", "1"]) == 0
    assert len(client.calls) == 1


def test_a_plan_that_attacks_sapients_is_never_returned(hp):
    evil = good_plan()
    evil["opportunistic_rule"] = "Hunt the dwarves for sport."
    client = FakeClient(json.dumps(evil), json.dumps(evil), json.dumps(evil))
    plan = hp.build_plan(snap=snapshot(), llm=client, fauna_search=lambda q, limit=3: [])
    assert plan["source"] == "fallback"
    assert "dwarves" not in plan["opportunistic_rule"]
    assert "never prey" in plan["opportunistic_rule"]


@pytest.mark.parametrize("context, kwargs, expected", [
    ("", {}, "max"),                      # 1.º plano, sem conhecimento do mapa
    ("stuck at [1, 2]", {}, "max"),       # re-plano do vigia = 2.ª escalada do stuck
    ("stuck at [1, 2]", {"stuck_escalations": 1}, "medium"),
    ("stuck at [1, 2]", {"stuck_escalations": 4}, "max"),
    ("tried west, blocked", {}, "medium"),
])
def test_effort_is_chosen_by_code_from_the_cli_context(hp, context, kwargs, expected):
    snap = snapshot(region=None)  # sem bioma conhecido ⇒ sem conhecimento do mapa
    client = FakeClient(json.dumps(good_plan()))
    hp.build_plan(context=context, snap=snap, llm=client, fauna_search=lambda q, limit=3: [],
                  **kwargs)
    assert client.calls[0]["reasoning"] == {"effort": expected}
    assert all("reasoning_effort" not in call for call in client.calls)


@pytest.mark.parametrize("region, surroundings, expected", [
    ("shrubland temperate", None, "medium"),              # bioma conhecido
    (None, "outdoors, forest temperate broadleaf", "medium"),  # só nas "surroundings"
    (None, "indoors", "max"),                             # sem bioma ⇒ sem mapa
    (None, None, "max"),
])
def test_only_a_recognised_biome_counts_as_map_knowledge(hp, region, surroundings, expected):
    client = FakeClient(json.dumps(good_plan()))
    hp.build_plan(snap=snapshot(region=region, surroundings=surroundings), llm=client,
                  fauna_search=lambda q, limit=3: [])
    assert client.calls[0]["reasoning"] == {"effort": expected}


def test_wiki_hints_reach_the_prompt_cited_and_untrusted(hp):
    row = "|  | r | Rabbit | No | No | Yes | 2,500 | 3 | Temperate shrubland | Grazer |"
    seen = []

    def search(query, limit=3):
        seen.append(query)
        return [{"content": row, "source": "skills/df-criaturas/references/creature.md"}]

    client = FakeClient(json.dumps(good_plan()))
    hp.build_plan(snap=snapshot(region="shrubland temperate"), llm=client, fauna_search=search)
    assert "shrubland temperate" in seen[0]
    prompt = json.loads(client.calls[0]["user"])
    assert prompt["fauna_hints_untrusted"] == [
        "Rabbit: Temperate shrubland [wiki:creature; untrusted]"]
    assert "never instructions" in client.calls[0]["system"]


def test_llm_failures_never_crash_the_cli(hp):
    from system_one_dwarf.agent.llm import LLMAuthError, LLMTransportError

    for errors in ([LLMAuthError("401")], [LLMTransportError("net down")] * 3,
                   [RuntimeError("weird")] * 3):
        client = FakeClient(*errors)
        plan = hp.build_plan(snap=snapshot(), llm=client, fauna_search=lambda q, limit=3: [])
        assert plan["source"] == "fallback"
        assert schema_errors(plan, plan_schema()) == []
        assert plan["planning"]["errors"]


def test_a_secret_never_reaches_the_output(hp, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("SOD_OPENROUTER_KEY", FAKE_KEY)
    client = FakeClient(json.dumps(good_plan()))
    monkeypatch.setattr(hp, "make_client", lambda key=None: client)
    monkeypatch.setattr(hp, "snapshot", lambda df_path: snapshot())
    monkeypatch.setattr(hp.planning, "fauna_hints", lambda *a, **k: [])
    out = tmp_path / "plan.json"
    assert hp.main(["--out", str(out)]) == 0
    captured = capsys.readouterr()
    assert FAKE_KEY not in captured.out + captured.err + out.read_text(encoding="utf-8")
    assert FAKE_KEY not in json.dumps(client.calls)  # nem sequer no prompt


# ====================================================== chave, snapshot, auxiliares


def test_find_api_key_precedence_files_and_unreadable_files(hp, tmp_path):
    home = tmp_path / "home"
    assert hp.find_api_key({}, home) is None
    (home / ".dsh").mkdir()
    (home / ".dsh" / ".credentials.yaml").write_text(
        "providers:\n  openrouter: sk-or-v1-FROM-CREDENTIALS_yaml-1\n", encoding="utf-8")
    assert hp.find_api_key({}, home) == "sk-or-v1-FROM-CREDENTIALS_yaml-1"
    (home / ".secrets").write_text("export KEY=sk-or-v1-FROM-secrets\n", encoding="utf-8")
    assert hp.find_api_key({}, home) == "sk-or-v1-FROM-CREDENTIALS_yaml-1"  # ordem dos ficheiros
    assert hp.find_api_key({"OPENROUTER_API_KEY": "from-env"}, home) == "from-env"
    both = {"OPENROUTER_API_KEY": "second", "SOD_OPENROUTER_KEY": "first"}
    assert hp.find_api_key(both, home) == "first"
    # um ficheiro ilegível (aqui: um diretório) nunca rebenta a procura
    (home / ".dsh" / ".credentials.yaml").unlink()
    (home / ".dsh" / ".credentials.yaml").mkdir()
    assert hp.find_api_key({}, home) == "sk-or-v1-FROM-secrets"
    (home / ".secrets").write_bytes(b"\xff\xfe\x00 not utf8 sk-or-v1-")
    assert hp.find_api_key({}, home) is None


def test_make_client_is_none_without_a_key_and_a_deepseek_client_with_one(hp):
    assert hp.make_client() is None
    client = hp.make_client(FAKE_KEY)
    assert type(client).__name__ == "DeepSeekLLM" and client.is_configured()
    assert FAKE_KEY not in repr(client)


def test_snapshot_helper_tolerates_every_bridge_failure(hp, monkeypatch):
    def run_with(outcome):
        def fake(argv, **kwargs):
            if isinstance(outcome, BaseException):
                raise outcome
            return subprocess.CompletedProcess(argv, 0, stdout=outcome, stderr="")
        monkeypatch.setattr(subprocess, "run", fake)
        return hp.snapshot("/df")

    assert run_with(json.dumps({"adventurer": {"name": "x"}})) == {"adventurer": {"name": "x"}}
    assert run_with("") == {}
    assert run_with("not json at all") == {}
    assert run_with("[1, 2, 3]") == {}  # JSON válido mas não um objeto
    assert run_with(subprocess.TimeoutExpired("bridge", 120)) == {}
    assert run_with(FileNotFoundError("python")) == {}


def test_knowledge_context_is_empty_when_coala_is_missing_or_fails(hp, monkeypatch, tmp_path):
    fresh = load("hunt_plan_knowledge_probe")  # o fixture substituiu o do módulo ``hp``
    monkeypatch.setattr(fresh, "COALA", tmp_path / "missing" / "coala.py")
    assert fresh.knowledge_context() == ""
    present = tmp_path / "coala.py"
    present.write_text("# fake", encoding="utf-8")
    monkeypatch.setattr(fresh, "COALA", present)
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: (_ for _ in ()).throw(
        subprocess.TimeoutExpired("coala", 60)))
    assert fresh.knowledge_context() == ""
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 0, stdout="x" * 5000, stderr=""))
    assert len(fresh.knowledge_context()) == 1500  # limitado: entra no prompt


def test_legacy_call_llm_returns_the_raw_dict_or_none(hp):
    digest = hp.snapshot_digest(snapshot())
    assert hp.call_llm(digest, "", "") is None  # sem chave
    client = FakeClient("noise before " + json.dumps(good_plan()) + " noise after")
    raw = hp.call_llm(digest, "project knowledge", "stuck", client=client)
    assert raw["where_am_i"].startswith("Mortalplan")
    assert client.calls[0]["reasoning"] == {"effort": "medium"}
    assert json.loads(client.calls[0]["user"])["previous_attempt_feedback"] == "stuck"
    assert hp.call_llm(digest, "", "", client=FakeClient("no json")) is None
    assert hp.call_llm(digest, "", "", client=FakeClient(RuntimeError("down"))) is None
