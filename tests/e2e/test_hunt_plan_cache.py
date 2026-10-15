"""Cache de planos no CLI ``hunt_plan.py`` + métrica por sessão (Passo 19).

A métrica reportável — custo poupado + hit-rate — é registada **por sessão**
(``CacheSession``) no mesmo padrão onde o projeto regista gastos
(``campaign.spend.SpendLedger``), e o CLI devolve-a em ``planning.cache``.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "e2e" / "hunt_plan.py"

if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from system_one_dwarf.agent.plan_cache import PlanCache  # noqa: E402
from system_one_dwarf.campaign.spend import SpendLedger  # noqa: E402
from system_one_dwarf.cache import CacheSession  # noqa: E402

FAKE_KEY = "sk-or-v1-test-key-not-a-real-secret"


def load(name, path=SCRIPT):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("test_hunt_plan_cache must stay offline")

    for target, name in ((socket, "socket"), (socket, "create_connection"),
                         (subprocess, "run"), (subprocess, "Popen")):
        monkeypatch.setattr(target, name, refuse)


@pytest.fixture
def hp(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("SOD_OPENROUTER_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    module = load("hunt_plan_cache_under_test")
    monkeypatch.setattr(module, "knowledge_context", lambda limit=3: "")
    return module


def snapshot(x=40, y=50):
    return {
        "adventurer": {"name": "Usmen", "pos": {"x": x, "y": y, "z": 5},
                       "health": {"wounds": 0}, "kills": 0},
        "metrics": {"location": {"x": x, "y": y, "z": 5, "site": "Mortalplan",
                                 "site_type": "town", "region": "shrubland temperate",
                                 "tile": "ConstructedFloor",
                                 "surroundings": "outdoors, shrubland temperate"},
                    "units_nearby": {"units": []},
                    "threats": {"count": 0, "level": "none", "hostiles": []},
                    "game_clock": {"tick": 1200}},
    }


def good_plan(x=40, y=50):
    return {
        "where_am_i": f"Mortalplan at {x},{y}",
        "animal_zones": [{"zone": "south scrub", "why": "game avoids floors",
                          "waypoint": [x + 15, y + 15]}],
        "route_steps": [{"action": "goto", "target": [x + 15, y + 15], "note": "leave town"},
                        {"action": "wait", "target": "short", "note": "scan"}],
        "opportunistic_rule": "Attack any non-sapient wild creature on the way; "
                              "never attack humans, dwarves, elves or goblins.",
        "success_conditions": [{"kind": "kill_confirmed"}],
        "assumptions": ["open shrubland"],
    }


class FakeClient:
    """Estilo ``DeepSeekLLM``: uma resposta por chamada, custo real por chamada."""

    def __init__(self, *answers, cost_usd=0.05):
        from system_one_dwarf.agent.llm import LLMUsage

        self.answers = list(answers)
        self.usage = LLMUsage(100, 50, cost_usd)
        self.calls = []

    def complete(self, system, user, *, reasoning="auto", max_tokens=None, temperature=None,
                 purpose=None):
        from system_one_dwarf.agent.llm import LLMResult

        self.calls.append({"user": user, "purpose": purpose})
        answer = self.answers.pop(0) if self.answers else json.dumps(good_plan())
        return LLMResult(text=answer if isinstance(answer, str) else json.dumps(answer),
                         usage=self.usage)


def test_the_second_build_is_served_from_the_cache_with_zero_calls(hp):
    client = FakeClient(json.dumps(good_plan()))
    cache = PlanCache()

    first = hp.build_plan(snap=snapshot(), llm=client, fauna_search=lambda q, limit=3: [],
                          cache=cache)
    again = hp.build_plan(snap=snapshot(), llm=client, fauna_search=lambda q, limit=3: [],
                          cache=cache)

    assert len(client.calls) == 1                  # a 2.ª não custou tokens
    assert "cached" not in first["planning"]       # 1.ª é geração real
    assert again["planning"]["cached"] is True
    assert again["planning"]["attempts"] == 0
    assert again["where_am_i"] == first["where_am_i"]


def test_the_session_metric_registers_cost_and_hit_rate_per_session(hp, tmp_path):
    client = FakeClient(json.dumps(good_plan()), cost_usd=0.05)
    cache = PlanCache()
    session = CacheSession("sessão-e2e")
    session.bind(cache.name, cache)

    hp.build_plan(snap=snapshot(), llm=client, fauna_search=lambda q, limit=3: [], cache=cache)
    hp.build_plan(snap=snapshot(), llm=client, fauna_search=lambda q, limit=3: [], cache=cache)

    report = session.report()
    assert report["session"] == "sessão-e2e"
    assert report["hits"] == 1 and report["misses"] == 1
    assert report["hit_rate"] == 0.5
    assert report["saved_usd"] == 0.05           # o custo da geração evitada

    # Onde o projeto já regista gastos: mesmo padrão (SpendLedger.record).
    ledger = SpendLedger(tmp_path / "gastos.sqlite")
    rows = session.record_to(ledger)
    assert rows[0]["provider"] == "cache" and rows[0]["cost_usd"] == 0.0
    assert "hit_rate=0.500000" in rows[0]["reason"]
    assert "saved_usd=0.050000" in rows[0]["reason"]
    assert ledger.total_usd() == 0.0


def test_the_cli_reports_the_cache_metric_in_the_plan(hp, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SOD_OPENROUTER_KEY", FAKE_KEY)
    client = FakeClient(json.dumps(good_plan()))
    monkeypatch.setattr(hp, "make_client", lambda key=None: client)
    monkeypatch.setattr(hp, "snapshot", lambda df_path: snapshot())
    monkeypatch.setattr(hp.planning, "fauna_hints", lambda *a, **k: [])
    out = tmp_path / "plano.json"
    cache_path = tmp_path / "planos-cache.json"

    assert hp.main(["--out", str(out), "--cache", str(cache_path)]) == 0
    first = json.loads(out.read_text(encoding="utf-8"))
    assert first["planning"]["cache"]["misses"] == 1
    assert first["planning"]["cache"]["hits"] == 0

    assert hp.main(["--out", str(out), "--cache", str(cache_path)]) == 0
    second = json.loads(out.read_text(encoding="utf-8"))
    assert second["planning"]["cached"] is True
    assert second["planning"]["cache"]["hits"] == 1     # sessão do 2.º run
    assert second["planning"]["cache"]["hit_rate"] == 1.0
    assert second["planning"]["cache"]["saved_usd"] == 0.05
    assert len(client.calls) == 1                 # o 2.º run do CLI não gastou tokens
    capsys.readouterr()


def test_the_cli_flag_disables_the_cache(hp, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SOD_OPENROUTER_KEY", FAKE_KEY)
    client = FakeClient(json.dumps(good_plan()), json.dumps(good_plan()))
    monkeypatch.setattr(hp, "make_client", lambda key=None: client)
    monkeypatch.setattr(hp, "snapshot", lambda df_path: snapshot())
    monkeypatch.setattr(hp.planning, "fauna_hints", lambda *a, **k: [])
    out = tmp_path / "plano.json"

    for _ in range(2):
        assert hp.main(["--out", str(out), "--no-cache"]) == 0

    plan = json.loads(out.read_text(encoding="utf-8"))
    assert "cache" not in plan["planning"]         # sem cache: sem métrica
    assert plan["planning"].get("cached") is None
    assert len(client.calls) == 2                  # gerou sempre de novo
    capsys.readouterr()
