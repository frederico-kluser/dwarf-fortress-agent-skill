"""``agent.plan_cache`` + integração no ``HuntPlanner``: hit, miss, invalidação.

Invalidação CONSERVADORA (Passo 19): mudança de zona, de objetivo, de ameaça ou
de HP invalida a entrada — e um plano stale é pior do que re-gerar, por isso a
propriedade testada é **zero planos stale servidos**: um hit só aparece quando
zona + objetivo + ameaças + HP são exatamente os do contexto pedido.
"""

from __future__ import annotations

import copy
import json
import random

import pytest

from system_one_dwarf.agent import planning
from system_one_dwarf.agent.plan_cache import (
    PlanCache,
    plan_key,
    plan_signature,
    signature_parts,
)


# ------------------------------------------------------------------- helpers


def state(x=40, y=50, *, region="shrubland temperate", threats=None, health=None,
          clock=1200, kills=0):
    """Snapshot mínimo no formato que ``planning.build_digest`` normaliza."""
    return {
        "adventurer": {"name": "Usmen", "pos": {"x": x, "y": y, "z": 5},
                       "health": {"wounds": 0, "status": "healthy"} if health is None else health,
                       "kills": kills},
        "metrics": {
            "location": {"x": x, "y": y, "z": 5, "site": "Mortalplan", "site_type": "town",
                         "region": region, "tile": "ConstructedFloor",
                         "surroundings": f"outdoors, {region}"},
            "units_nearby": {"units": []},
            "threats": {"count": 0, "level": "none", "hostiles": []} if threats is None else threats,
            "game_clock": {"tick": clock},
        },
    }


def digest_of(snap) -> dict:
    return planning.build_digest(snap)


def plan_dict(x=40, y=50, marker=""):
    """Plano válido com uma marca identificadora do contexto em que foi gerado."""
    return {
        "where_am_i": marker or f"Mortalplan at {x},{y}",
        "animal_zones": [{"zone": "south scrub", "why": "game avoids floors",
                          "waypoint": [x + 15, y + 15]}],
        "route_steps": [{"action": "goto", "target": [x + 15, y + 15], "note": "leave town"},
                        {"action": "wait", "target": "short", "note": "scan"}],
        "opportunistic_rule": "Attack non-sapient wild creatures on the way; never sapients.",
        "success_conditions": [{"kind": "kill_confirmed"}],
        "assumptions": ["open shrubland"],
    }


def no_kb(query, limit=3):
    return []


class FakeLLM:
    """``DeepSeekLLM``-like: respostas em fila, custo fixo por chamada."""

    def __init__(self, *answers, cost_usd=0.5):
        self.answers = list(answers)
        self.cost_usd = cost_usd
        self.calls = []

    def complete(self, system, user, *, reasoning="auto", max_tokens=None, temperature=None,
                 purpose=None):
        from system_one_dwarf.agent.llm import LLMResult, LLMUsage

        self.calls.append({"user": user, "reasoning": reasoning, "purpose": purpose})
        answer = self.answers.pop(0) if self.answers else json.dumps(plan_dict())
        return LLMResult(text=answer if isinstance(answer, str) else json.dumps(answer),
                         usage=LLMUsage(100, 50, self.cost_usd))


# ----------------------------------------------------------- chave e assinatura


def test_the_key_is_hash_of_normalised_state_plus_objective() -> None:
    base = digest_of(state())

    assert plan_key(base, "hunt") == plan_key(copy.deepcopy(base), "hunt")
    assert plan_key(base, "hunt") != plan_key(base, "explore")
    assert plan_key(base, "hunt") != plan_key(digest_of(state(region="grassland")), "hunt")
    assert plan_key(base, "hunt") != plan_key(digest_of(state(x=41)), "hunt")


def test_the_volatile_game_clock_is_not_part_of_the_key() -> None:
    """O relógio muda sem progresso real — o mesmo caso "nada mudou" (H6)."""
    early = digest_of(state(clock=1000))
    late = digest_of(state(clock=9999))

    assert plan_key(early, "hunt") == plan_key(late, "hunt")


def test_the_signature_carries_the_four_conservative_axes() -> None:
    parts = signature_parts(digest_of(state()), "hunt")
    assert set(parts) == {"zone", "goal", "threats", "health"}


# --------------------------------------------------------------- hit e miss


def test_hit_serves_the_stored_plan_and_counts_the_saved_cost() -> None:
    cache = PlanCache()
    snap = state()
    cache.put_plan(digest_of(snap), "hunt", plan_dict(), cost_usd=0.5)

    hit = cache.get_plan(digest_of(snap), "hunt")

    assert hit is not None and hit["where_am_i"] == "Mortalplan at 40,50"
    stats = cache.stats()
    assert stats["hits"] == 1 and stats["misses"] == 0
    assert stats["saved_usd"] == 0.5


def test_miss_when_nothing_was_stored() -> None:
    cache = PlanCache()
    assert cache.get_plan(digest_of(state()), "hunt") is None
    assert cache.stats()["misses"] == 1


@pytest.mark.parametrize("axis,other", [
    ("zone", state(region="grassland")),
    ("zone", state(x=45)),
    ("objective", None),
    ("threats", state(threats={"count": 2, "level": "great_danger",
                               "hostiles": [{"name": "wolf"}]})),
    ("hp", state(health={"wounds": 3, "status": "badly wounded"})),
])
def test_each_conservative_axis_change_is_a_miss(axis, other) -> None:
    cache = PlanCache()
    cache.put_plan(digest_of(state()), "hunt", plan_dict())

    goal = "explore" if axis == "objective" else "hunt"
    miss = cache.get_plan(digest_of(other if other is not None else state()), goal)

    assert miss is None
    assert cache.stats()["misses"] == 1


# ------------------------------------------------------ invalidação explícita


def test_zone_change_invalidates_the_entry() -> None:
    cache = PlanCache()
    cache.put_plan(digest_of(state()), "hunt", plan_dict())

    dropped = cache.invalidate_zone(digest_of(state(region="grassland")))

    assert dropped == 1 and len(cache) == 0


def test_objective_change_invalidates_the_entry() -> None:
    cache = PlanCache()
    cache.put_plan(digest_of(state()), "hunt", plan_dict())

    dropped = cache.invalidate_objective("explore")

    assert dropped == 1 and len(cache) == 0
    assert cache.get_plan(digest_of(state()), "hunt") is None  # entrada já não existe


def test_threat_change_invalidates_the_entry() -> None:
    cache = PlanCache()
    cache.put_plan(digest_of(state()), "hunt", plan_dict())

    dropped = cache.invalidate_threats(
        digest_of(state(threats={"count": 1, "level": "danger", "hostiles": [{"name": "wolf"}]})))

    assert dropped == 1 and len(cache) == 0


def test_hp_change_invalidates_the_entry() -> None:
    cache = PlanCache()
    cache.put_plan(digest_of(state()), "hunt", plan_dict())

    dropped = cache.invalidate_health(
        digest_of(state(health={"wounds": 2, "status": "injured"})))

    assert dropped == 1 and len(cache) == 0


def test_sweep_keeps_only_the_current_context() -> None:
    cache = PlanCache()
    cache.put_plan(digest_of(state()), "hunt", plan_dict())
    cache.put_plan(digest_of(state(x=60)), "hunt", plan_dict(x=60))

    kept = cache.sweep(digest_of(state()), "hunt")

    assert kept == 1 and len(cache) == 1
    assert cache.get_plan(digest_of(state()), "hunt") is not None


# ------------------------------------------------------------- zero stale


def test_zero_stale_plans_are_ever_served() -> None:
    """Propriedade: um hit servido tem SEMPRE zona+objetivo+ameaças+HP do pedido.

    Contextos originais servem (com o plano certo); qualquer mutação de zona,
    objetivo, ameaça ou HP nunca recebe plano — zero planos stale.
    """
    rng = random.Random(19)
    cache = PlanCache(maxsize=64)
    goals = ("hunt", "explore")

    def mutate_zone(snap):
        loc = {**snap["metrics"]["location"], "region": "desert"}
        return dict(snap, metrics={**snap["metrics"], "location": loc})

    def mutate_pos(snap):
        loc = {**snap["metrics"]["location"], "x": 0, "y": 0}
        adv = {**snap["adventurer"], "pos": {"x": 0, "y": 0, "z": 5}}
        return dict(snap, metrics={**snap["metrics"], "location": loc}, adventurer=adv)

    def mutate_threats(snap):
        threats = {"count": 9, "level": "great_danger", "hostiles": [{"name": "dragon"}]}
        return dict(snap, metrics={**snap["metrics"], "threats": threats})

    def mutate_hp(snap):
        adv = {**snap["adventurer"], "health": {"wounds": 9, "status": "unconscious"}}
        return dict(snap, adventurer=adv)

    requests = []
    for i in range(12):
        snap = state(x=20 + i, region="grassland" if i % 2 else "shrubland temperate",
                     threats={"count": i % 3, "level": ["none", "danger", "great_danger"][i % 3],
                              "hostiles": []},
                     health={"wounds": i % 4, "status": "healthy"})
        goal = goals[i % 2]
        marker = f"plan:{i}:{goal}"
        cache.put_plan(digest_of(snap), goal, plan_dict(marker=marker))
        other = "explore" if goal == "hunt" else "hunt"
        requests.extend([
            (snap, goal, marker),                 # contexto original → serve
            (mutate_zone(snap), goal, None),      # zona → nunca serve
            (mutate_pos(snap), goal, None),       # posição/zona → nunca serve
            (snap, other, None),                  # objetivo → nunca serve
            (mutate_threats(snap), goal, None),   # ameaça → nunca serve
            (mutate_hp(snap), goal, None),        # HP → nunca serve
        ])

    rng.shuffle(requests)
    served = 0
    for snap, goal, marker in requests:
        got = cache.get_plan(digest_of(snap), goal)
        if marker is None:
            assert got is None, f"plano stale servido para contexto mutado ({goal})"
            continue
        assert got is not None and got["where_am_i"] == marker
        entry = cache.peek(plan_key(digest_of(snap), goal))
        assert entry["signature"] == plan_signature(digest_of(snap), goal)
        served += 1

    assert served == 12
    # Auditoria + sweep: depois do sweep não sobra nenhuma entrada divergente.
    assert cache.sweep(digest_of(state()), "hunt") >= 0
    assert cache.stale_entries(digest_of(state()), "hunt") == []


def test_a_corrupt_mirror_entry_is_never_served(tmp_path) -> None:
    path = tmp_path / "plans.json"
    cache = PlanCache(path=path)
    cache.put_plan(digest_of(state()), "hunt", plan_dict())
    # Espelho editado à mão: chave certa, assinatura errada → nunca servido.
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["entries"][0]["signature"]["zone"] = "outra-zona"
    path.write_text(json.dumps(payload), encoding="utf-8")

    reloaded = PlanCache(path=path)
    assert reloaded.get_plan(digest_of(state()), "hunt") is None
    assert len(reloaded) == 0  # descartada, não só ignorada


# ------------------------------------------------------- HuntPlanner em loop


def test_the_planner_serves_the_cached_plan_with_zero_llm_calls() -> None:
    llm = FakeLLM(json.dumps(plan_dict()))
    planner = planning.HuntPlanner(llm, fauna_search=no_kb, cache=PlanCache())

    first = planner.plan(state())
    again = planner.plan(state())

    assert first.cached is False and first.attempts == 1
    assert again.cached is True and again.attempts == 0
    assert len(llm.calls) == 1                 # a 2.ª não custou nada
    assert again.summary()["cached"] is True
    assert again.plan.to_dict()["where_am_i"] == first.plan.to_dict()["where_am_i"]


def test_the_planner_replans_when_any_conservative_axis_changes() -> None:
    llm = FakeLLM(json.dumps(plan_dict()), json.dumps(plan_dict(x=45)),
                  json.dumps(plan_dict()), json.dumps(plan_dict()))
    cache = PlanCache()
    planner = planning.HuntPlanner(llm, fauna_search=no_kb, cache=cache)

    planner.plan(state())
    planner.plan(state(region="grassland"))       # zona
    planner.plan(state(), goal="explore")         # objetivo
    planner.plan(state(threats={"count": 1, "level": "danger", "hostiles": []}))  # ameaça
    planner.plan(state(health={"wounds": 2, "status": "injured"}))                # HP

    assert len(llm.calls) == 5                    # nenhum destes foi servido em cache


def test_clock_ticks_alone_still_hit_the_cache() -> None:
    llm = FakeLLM(json.dumps(plan_dict()))
    planner = planning.HuntPlanner(llm, fauna_search=no_kb, cache=PlanCache())

    planner.plan(state(clock=1000))
    again = planner.plan(state(clock=9000))       # "nada mudou" (relógio não conta)

    assert again.cached is True and len(llm.calls) == 1


def test_a_stale_cached_plan_fails_revalidation_and_is_regenerated() -> None:
    cache = PlanCache()
    poison = plan_dict()
    poison["route_steps"] = [{"action": "goto", "target": [5000, 5000], "note": "longe demais"}]
    cache.put_plan(digest_of(state()), "hunt", poison)

    llm = FakeLLM(json.dumps(plan_dict()))
    planner = planning.HuntPlanner(llm, fauna_search=no_kb, cache=cache)
    result = planner.plan(state())

    assert result.cached is False                 # o plano stale NUNCA é servido
    assert len(llm.calls) == 1                    # re-gerado
    assert result.plan.route_steps[0].target == (55, 65)
    assert cache.get_plan(digest_of(state()), "hunt") is not None  # o novo entrou


def test_fallback_plans_are_never_cached() -> None:
    cache = PlanCache()
    planner = planning.HuntPlanner(None, fauna_search=no_kb, cache=cache)  # offline

    planner.plan(state())
    planner.plan(state())

    assert len(cache) == 0


def test_the_saved_cost_of_a_hit_is_the_generation_cost() -> None:
    llm = FakeLLM(json.dumps(plan_dict()), cost_usd=0.5)
    cache = PlanCache()
    planner = planning.HuntPlanner(llm, fauna_search=no_kb, cache=cache)

    planner.plan(state())
    planner.plan(state())                         # hit: poupa os 0.5 da geração

    assert cache.stats()["saved_usd"] == 0.5
