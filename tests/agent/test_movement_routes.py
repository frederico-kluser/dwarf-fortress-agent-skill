"""Movimento como macro-ação: rotas por legs (mudança 04, ARQUITETURA.md §8).

O decisor escolhe destino + urgência; o código decompõe em legs, valida cada
coordenada/direção antes de a pôr no DSL e avalia cada leg com um snapshot novo.
Estes testes fixam o contrato do módulo ``agent/movement.py`` (PURO: sem I/O):

* **geometria** — 8 rumos, modo destino/rumo, limites, distâncias nulas, escadaria,
  desvio de hostis, orçamento ``stride × max_legs`` NUNCA excedido;
* **urgência** — índice/texto da régua do JEV-A → ``(stride, max_legs, reassess)``;
* **serialização/DSL** — ida e volta exata, lixo tolerado, injeção recusada, DSL
  validado pelo ``parse_batch`` REAL da ponte;
* **veredictos** — matriz continue/arrived/blocked/abort;
* **integração** — params REAIS do gerador de candidatos e um corredor de rota que
  usa estados REAIS (``GameState``) como o executor fará.
"""
from __future__ import annotations

import ast
import dataclasses
import importlib.util
import inspect
import itertools
import json
import math
import pathlib
import sys
from types import SimpleNamespace

import pytest

from system_one_dwarf.agent import candidates, executor, movement as mv
from system_one_dwarf.agent.movement import (
    COMPASS,
    DIRECTION_VECTORS,
    LegObservation,
    LegVerdict,
    RouteLeg,
    RoutePlan,
    UrgencyProfile,
    Waypoint,
    as_xy,
    build_route,
    compass_bearing,
    evaluate_leg,
    evaluate_states,
    next_unvisited_direction,
    normalise_compass,
    observe_state,
    plan_waypoints,
    route_summary,
    urgency_level,
    urgency_profile,
)
from system_one_dwarf.agent.stuck import StuckDetector, state_fingerprint
from system_one_dwarf.jev.fleet import DEFAULT_URGENCY_LEVELS

from .fakes_loop import animal, friendly, game_state, hostile

ORIGIN = (50, 50)


# ---------------------------------------------------------------- utilitários


def cheb(a, b) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def leg_sizes(origin, points) -> list[int]:
    chain = [origin, *points]
    return [cheb(chain[i], chain[i + 1]) for i in range(len(points))]


def half_away(numerator: int, denominator: int) -> int:
    """Arredondamento independente (meio afasta-se de zero) para amostrar segmentos."""
    quotient, remainder = divmod(abs(numerator), denominator)
    quotient += 1 if 2 * remainder >= denominator else 0
    return quotient if numerator >= 0 else -quotient


def segment_tiles(a, b):
    """Tiles de ``a`` (exclusive) a ``b`` (inclusive) numa reta de 8 direções."""
    count = cheb(a, b)
    return [
        (a[0] + half_away((b[0] - a[0]) * i, count), a[1] + half_away((b[1] - a[1]) * i, count))
        for i in range(1, count + 1)
    ]


def bridge_parse(dsl: str):
    """``parse_batch`` da ponte REAL (valida que o DSL é DSL de verdade)."""
    path = pathlib.Path(executor.DEFAULT_BRIDGE_PATH)
    if not path.is_file():  # pragma: no cover - o repositório tem a ponte
        pytest.skip("dfhack/df_bridge.py not available")
    name = "sod_test_df_bridge"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module.parse_batch(dsl)


def route(params=None, origin=ORIGIN, urgency=None, step=0, **kwargs) -> RoutePlan:
    plan = build_route(params if params is not None else {"direction": "e"}, origin, urgency, step, **kwargs)
    assert plan is not None
    return plan


# ======================================================== A. geometria: waypoints


@pytest.mark.parametrize("name,vec", sorted(DIRECTION_VECTORS.items()))
def test_direction_mode_walks_each_of_the_eight_compass_vectors(name, vec):
    points = plan_waypoints((100, 100), direction=name, distance=12, stride=4, max_legs=6)
    assert points == [(100 + vec[0] * 4 * k, 100 + vec[1] * 4 * k) for k in (1, 2, 3)]
    assert leg_sizes((100, 100), points) == [4, 4, 4]


@pytest.mark.parametrize("text", ["NE", " ne ", "north-east", "NorthEast", "ne +2z", "north_east"])
def test_direction_text_variants_normalise_to_the_dsl_vocabulary(text):
    assert normalise_compass(text) == "ne"
    assert plan_waypoints((100, 100), direction=text, distance=8, stride=4) == [(104, 96), (108, 92)]


def test_goal_mode_reaches_the_goal_in_stride_sized_legs():
    points = plan_waypoints((10, 10), (40, 16), stride=8, max_legs=6)
    assert points == [(18, 12), (26, 13), (34, 15), (40, 16)]  # golden: reta + arredondamento fixo
    assert leg_sizes((10, 10), points) == [8, 8, 8, 6]
    assert points[-1] == (40, 16)


def test_goal_mode_works_in_every_quadrant_and_ends_within_one_tile_of_the_goal():
    for goal in [(70, 50), (30, 50), (50, 20), (50, 80), (75, 75), (25, 25), (75, 25), (25, 75)]:
        points = plan_waypoints(ORIGIN, goal, stride=6, max_legs=12)
        assert cheb(points[-1], goal) <= mv.ARRIVAL_TOLERANCE   # "arrived" da ponte
        assert all(size <= 6 for size in leg_sizes(ORIGIN, points))
        # progresso estritamente decrescente até ao destino
        chain = [cheb(p, goal) for p in [ORIGIN, *points]]
        assert chain == sorted(chain, reverse=True) and len(set(chain)) == len(chain)


def test_budget_caps_the_route_before_the_goal():
    points = plan_waypoints((10, 10), (500, 10), stride=6, max_legs=4)
    assert points == [(16, 10), (22, 10), (28, 10), (34, 10)]  # 24 tiles: nunca mais


def test_direction_mode_defaults_to_the_whole_budget():
    assert plan_waypoints((10, 10), direction="e", stride=5, max_legs=3) == [(15, 10), (20, 10), (25, 10)]


def test_distance_limits_the_walk_and_the_last_leg_is_short():
    assert plan_waypoints((50, 50), direction="s", distance=10, stride=4) == [(50, 54), (50, 58), (50, 60)]
    # distance maior que o orçamento é cortada pelo orçamento
    assert plan_waypoints((50, 50), direction="s", distance=999, stride=4, max_legs=2) == [(50, 54), (50, 58)]


def test_a_final_leg_of_one_tile_is_dropped_because_goto_would_not_move():
    # a ponte dá "arrived" a <= 1 tile: um leg de 1 tile é no-op e nunca é planeado
    assert plan_waypoints((50, 50), direction="s", distance=9, stride=4) == [(50, 54), (50, 58)]
    assert plan_waypoints((10, 10), (10, 31), stride=10) == [(10, 20), (10, 30)]  # 1 tile do alvo


@pytest.mark.parametrize("distance", [0, 1, -5, "far", 1.5, float("nan"), [], {}])
def test_tiny_zero_or_garbage_distance_yields_no_route(distance):
    assert plan_waypoints(ORIGIN, direction="n", distance=distance) == []


def test_goal_on_or_next_to_the_origin_yields_no_route():
    assert plan_waypoints(ORIGIN, ORIGIN) == []
    assert plan_waypoints(ORIGIN, (51, 51)) == []          # adjacente: a ponte já dá "arrived"
    assert plan_waypoints(ORIGIN, (52, 50)) == [(52, 50)]  # 2 tiles: um leg


@pytest.mark.parametrize("origin", [None, "50,50", (1,), [], {}, {"x": 5}, {"x": "a", "y": 2},
                                    (True, 2), (1.5, 2), (math.nan, 1), (10**9, 5), object()])
def test_unknown_or_invalid_origin_is_neutral(origin):
    assert plan_waypoints(origin, direction="n", distance=10) == []
    assert plan_waypoints(origin, (60, 60)) == []


@pytest.mark.parametrize("direction", ["up", "down", "wait", "here", "", None, "n; key A_TALK", 7, "ne;ls"])
def test_non_planar_or_garbage_direction_is_neutral(direction):
    assert plan_waypoints(ORIGIN, direction=direction, distance=10) == []


def test_invalid_goal_does_not_fall_back_to_the_direction():
    # um destino dado e inválido nunca vira "andar para o rumo" em silêncio
    assert plan_waypoints(ORIGIN, "somewhere", direction="n", distance=10) == []
    assert plan_waypoints(ORIGIN, (1,), direction="n", distance=10) == []


def test_goal_mode_ignores_direction_and_staircase():
    base = plan_waypoints(ORIGIN, (80, 50), stride=10)
    assert plan_waypoints(ORIGIN, (80, 50), stride=10, direction="n", staircase=True) == base
    assert plan_waypoints(ORIGIN, (80, 50), stride=10, distance=15) == [(60, 50), (65, 50)]


def test_bounds_clip_waypoints_and_drop_stalled_legs():
    # 5 tiles até à parede oeste; depois não há progresso
    assert plan_waypoints((5, 5), direction="w", distance=40, bounds=(0, 0, 100, 100)) == [(0, 5)]
    points = plan_waypoints((95, 50), direction="e", distance=40, stride=8, bounds=(0, 0, 100, 100))
    assert points == [(100, 50)]
    for x, y in plan_waypoints((50, 50), direction="ne", distance=200, stride=8, max_legs=12,
                               bounds=(40, 40, 80, 60)):
        assert 40 <= x <= 80 and 40 <= y <= 60


def test_bounds_clamp_the_goal_and_normalise_swapped_corners():
    assert plan_waypoints((10, 10), (500, 10), stride=10, max_legs=12, bounds=(0, 0, 40, 40))[-1] == (40, 10)
    assert plan_waypoints((50, 50), direction="n", distance=10, bounds=(100, 100, 0, 0)) == \
        plan_waypoints((50, 50), direction="n", distance=10, bounds=(0, 0, 100, 100))
    assert plan_waypoints((50, 50), direction="n", distance=10, bounds={"min_x": 0, "min_y": 0,
                                                                         "max_x": 100, "max_y": 100}) == \
        plan_waypoints((50, 50), direction="n", distance=10)


def test_default_bounds_never_leave_the_non_negative_world():
    for direction in COMPASS:
        for x, y in plan_waypoints((3, 3), direction=direction, distance=60, stride=10):
            assert x >= 0 and y >= 0


def test_origin_outside_the_bounds_is_rejected_not_clamped():
    assert plan_waypoints((200, 200), direction="n", distance=10, bounds=(0, 0, 100, 100)) == []
    assert plan_waypoints((-5, 3), direction="e", distance=10) == []  # DF não tem x/y negativos


def test_garbage_bounds_fall_back_to_the_default_world():
    expected = plan_waypoints((50, 50), direction="n", distance=10)
    for bounds in ("a", (1, 2, 3), (1, 2, 3, "x"), [None] * 4, 5):
        assert plan_waypoints((50, 50), direction="n", distance=10, bounds=bounds) == expected


def test_staircase_alternates_the_heading_and_the_side():
    points = plan_waypoints((50, 50), direction="n", stride=4, max_legs=4, staircase=True)
    assert points == [(50, 46), (54, 46), (54, 42), (58, 42)]  # n, e, n, e
    assert leg_sizes((50, 50), points) == [4, 4, 4, 4]
    west = plan_waypoints((50, 50), direction="n", stride=4, max_legs=4, staircase=True, side="w")
    assert west == [(50, 46), (46, 46), (46, 42), (42, 42)]


@pytest.mark.parametrize("name", sorted(DIRECTION_VECTORS))
def test_staircase_side_matches_the_executor_hunt_sides(name):
    sides = getattr(executor, "_HUNT_SIDES", None)
    if sides is None:  # pragma: no cover - o executor passou a importar o vocabulário
        pytest.skip("executor no longer owns _HUNT_SIDES")
    points = plan_waypoints((100, 100), direction=name, stride=4, max_legs=2, staircase=True)
    side = DIRECTION_VECTORS[sides[name][0]]
    assert points[1] == (points[0][0] + side[0] * 4, points[0][1] + side[1] * 4)


def test_avoid_steers_the_route_around_a_hostile_on_the_line():
    hostile_at = (16, 10)
    points = plan_waypoints((10, 10), (60, 10), stride=8, max_legs=6, avoid=[hostile_at])
    assert points, "um desvio existe e a rota continua"
    chain = [(10, 10), *points]
    for a, b in zip(chain, chain[1:]):
        assert cheb(a, b) <= 8
        assert all(cheb(tile, hostile_at) > 2 for tile in segment_tiles(a, b))
    assert plan_waypoints((10, 10), (60, 10), stride=8, max_legs=6) != points


def test_a_fleeing_adventurer_may_walk_away_from_an_adjacent_hostile_but_not_into_it():
    adjacent = (11, 10)
    away = plan_waypoints((10, 10), direction="w", distance=12, stride=4, max_legs=4, avoid=[adjacent])
    assert away == [(6, 10), (2, 10), (0, 10)]
    assert plan_waypoints((10, 10), direction="e", distance=12, stride=4, max_legs=4,
                          avoid=[adjacent]) == []


def test_avoid_accepts_pairs_mappings_objects_and_ignores_garbage():
    plain = plan_waypoints((10, 10), (60, 10), stride=8, avoid=[(16, 10)])
    assert plan_waypoints((10, 10), (60, 10), stride=8, avoid=(16, 10)) == plain
    assert plan_waypoints((10, 10), (60, 10), stride=8, avoid=[{"x": 16, "y": 10}]) == plain
    assert plan_waypoints((10, 10), (60, 10), stride=8, avoid=[{"pos": {"x": 16, "y": 10}}]) == plain
    assert plan_waypoints((10, 10), (60, 10), stride=8, avoid=[Waypoint(16, 10)]) == plain
    assert plan_waypoints((10, 10), (60, 10), stride=8, avoid=[None, "x", (1,), {}, 5, (16, 10)]) == plain
    clean = plan_waypoints((10, 10), (60, 10), stride=8)
    for junk in (None, "abc", 5, object(), [None], [[]]):
        assert plan_waypoints((10, 10), (60, 10), stride=8, avoid=junk) == clean


def test_avoid_list_is_bounded_even_for_endless_iterators():
    endless = itertools.cycle([(300, 300)])
    assert plan_waypoints((10, 10), (60, 10), stride=8, avoid=endless) == \
        plan_waypoints((10, 10), (60, 10), stride=8)


def test_stride_and_legs_are_clamped_to_sane_limits():
    huge = plan_waypoints((100, 100), direction="e", stride=10**6, max_legs=10**6)
    assert len(huge) <= mv.MAX_LEGS and max(leg_sizes((100, 100), huge)) <= mv.MAX_STRIDE
    tiny = plan_waypoints((100, 100), direction="e", stride=0, max_legs=0)
    assert len(tiny) == 1 and leg_sizes((100, 100), tiny)[0] >= mv.MIN_LEG_TILES
    junk = plan_waypoints((100, 100), direction="e", stride="x", max_legs=None)  # defaults 8 × 6
    assert leg_sizes((100, 100), junk) == [8] * 6


def test_the_route_budget_is_never_exceeded_over_a_grid_of_inputs():
    goals = [None, (10, 10), (50, 50), (53, 50), (90, 14), (500, 500), (0, 0), (51, 51)]
    for stride, legs, goal, direction, avoid in itertools.product(
        (2, 3, 6, 10, 24), (1, 2, 4, 6, 12), goals, (None, "n", "se", "w"),
        ([], [(60, 50)], [(52, 52), (49, 49)]),
    ):
        if goal is None and direction is None:
            continue
        points = plan_waypoints(ORIGIN, goal, direction=direction, stride=stride, max_legs=legs,
                                bounds=(0, 0, 120, 120), avoid=avoid)
        sizes = leg_sizes(ORIGIN, points)
        assert len(points) <= legs
        assert all(mv.MIN_LEG_TILES <= size <= stride for size in sizes), (stride, legs, goal, points)
        assert sum(sizes) <= stride * legs
        assert all(0 <= x <= 120 and 0 <= y <= 120 for x, y in points)
        if goal is None and avoid:
            radius = {a: min(mv.AVOID_RADIUS, cheb(ORIGIN, a)) for a in avoid}
            chain = [ORIGIN, *points]
            for a, b in zip(chain, chain[1:]):
                for tile in segment_tiles(a, b):
                    assert all(cheb(tile, h) > radius[h] for h in avoid)


def test_waypoint_planning_is_deterministic():
    kwargs = dict(stride=7, max_legs=5, avoid=[(60, 50)], bounds=(0, 0, 200, 200))
    runs = [plan_waypoints(ORIGIN, (130, 61), **kwargs) for _ in range(3)]
    assert runs[0] == runs[1] == runs[2] and runs[0]


# ================================================================ B. urgência


@pytest.mark.parametrize("urgency,expected", [
    (0, (10, 6, 2)), (1, (6, 4, 1)), (2, (3, 2, 1)),
    (0.0, (10, 6, 2)), (1.0, (6, 4, 1)), (2.0, (3, 2, 1)),
])
def test_score_index_maps_to_the_documented_profiles(urgency, expected):
    profile = urgency_profile(urgency)
    assert profile == expected
    stride, max_legs, reassess_every = profile  # desempacota como tuplo
    assert (stride, max_legs, reassess_every) == expected
    assert isinstance(profile, UrgencyProfile) and profile.stride == expected[0]


@pytest.mark.parametrize("value,level", [
    (0.49, "safe"), (0.5, "pressing"), (0.99, "pressing"), (1.49, "pressing"),
    (1.5, "critical"), (1.99, "critical"), (-3, "safe"), (9, "critical"), ("2", "critical"),
    ("0.4", "safe"),
])
def test_fractional_scores_round_to_the_nearest_level_ties_going_to_the_more_urgent(value, level):
    assert urgency_level(value) == level


@pytest.mark.parametrize("text,level", [
    ("Safe", "safe"), ("safe", "safe"), ("  SAFE — all quiet", "safe"),
    ("Pressing", "pressing"), ("pressing - soon", "pressing"),
    ("Critical", "critical"), ("CRITICAL: now", "critical"),
])
def test_level_text_prefix_maps_to_the_level(text, level):
    assert urgency_level(text) == level


def test_the_real_jev_ruler_texts_map_to_their_own_index():
    assert len(DEFAULT_URGENCY_LEVELS) == 3
    for index, text in enumerate(DEFAULT_URGENCY_LEVELS):
        assert urgency_level(text) == mv.URGENCY_LEVELS[index]
        assert urgency_profile(text) == urgency_profile(index)


@pytest.mark.parametrize("junk", [None, "", "whenever", "nan", float("nan"), float("inf"), True, False,
                                  [], {}, object(), 10**400])
def test_missing_or_garbage_urgency_uses_the_default_level(junk):
    assert urgency_level(junk) == mv.DEFAULT_URGENCY == "pressing"
    assert urgency_profile(junk) == mv.URGENCY_PROFILES["pressing"]


def test_an_answer_like_object_is_accepted_through_its_value():
    assert urgency_level(SimpleNamespace(value=1.7, confidence=0.8)) == "critical"
    assert urgency_level(SimpleNamespace(value="Safe — calm")) == "safe"
    assert urgency_level(SimpleNamespace(value=None)) == "pressing"
    assert urgency_level(SimpleNamespace()) == "pressing"


def test_profiles_override_and_are_clamped():
    assert urgency_profile(0, profiles={"safe": (12, 5, 3)}) == (12, 5, 3)
    assert urgency_profile(0, profiles={"safe": (10**6, 10**6, 10**6)}) == \
        (mv.MAX_STRIDE, mv.MAX_LEGS, mv.MAX_LEGS)
    assert urgency_profile(0, profiles={"safe": (0, 0, 0)}) == (mv.MIN_STRIDE, 1, 1)
    assert urgency_profile(0, profiles={"safe": "nope"}) == mv.URGENCY_PROFILES["safe"]
    assert urgency_profile(0, profiles={"safe": (1,)}) == mv.URGENCY_PROFILES["safe"]
    assert urgency_profile(0, profiles="junk") == mv.URGENCY_PROFILES["safe"]
    assert urgency_profile(1, profiles={"safe": (12, 5, 3)}) == mv.URGENCY_PROFILES["pressing"]


def test_the_profile_table_is_read_only():
    with pytest.raises(TypeError):
        mv.URGENCY_PROFILES["safe"] = (99, 99, 99)  # type: ignore[index]
    assert tuple(mv.URGENCY_PROFILES) == mv.URGENCY_LEVELS


def test_critical_means_short_legs_and_a_small_budget_per_decision():
    budgets = {level: p.stride * p.max_legs for level, p in mv.URGENCY_PROFILES.items()}
    assert budgets["critical"] < budgets["pressing"] < budgets["safe"]
    assert (budgets["safe"], budgets["pressing"], budgets["critical"]) == (60, 24, 6)


# ============================================== C. serialização, DSL e injeção


def test_to_params_is_json_safe_and_round_trips_exactly():
    plan = route({"x": 80, "y": 50, "z": 3, "destination": "the objective"}, urgency=0, step=7,
                 verb="navigate_to")
    params = plan.to_params()
    assert json.loads(json.dumps(params)) == params           # JSON puro
    assert RoutePlan.from_params(params) == plan              # ida e volta exata
    assert RoutePlan.from_params(json.loads(json.dumps(params))) == plan
    assert RoutePlan.from_params({"route": params, "steps": 5}) == plan  # params da ação
    assert params["legs"][-1]["z"] == 3 and params["legs"][-1]["name"] == "the objective"


@pytest.mark.parametrize("junk", [
    None, "route", 5, 1.5, [], (), {}, {"legs": []}, {"legs": "abc"}, {"legs": [None]},
    {"legs": [{}]}, {"legs": [{"x": 1}]}, {"legs": [[1, 2]]}, {"route": None}, {"route": "x"},
    {"id": "r", "legs": [{"x": 1, "y": 2}] * 99},   # mais legs que o máximo
    {"legs": [{"x": 1, "y": 2}]},                    # falta o id
    {"id": {"a": 1}, "legs": [{"x": 1, "y": 2}]},   # id que não é texto
    {"id": "r", "v": "1", "legs": [{"x": 1, "y": 2}]},
    {"id": "r", "legs": [{"x": 1, "y": 2}], "origin": "x"},
])
def test_from_params_returns_none_for_garbage_and_never_raises(junk):
    assert RoutePlan.from_params(junk) is None


@pytest.mark.parametrize("field,value", [
    ("dir", "n; key A_TALK"), ("dir", "up"), ("dir", "north"), ("dir", 5), ("dir", ["n"]),
    ("x", "10; key A_TALK"), ("x", 1.5), ("x", True), ("x", "abc"), ("x", None), ("x", math.nan),
    ("x", mv.MAX_COORD + 1), ("x", "1 2"), ("y", "#"), ("y", [1]),
    ("steps", -1), ("steps", 10**6), ("steps", "many"), ("steps", 2.5),
])
def test_from_params_rejects_anything_that_could_reach_the_dsl_unvalidated(field, value):
    params = route({"direction": "e"}, verb="explore").to_params()
    params["legs"][0][field] = value
    assert RoutePlan.from_params(params) is None


def test_from_params_validates_the_direction_alias_too():
    base = route({"direction": "e"}, verb="explore").to_params()
    for alias, expected in (("n; key A_TALK", None), ("ne", "ne")):
        params = json.loads(json.dumps(base))
        params["legs"][0].pop("dir")
        params["legs"][0]["direction"] = alias
        plan = RoutePlan.from_params(params)
        assert (plan.legs[0].direction if plan else None) == expected


def test_from_params_accepts_numeric_text_but_stores_plain_ints():
    params = route({"direction": "e"}, verb="explore").to_params()
    params["legs"][0]["x"], params["legs"][0]["y"] = "56", " 50 "
    plan = RoutePlan.from_params(params)
    assert plan is not None
    assert plan.to_dsl(plan.legs[0]) == "goto 56,50"


def test_from_params_revalidates_the_per_decision_budget():
    params = route({"direction": "e"}, urgency=2, verb="explore").to_params()
    assert RoutePlan.from_params(params) is not None
    params["legs"][0]["x"] = 50 + 40        # um leg de 40 tiles com stride 3
    assert RoutePlan.from_params(params) is None
    params = route({"direction": "e"}, urgency=2, verb="explore").to_params()
    params["stride"] = 10**6                 # stride declarado é fixado a MAX_STRIDE
    assert RoutePlan.from_params(params).stride == mv.MAX_STRIDE


def test_from_params_tolerates_garbage_in_the_context_fields():
    params = route({"direction": "e"}, verb="explore").to_params()
    params.update(urgency="whenever", goal="x\x00\n;#" + "y" * 500, reason=["junk"], created_step=-9,
                  reassess_every="many", truncated="yes", unknown_key={"a": 1})
    plan = RoutePlan.from_params(params)
    assert plan is not None
    assert plan.urgency == mv.DEFAULT_URGENCY
    assert ";" not in plan.goal_name and "#" not in plan.goal_name and len(plan.goal_name) <= 60
    assert plan.created_step == 0 and plan.reassess_every == 1 and plan.truncated is False


def test_to_dsl_is_goto_with_validated_integers():
    plan = route({"direction": "e"}, urgency=1, verb="explore")
    assert [plan.to_dsl(leg) for leg in plan.legs] == [
        "goto 56,50", "goto 62,50", "goto 68,50", "goto 74,50",
    ]
    assert plan.to_dsl(2) == "goto 68,50"
    assert plan.to_dsl(plan.legs[0], with_budget=True) == "goto 56,50 maxsteps=18"  # 6 tiles × 3
    for bad in (-1, 99, None, "goto 1,1", 1.5, True, object(), {"x": 1}):
        assert plan.to_dsl(bad) == ""


def test_fallback_dsl_is_a_validated_relative_move_of_the_planned_length():
    plan = route({"direction": "ne"}, urgency=1, verb="explore")
    assert [plan.fallback_dsl(leg) for leg in plan.legs] == ["move ne times=6"] * 4
    assert plan.fallback_dsl(1) == "move ne times=6"
    for leg in plan.legs:
        assert bridge_parse(plan.fallback_dsl(leg)) == [{"op": "move", "dir": "ne", "times": 6}]
    # sem rumo conhecido, sem passos ou fora do intervalo: nada (nunca texto livre)
    no_dir = RouteLeg(0, Waypoint(5, 5), 4, "")
    no_steps = RouteLeg(0, Waypoint(5, 5), 0, "n")
    assert plan.fallback_dsl(no_dir) == "" and plan.fallback_dsl(no_steps) == ""
    for bad in (-1, 99, None, "move n times=3", 1.5, True, object()):
        assert plan.fallback_dsl(bad) == ""
    object.__setattr__(plan.legs[0], "direction", "n; key A_TALK")  # frozen, mas forçado
    assert plan.fallback_dsl(plan.legs[0]) == ""


def test_to_dsl_output_is_valid_bridge_dsl_and_one_step_per_leg():
    plan = route({"x": 90, "y": 62, "destination": "ford"}, urgency=0, verb="navigate_to")
    for leg in plan.legs:
        steps = bridge_parse(plan.to_dsl(leg))
        assert steps == [{"op": "goto", "x": leg.waypoint.x, "y": leg.waypoint.y}]
        budget = bridge_parse(plan.to_dsl(leg, with_budget=True))
        assert budget == [{"op": "goto", "x": leg.waypoint.x, "y": leg.waypoint.y,
                           "maxsteps": plan.leg_budget(leg)}]
    batch = "; ".join(plan.to_dsl(leg) for leg in plan.legs)
    assert [s["op"] for s in bridge_parse(batch)] == ["goto"] * len(plan.legs)


def test_names_from_the_game_never_reach_the_dsl_and_are_sanitised():
    evil = "ford; key A_TALK # \x00\x1b[31m‮ rm -rf\nclick 1,1"
    plan = route({"x": 90, "y": 62, "destination": evil}, urgency=0, verb="navigate_to")
    for leg in plan.legs:
        dsl = plan.to_dsl(leg, with_budget=True)
        assert dsl.startswith("goto ") and ";" not in dsl and "#" not in dsl and "\n" not in dsl
        assert "A_TALK" not in dsl
    name = plan.goal_name
    assert name and ";" not in name and "#" not in name and "\n" not in name
    assert not any(ord(char) < 32 or char == "‮" for char in name) and len(name) <= 60
    summary = route_summary(plan)
    assert ";" not in summary and "\x00" not in summary and len(summary) < 160


@pytest.mark.parametrize("x,y", [
    ("10; key A_TALK", 5), (1.5, 2), (True, 2), (None, 2), ("a", "b"), (mv.MAX_COORD + 1, 0),
    (0, -mv.MAX_COORD - 1), (math.inf, 0), ([1], 2),
])
def test_waypoint_constructor_refuses_what_could_not_be_a_goto(x, y):
    with pytest.raises(ValueError):
        Waypoint(x, y)


def test_waypoint_accepts_the_boundary_and_numeric_text():
    assert Waypoint(mv.MAX_COORD, -mv.MAX_COORD).xy == (mv.MAX_COORD, -mv.MAX_COORD)
    assert Waypoint("12", 5.0).xy == (12, 5)
    with pytest.raises(ValueError):
        Waypoint(1, 2, z="up; key A_TALK")


def test_leg_and_plan_constructors_refuse_unvalidated_text():
    waypoint = Waypoint(10, 10)
    for direction in ("n; key A_TALK", "up", "wait", "north", 5):
        with pytest.raises(ValueError):
            RouteLeg(0, waypoint, 3, direction)
    for index, waypoint_arg, steps in [(-1, waypoint, 1), (0, (10, 10), 1), (0, waypoint, -1),
                                       (0, waypoint, mv.MAX_PLAN_STEPS + 1), ("a", waypoint, 1)]:
        with pytest.raises(ValueError):
            RouteLeg(index, waypoint_arg, steps, "n")
    leg = RouteLeg(0, waypoint, 3, "N")
    assert leg.direction == "n"          # normalizado
    with pytest.raises(ValueError):
        RoutePlan(id="r", goal_name=None, legs=())
    with pytest.raises(ValueError):
        RoutePlan(id="", goal_name=None, legs=(leg,))
    with pytest.raises(ValueError):
        RoutePlan(id="r", goal_name=None, legs=("goto 1,1",))
    with pytest.raises(ValueError):
        RoutePlan(id="r", goal_name=None, legs=(leg,) * (mv.MAX_LEGS + 1))


def test_plans_are_immutable_and_hashable_values():
    plan = route({"direction": "e"}, verb="explore")
    with pytest.raises(Exception):
        plan.stride = 99  # type: ignore[misc]
    with pytest.raises(Exception):
        plan.legs[0].waypoint.x = 1  # type: ignore[misc]
    assert isinstance(plan.legs, tuple)
    assert hash(plan) == hash(RoutePlan.from_params(plan.to_params()))
    assert len({plan, RoutePlan.from_params(plan.to_params())}) == 1


def test_to_dsl_revalidates_a_tampered_waypoint():
    plan = route({"direction": "e"}, verb="explore")
    leg = plan.legs[0]
    object.__setattr__(leg.waypoint, "x", "1; key A_TALK")  # frozen, mas forçado
    assert plan.to_dsl(leg) == ""
    object.__setattr__(leg.waypoint, "x", mv.MAX_COORD + 5)
    assert plan.to_dsl(leg) == ""


def test_mirrored_vocabulary_matches_the_executor_and_the_candidates():
    assert mv.MAX_COORD == executor.MAX_COORD
    assert mv.MAX_PLAN_STEPS == executor.MAX_PLAN_STEPS
    assert set(COMPASS) <= executor.MOVE_DIRECTIONS         # só rumos que o ``move`` aceita
    vectors = getattr(executor, "_DIR_VECTORS", None)        # ausente se o executor passar a importá-los
    if vectors is not None:
        assert dict(DIRECTION_VECTORS) == vectors            # y cresce para sul, como no DF
    assert set(COMPASS) == set(candidates.DIRECTIONS)
    assert mv.ARRIVAL_TOLERANCE == 1                         # _goto: "arrived" a <= 1 tile
    assert all(candidates.opposite_direction(d) in COMPASS for d in COMPASS)


def test_plan_bearing_uses_the_origin_and_falls_back_to_the_legs():
    # 30 × 13 tiles = 23° ⇒ "se"; a soma dos rumos (quantizados) dos legs dá "e"
    plan = route({"x": 80, "y": 63}, urgency=0, verb="navigate_to")
    assert plan.bearing == "se" and "SE" in route_summary(plan)
    assert [leg.direction for leg in plan.legs] == ["e", "se", "e"]
    assert dataclasses.replace(plan, origin=None).bearing == "e"   # aproximação sem origem
    legs = (RouteLeg(0, Waypoint(60, 50), 10, "e"), RouteLeg(1, Waypoint(60, 60), 10, "s"))
    manual = RoutePlan(id="r", goal_name=None, legs=legs, origin=None)
    assert manual.bearing == "se"                      # soma dos vetores dos legs
    assert RoutePlan(id="r", goal_name=None, legs=legs, origin=Waypoint(50, 50)).bearing == "se"
    assert RoutePlan(id="r", goal_name=None, legs=legs, origin=Waypoint(60, 20)).bearing == "s"


def test_leg_directions_use_the_candidates_vocabulary():
    plan = route({"x": 50 + 40, "y": 50 - 25}, urgency=0, verb="navigate_to")
    assert all(leg.direction in COMPASS for leg in plan.legs)
    assert candidates.normalise_direction(plan.legs[0].direction.upper()) == plan.legs[0].direction


def test_compass_bearing_quantises_to_eight_sectors():
    assert compass_bearing(0, 0) == ""
    assert [compass_bearing(*DIRECTION_VECTORS[d]) for d in COMPASS] == list(COMPASS)
    assert compass_bearing(20, 8) == "e" and compass_bearing(20, 9) == "se"   # tan 22,5° ≈ 0,414
    assert compass_bearing(-8, -20) == "n" and compass_bearing(-9, -20) == "nw"
    assert compass_bearing(10, -10) == "ne" and compass_bearing(-30, 0) == "w"


# ========================================================= D. build_route


def test_explore_uses_the_whole_budget_of_the_urgency_level():
    safe = route({"steps": 5, "direction": "e"}, urgency=0, step=7, verb="explore")
    assert [leg.waypoint.xy for leg in safe.legs] == [(60 + 10 * k, 50) for k in range(6)]
    assert (safe.urgency, safe.stride, safe.reassess_every, safe.created_step) == ("safe", 10, 2, 7)
    assert safe.reason == "explore:direction" and safe.watch_prey is True and safe.truncated is False
    assert safe.total_tiles == 60 and safe.origin == Waypoint(50, 50)

    pressing = route({"direction": "e"}, urgency=None, verb="explore")
    assert pressing.urgency == "pressing" and len(pressing.legs) == 4 and pressing.total_tiles == 24
    critical = route({"direction": "e"}, urgency="Critical", verb="explore")
    assert [leg.waypoint.xy for leg in critical.legs] == [(53, 50), (56, 50)]
    assert critical.stride == 3 and critical.total_tiles == 6


def test_navigate_to_carries_z_and_name_on_the_final_leg_only_when_reached():
    plan = route({"x": 80, "y": 50, "z": 3, "destination": "the objective"}, urgency=0,
                 verb="navigate_to")
    assert [leg.waypoint.xy for leg in plan.legs] == [(60, 50), (70, 50), (80, 50)]
    assert plan.legs[-1].waypoint.z == 3 and plan.legs[-1].waypoint.name == "the objective"
    assert all(leg.waypoint.z is None and leg.waypoint.name is None for leg in plan.legs[:-1])
    assert plan.goal_name == "the objective" and plan.truncated is False
    assert plan.reason == "navigate_to:goal" and plan.watch_prey is False

    short = route({"x": 80, "y": 50, "z": 3, "destination": "the objective"}, urgency=1,
                  verb="navigate_to")
    assert short.truncated is True and short.total_tiles == 24 and short.final.xy == (74, 50)
    assert short.final.z is None and short.goal_name == "the objective"   # z/nome só no destino real
    assert route_summary(short).count("toward the objective") == 1


def test_navigate_to_without_coordinates_has_no_route():
    assert build_route({"destination": "Fortress"}, ORIGIN, 0, verb="navigate_to") is None
    assert build_route({"x": 51, "y": 51}, ORIGIN, 0, verb="navigate_to") is None  # já lá
    assert build_route({"x": 50, "y": 50}, ORIGIN, 0, verb="navigate_to") is None


def test_hunt_search_uses_the_staircase_and_watches_for_prey():
    plan = route({"steps": 5, "direction": "n"}, urgency=1, verb="hunt")
    assert [leg.waypoint.xy for leg in plan.legs] == [(50, 44), (56, 44), (56, 38), (62, 38)]
    assert plan.watch_prey is True and plan.reason == "hunt:direction"


def test_flee_runs_steps_tiles_and_reassesses_after_every_leg():
    safe = route({"direction": "s", "steps": 5, "from": "goblin"}, urgency=0, verb="flee")
    assert [leg.waypoint.xy for leg in safe.legs] == [(50, 55)]
    assert safe.reassess_every == 1          # fugir: reler depois de CADA leg, mesmo em Safe
    assert safe.watch_prey is False
    critical = route({"direction": "s", "steps": 5}, urgency=2, verb="flee")
    assert [leg.waypoint.xy for leg in critical.legs] == [(50, 53), (50, 55)]
    assert route({"direction": "s"}, urgency=0, verb="flee").total_tiles == mv.DEFAULT_FLEE_TILES
    assert build_route({"direction": "s", "steps": 1}, ORIGIN, 0, verb="flee") is None  # 1 tile: no-op
    assert build_route({"direction": "s", "steps": 0}, ORIGIN, 0, verb="flee") is None  # não andar
    assert route({"direction": "s", "steps": 500}, urgency=2, verb="flee").total_tiles == 6  # orçamento


@pytest.mark.parametrize("verb", ["attack", "eat", "drink", "drink_potion", "rest", "pick_up", "talk",
                                  "wait", "other", "unknown"])
def test_non_movement_verbs_never_get_a_route(verb):
    params = {"x": 80, "y": 50, "direction": "n", "steps": 5}
    assert build_route(params, ORIGIN, 0, verb=verb) is None


def test_verb_is_inferred_aliased_and_split_from_ids():
    assert route({"x": 80, "y": 50}).reason == "navigate_to:goal"          # sem verbo, com destino
    assert route({"direction": "n"}).reason == "explore:direction"        # sem verbo, só rumo
    assert route({"direction": "n"}, verb="explore:savanna").reason == "explore:direction"
    assert route({"x": 80, "y": 50}, verb="navigate").reason == "navigate_to:goal"   # prefixo do id
    assert route({"x": 80, "y": 50}, verb="NAVIGATE_TO").reason == "navigate_to:goal"
    assert route({"direction": "n"}, verb="").reason == "explore:direction"


def test_waypoints_param_is_chained_in_order_with_the_goal_last():
    plan = route({"waypoints": [[60, 50], {"x": 60, "y": 70, "name": "ford"}]}, urgency=0,
                 verb="navigate_to")
    assert [leg.waypoint.xy for leg in plan.legs] == [(60, 50), (60, 60), (60, 70)]
    assert plan.reason == "navigate_to:waypoints" and plan.goal_name == "ford"
    assert plan.legs[-1].waypoint.name == "ford"
    with_goal = route({"waypoints": [[60, 50]], "x": 60, "y": 70}, urgency=0, verb="navigate_to")
    assert [leg.waypoint.xy for leg in with_goal.legs] == [(60, 50), (60, 60), (60, 70)]
    same_goal = route({"waypoints": [[60, 50], [60, 70]], "x": 60, "y": 70}, urgency=0)
    assert [leg.waypoint.xy for leg in same_goal.legs] == [(60, 50), (60, 60), (60, 70)]  # sem duplicar


def test_waypoints_param_skips_garbage_entries_and_is_budgeted():
    plan = route({"waypoints": [None, "x", [1], {"x": "a", "y": 2}, {"x": 5}, [60, 50], True]},
                 urgency=0, verb="navigate_to")
    assert [leg.waypoint.xy for leg in plan.legs] == [(60, 50)]
    only_junk = {"waypoints": [None, "x", [1]]}
    assert build_route(only_junk, ORIGIN, 0, verb="navigate_to") is None   # nada para onde ir
    far = route({"waypoints": [[200, 50]]}, urgency=2, verb="navigate_to")
    assert far.truncated is True and far.total_tiles == 6 and len(far.legs) == 2
    many = route({"waypoints": [[50 + 3 * k, 50] for k in range(1, 40)]}, urgency=0)
    assert len(many.legs) <= 6 and many.total_tiles <= 60


def test_unknown_origin_or_garbage_params_yield_none_without_raising():
    for origin in (None, "abc", (1,), {}, object(), (True, 2)):
        assert build_route({"direction": "n"}, origin, 0, verb="explore") is None
    for params in (None, [], "x", 5, object(), {"x": "a", "y": "b"}, {"x": 10**12, "y": 5},
                   {"direction": "up"}, {"direction": "n; key A_TALK"}, {"direction": 7}):
        assert build_route(params, ORIGIN, 0, verb="explore") is None
    assert build_route({"direction": "n"}, (200, 200), 0, bounds=(0, 0, 100, 100)) is None


def test_build_route_is_deterministic_and_the_id_tracks_the_step():
    one = route({"x": 90, "y": 33}, urgency=0, step=4, verb="navigate_to")
    two = route({"x": 90, "y": 33}, urgency=0, step=4, verb="navigate_to")
    assert one == two and one.to_params() == two.to_params() and one.id == two.id
    other_step = route({"x": 90, "y": 33}, urgency=0, step=5, verb="navigate_to")
    assert other_step.id != one.id and other_step.legs == one.legs
    other_urgency = route({"x": 90, "y": 33}, urgency=2, step=4, verb="navigate_to")
    assert other_urgency.id.split("-")[2] != one.id.split("-")[2]
    for junk_step in (-5, "junk", None, 1.5, 10**30):
        clamped = route({"x": 90, "y": 33}, step=junk_step)
        assert clamped.created_step in (0, 10**9) and clamped.id.startswith(f"route-{clamped.created_step}-")
    assert route({"x": 90, "y": 33}, step="12").created_step == 12


def test_every_planned_route_is_internally_consistent_and_survives_a_json_round_trip():
    seen = 0
    for verb, params, urgency, origin in itertools.product(
        ("explore", "hunt", "flee", "navigate_to"),
        ({"direction": "ne", "steps": 7}, {"direction": "w"}, {"x": 140, "y": 20}, {"x": 53, "y": 49},
         {"waypoints": [[60, 60], [70, 40]], "destination": "camp"}),
        (None, 0, 1, 2, 0.7, "Critical"),
        ((50, 50), (3, 3), (500, 500, 2)),
    ):
        plan = build_route(params, origin, urgency, 11, verb=verb)
        if plan is None:
            continue
        seen += 1
        # índices contíguos; steps_hint = distância real entre waypoints consecutivos
        assert [leg.index for leg in plan.legs] == list(range(len(plan.legs)))
        chain = [plan.origin.xy, *[leg.waypoint.xy for leg in plan.legs]]
        assert [leg.steps_hint for leg in plan.legs] == [cheb(a, b) for a, b in zip(chain, chain[1:])]
        assert all(mv.MIN_LEG_TILES <= leg.steps_hint <= plan.stride for leg in plan.legs)
        assert plan.total_tiles <= plan.stride * len(plan.legs) <= mv.MAX_STRIDE * mv.MAX_LEGS
        assert len(plan.legs) <= mv.URGENCY_PROFILES[plan.urgency].max_legs
        assert (plan.stride, plan.reassess_every) == (
            mv.URGENCY_PROFILES[plan.urgency].stride,
            1 if verb == "flee" else mv.URGENCY_PROFILES[plan.urgency].reassess_every)
        # (de)serialização exata e DSL aceite pela ponte real
        assert RoutePlan.from_params(json.loads(json.dumps(plan.to_params()))) == plan
        assert RoutePlan.from_params(plan.to_params()).to_params() == plan.to_params()
        for leg in plan.legs:
            assert bridge_parse(plan.to_dsl(leg, with_budget=True))[0]["op"] == "goto"
        assert route_summary(plan).startswith("Travel ~") and route_summary(plan).isascii()
    assert seen >= 80   # a grelha não pode degenerar em "nada foi testado"


def test_leg_budget_is_bounded_and_proportional_to_the_planned_tiles():
    plan = route({"direction": "e"}, urgency=0, verb="explore")
    assert [plan.leg_budget(leg) for leg in plan.legs] == [30] * 6                   # 10 tiles × 3
    tiny = RouteLeg(0, Waypoint(5, 5), 0, "")
    assert plan.leg_budget(tiny) == mv.MIN_LEG_TILES * mv.LEG_STEP_FACTOR
    huge = RouteLeg(0, Waypoint(5, 5), mv.MAX_PLAN_STEPS, "")
    assert plan.leg_budget(huge) == mv.MAX_PLAN_STEPS


def test_watch_prey_defaults_by_verb_and_can_be_overridden():
    assert route({"direction": "n"}, verb="explore").watch_prey is True
    assert route({"direction": "n"}, verb="hunt").watch_prey is True
    assert route({"direction": "n"}, verb="flee").watch_prey is False
    assert route({"x": 90, "y": 50}, verb="navigate_to").watch_prey is False
    assert route({"direction": "n"}, verb="explore", watch_prey=False).watch_prey is False
    assert route({"x": 90, "y": 50}, verb="navigate_to", watch_prey=True).watch_prey is True


def test_unknown_schema_version_is_not_guessed():
    params = route({"direction": "e"}, verb="explore").to_params()
    assert RoutePlan.from_params(params) is not None
    params["v"] = 2
    assert RoutePlan.from_params(params) is None
    del params["v"]
    assert RoutePlan.from_params(params) is not None   # sem "v" vale o esquema 1


def test_build_route_steers_around_avoid_positions():
    plan = route({"x": 110, "y": 50}, urgency=0, verb="navigate_to", avoid=[(56, 50)])
    chain = [ORIGIN, *[leg.waypoint.xy for leg in plan.legs]]
    for a, b in zip(chain, chain[1:]):
        assert all(cheb(tile, (56, 50)) > 2 for tile in segment_tiles(a, b))
    blocked = build_route({"direction": "e", "steps": 12}, (10, 10), 2, verb="flee", avoid=[(11, 10)])
    assert blocked is None   # a única saída é para dentro do hostil: sem rota segura


def test_avoid_radius_is_a_tunable_margin():
    wide = route({"x": 110, "y": 50}, urgency=0, verb="navigate_to", avoid=[(56, 50)], avoid_radius=4)
    narrow = route({"x": 110, "y": 50}, urgency=0, verb="navigate_to", avoid=[(56, 50)], avoid_radius=0)

    def closest(plan):
        chain = [ORIGIN, *[leg.waypoint.xy for leg in plan.legs]]
        return min(cheb(tile, (56, 50)) for a, b in zip(chain, chain[1:]) for tile in segment_tiles(a, b))

    assert closest(narrow) >= 1            # nunca por cima do tile
    assert closest(wide) > 4               # a margem pedida é respeitada
    assert closest(wide) > closest(narrow)
    # sem avoid a reta passa mesmo por cima do tile
    assert closest(route({"x": 110, "y": 50}, urgency=0, verb="navigate_to")) == 0


def test_origin_z_and_custom_goal_name_are_carried():
    plan = route({"direction": "n"}, origin=(50, 50, 7), verb="explore", goal_name="the north road")
    assert plan.origin == Waypoint(50, 50, z=7) and plan.goal_name == "the north road"
    assert as_xy({"pos": {"x": 4, "y": 5}}) == (4, 5)


def test_profiles_override_reaches_the_planner():
    plan = route({"direction": "ne"}, urgency=1, verb="explore", profiles={"pressing": (8, 3, 1)})
    assert (plan.stride, len(plan.legs), plan.total_tiles) == (8, 3, 24)


def test_real_candidate_params_produce_routes_where_movement_makes_sense():
    state = game_state(
        x=50, y=50, z=0, surroundings="a road runs north", region="The Hills",
        hostiles=[hostile("goblin skulker", 3, "NE")],
        units=[animal("white-tailed deer", 12, "E", x=62, y=50), friendly("hunter", 4, "S")],
        landmarks=[{"name": "ford", "x": 80, "y": 52, "z": 0}],
        hunger="none", thirst="none", sleep="rested", hour=12.0,
    )
    actions = candidates.generate_actions(state, max_candidates=40, objective="find an animal and kill it")
    origin = (state.metrics.location.x, state.metrics.location.y)
    plans = {action.id: build_route(action.params, origin, 1, verb=action.verb) for action in actions}

    flee = next(a for a in actions if a.verb == "flee")
    assert plans[flee.id] is not None and plans[flee.id].total_tiles == 5      # flee_steps
    assert plans[flee.id].bearing == flee.params["direction"] == "sw"          # foge do hostil NE
    explore = next(a for a in actions if a.verb == "explore")
    assert plans[explore.id].bearing == "n" and plans[explore.id].watch_prey   # "a road runs north"
    navigate = next(a for a in actions if a.verb == "navigate_to")
    assert plans[navigate.id].truncated is True and plans[navigate.id].total_tiles == 24
    reached = build_route(navigate.params, origin, 0, verb=navigate.verb)      # Safe: chega
    assert reached.final == Waypoint(80, 52, z=0, name="ford") and reached.truncated is False
    for action in actions:
        if action.verb in ("attack", "wait", "other", "eat", "talk", "drink", "rest"):
            assert plans[action.id] is None, action.id
    # nenhum plano passa o orçamento do nível "pressing"
    assert all(plan.total_tiles <= 24 for plan in plans.values() if plan is not None)


def test_the_hunt_search_candidate_becomes_a_staircase_route():
    state = game_state(x=50, y=50, z=0, surroundings="a road runs north", region="The Hills",
                       hunger="none", thirst="none", sleep="rested", hour=12.0)
    actions = candidates.generate_actions(state, max_candidates=40, objective="find an animal and kill it")
    search = next(a for a in actions if a.verb == "hunt")
    plan = build_route(search.params, (50, 50), 1, verb=search.verb)
    assert plan is not None and plan.reason == "hunt:direction" and plan.watch_prey
    assert [leg.waypoint.xy for leg in plan.legs] == [(50, 44), (56, 44), (56, 38), (62, 38)]


def test_candidates_reexports_plan_waypoints_once_wired():
    # ``candidates.__all__`` anuncia ``plan_waypoints``; depois da ligação é este mesmo objeto.
    if not hasattr(candidates, "plan_waypoints"):
        pytest.skip("candidates.py ainda não importa plan_waypoints de movement")
    assert candidates.plan_waypoints is plan_waypoints


# ================================================================ E. veredictos


def wp_leg(x=60, y=50):
    return RouteLeg(0, Waypoint(x, y), 10, "e")


@pytest.mark.parametrize("before,after,leg,expected", [
    # progresso, ainda não chegou
    ((50, 50), (54, 50), wp_leg(60, 50), ("continue", "progress", 6, 4)),
    # chegou exatamente / a 1 tile / a 2 tiles (ainda não)
    ((50, 50), (60, 50), wp_leg(60, 50), ("arrived", "arrived", 0, 10)),
    ((50, 50), (59, 51), wp_leg(60, 50), ("arrived", "arrived", 1, 9)),
    ((50, 50), (58, 50), wp_leg(60, 50), ("continue", "progress", 2, 8)),
    # parado e longe ⇒ bloqueado; parado mas já dentro de 1 tile ⇒ chegou
    ((50, 50), (50, 50), wp_leg(60, 50), ("blocked", "position_unchanged", 10, 0)),
    ((59, 50), (59, 50), wp_leg(60, 50), ("arrived", "arrived", 1, 0)),
    # mudou só de z ⇒ não está bloqueado
    ((50, 50, 0), (50, 50, 1), wp_leg(60, 50), ("continue", "progress", 10, 0)),
    # sem posição final ⇒ não se verifica ⇒ aborta; sem a inicial dá para julgar a chegada
    ((50, 50), None, wp_leg(60, 50), ("abort", "position_unknown", None, None)),
    (None, (60, 50), wp_leg(60, 50), ("arrived", "arrived", 0, None)),
    (None, (52, 50), wp_leg(60, 50), ("continue", "progress", 8, None)),
])
def test_position_matrix(before, after, leg, expected):
    verdict = evaluate_leg(before, after, leg)
    kind, reason, distance, moved = expected
    assert (verdict.kind, verdict.reason, verdict.distance, verdict.moved) == (kind, reason, distance, moved)
    assert verdict.ok is (kind in ("continue", "arrived"))


@pytest.mark.parametrize("extras,reason", [
    (dict(hostile_count_before=0, hostile_count_after=1), "new_hostile"),
    (dict(hostile_count_before=2, hostile_count_after=3), "new_hostile"),
    (dict(blood_before=5000, blood_after=4400), "blood_drop"),               # -12 %
    (dict(blood_before=5000, blood_after=0), "blood_drop"),
    (dict(focus_before="dungeonmode/Default", focus_after="dungeonmode/Help"), "focus_changed"),
    (dict(great_danger_before=False, great_danger_after=True), "great_danger_appeared"),
    (dict(alive_after=False), "adventurer_dead"),
])
def test_safety_aborts_win_over_arrival(extras, reason):
    verdict = evaluate_leg((50, 50), (60, 50), wp_leg(60, 50), **extras)  # chegou E aconteceu algo
    assert (verdict.kind, verdict.reason) == ("abort", reason)
    assert verdict.distance == 0 and verdict.moved == 10 and verdict.ok is False


@pytest.mark.parametrize("extras", [
    dict(hostile_count_before=2, hostile_count_after=2),                      # o mesmo hostil
    dict(hostile_count_before=3, hostile_count_after=1),                      # um fugiu
    dict(blood_before=5000, blood_after=4500),                                # exatamente 10 %
    dict(blood_before=5000, blood_after=5200),                                # recuperou
    dict(blood_before=None, blood_after=100), dict(blood_before=0, blood_after=0),
    dict(blood_before=5000, blood_after=None), dict(blood_before="x", blood_after=1),
    dict(focus_before="dungeonmode/Default", focus_after="dungeonmode/Default"),
    dict(focus_before=None, focus_after="dungeonmode/Help"), dict(focus_before="a", focus_after=""),
    dict(great_danger_before=True, great_danger_after=True),
    dict(prey_before=0, prey_after=2),                                        # só com stop_on_new_prey
    dict(hostile_count_after="many"),                                         # lixo ⇒ 0
])
def test_non_events_do_not_abort(extras):
    assert evaluate_leg((50, 50), (54, 50), wp_leg(60, 50), **extras).kind == "continue"


def test_blood_threshold_is_configurable_and_strictly_greater():
    kwargs = dict(blood_before=1000, blood_after=900)  # -10 %
    assert evaluate_leg((50, 50), (54, 50), wp_leg(), **kwargs).kind == "continue"
    assert evaluate_leg((50, 50), (54, 50), wp_leg(), blood_drop_fraction=0.05, **kwargs).reason == "blood_drop"
    assert evaluate_leg((50, 50), (54, 50), wp_leg(), blood_drop_fraction="junk", **kwargs).kind == "continue"
    assert mv.BLOOD_DROP_ABORT_FRACTION == 0.10


def test_abort_priority_is_dead_then_focus_then_hostile_then_danger_then_blood_then_prey():
    everything = dict(alive_after=False, focus_before="a", focus_after="b", hostile_count_after=1,
                      great_danger_after=True, blood_before=100, blood_after=1, prey_after=1,
                      stop_on_new_prey=True)
    order = ["adventurer_dead", "focus_changed", "new_hostile", "great_danger_appeared", "blood_drop",
             "new_prey"]
    for expected in order:
        verdict = evaluate_leg((50, 50), (54, 50), wp_leg(), **everything)
        assert verdict.reason == expected
        removed = {"adventurer_dead": "alive_after", "focus_changed": "focus_after",
                   "new_hostile": "hostile_count_after", "great_danger_appeared": "great_danger_after",
                   "blood_drop": "blood_after", "new_prey": "prey_after"}[expected]
        everything.pop(removed)
        if expected == "focus_changed":
            everything.pop("focus_before")
        if expected == "blood_drop":
            everything.pop("blood_before")


def test_new_prey_is_an_opportunity_only_when_the_route_watches_for_it():
    base = dict(prey_before=0, prey_after=1)
    ignored = evaluate_leg((50, 50), (54, 50), wp_leg(), **base)
    assert ignored.kind == "continue"
    seen = evaluate_leg((50, 50), (54, 50), wp_leg(), stop_on_new_prey=True, **base)
    assert (seen.kind, seen.reason, seen.opportunity) == ("abort", "new_prey", True)
    assert "new_prey" in mv.OPPORTUNITY_REASONS
    assert evaluate_leg((50, 50), (54, 50), wp_leg(), stop_on_new_prey=True, prey_before=1,
                        prey_after=1).kind == "continue"
    hostile_abort = evaluate_leg((50, 50), (54, 50), wp_leg(), hostile_count_after=1)
    assert hostile_abort.opportunity is False   # perigo não é oportunidade


def test_arrival_tolerance_is_configurable():
    far = evaluate_leg((50, 50), (57, 50), wp_leg(60, 50), tolerance=3)
    assert far.kind == "arrived"
    strict = evaluate_leg((50, 50), (59, 50), wp_leg(60, 50), tolerance=0)
    assert strict.kind == "continue"
    assert evaluate_leg((50, 50), (59, 50), wp_leg(60, 50), tolerance="junk").kind == "arrived"


@pytest.mark.parametrize("leg", [wp_leg(60, 50), Waypoint(60, 50), (60, 50), [60, 50],
                                 {"x": 60, "y": 50}, {"pos": {"x": 60, "y": 50}}])
def test_leg_may_be_a_routeleg_waypoint_pair_or_mapping(leg):
    assert evaluate_leg((50, 50), (60, 50), leg).kind == "arrived"


@pytest.mark.parametrize("leg", [None, "x", 5, object(), {}, (1,)])
def test_unusable_leg_is_judged_by_movement_alone(leg):
    assert evaluate_leg((50, 50), (54, 50), leg).kind == "continue"
    assert evaluate_leg((50, 50), (50, 50), leg).kind == "blocked"


def test_evaluate_leg_never_raises_on_garbage():
    garbage = [None, "x", 5, 1.5, math.nan, math.inf, [], {}, object(), (1,), (1, 2, 3), True, 10**30]
    for a, b, c in itertools.product(garbage, repeat=3):
        verdict = evaluate_leg(a, b, c, hostile_count_before=a, hostile_count_after=b,
                               blood_before=a, blood_after=b, focus_before=a, focus_after=b,
                               prey_before=a, prey_after=b, stop_on_new_prey=True, tolerance=c,
                               blood_drop_fraction=c)
        assert isinstance(verdict, LegVerdict)
        assert verdict.kind in (mv.LEG_CONTINUE, mv.LEG_ARRIVED, mv.LEG_BLOCKED, mv.LEG_ABORT)


# ======================================================== F. snapshots reais


def snap(x=50, y=50, **kwargs):
    base = dict(x=x, y=y, z=0, focus="dungeonmode/Default", blood_current=5000, blood_max=5000,
                surroundings="open savanna", region="Plains")
    base.update(kwargs)
    return game_state(**base)


def test_observe_state_reads_position_threats_prey_blood_and_focus():
    state = snap(
        hostiles=[hostile("goblin skulker", 6, "NE", great_danger=True)],
        units=[animal("deer", 4, "E"), animal("rat", 3, "W", visible=False), friendly("farmer", 2, "S"),
               animal("wolf", 5, "N", hostile=True, hostile_flag=True),
               animal("something", 6, "S", kind=None)],          # espécie desconhecida: não é presa
    )
    seen = observe_state(state)
    assert seen == LegObservation(pos=(50, 50, 0), hostiles=1, great_danger=True, prey=1,
                                  blood=5000.0, focus="dungeonmode/Default", alive=True)
    assert observe_state(snap(status="dead")).alive is False


def test_observe_state_falls_back_to_the_adventurer_block_and_survives_garbage():
    state = game_state(adventurer={"name": "Urist", "pos": {"x": 88, "y": 72, "z": 1}}, focus=None)
    assert observe_state(state).pos == (88, 72, 1) and observe_state(state).focus is None
    for junk in (None, object(), "x", 5, {"metrics": 1}):
        assert observe_state(junk) == LegObservation()


def test_evaluate_states_matches_the_primitive_verdicts():
    leg = wp_leg(60, 50)
    assert evaluate_states(snap(50, 50), snap(54, 50), leg).kind == "continue"
    assert evaluate_states(snap(50, 50), snap(60, 50), leg).kind == "arrived"
    assert evaluate_states(snap(50, 50), snap(50, 50), leg).kind == "blocked"
    hostile_after = snap(54, 50, hostiles=[hostile("goblin", 6, "N")])
    assert evaluate_states(snap(50, 50), hostile_after, leg).reason == "new_hostile"
    assert evaluate_states(snap(50, 50, hostiles=[hostile("goblin", 6, "N")]), hostile_after, leg).kind \
        == "continue"
    assert evaluate_states(snap(50, 50), snap(54, 50, blood_current=4000), leg).reason == "blood_drop"
    assert evaluate_states(snap(50, 50), snap(54, 50, focus="dungeonmode/Help"), leg).reason == "focus_changed"
    assert evaluate_states(snap(50, 50), snap(54, 50, status="dead"), leg).reason == "adventurer_dead"
    deer = snap(54, 50, units=[animal("deer", 4, "E")])
    assert evaluate_states(snap(50, 50), deer, leg).kind == "continue"
    assert evaluate_states(snap(50, 50), deer, leg, stop_on_new_prey=True).opportunity is True
    assert evaluate_states(snap(50, 50), snap(59, 50), leg, tolerance=0).kind == "continue"
    assert evaluate_states(None, None, leg).reason == "position_unknown"


# ====================================================== G. corredor de rota


def run_route(plan: RoutePlan, snapshots, *, start):
    """O laço que o executor fará: DSL validado → "executar" → snapshot → veredicto."""
    sent, verdicts = [], []
    before = start
    for leg, after in zip(plan.legs, snapshots):
        dsl = plan.to_dsl(leg, with_budget=True)
        assert dsl and bridge_parse(dsl)[0]["op"] == "goto"
        sent.append(dsl)
        verdict = evaluate_states(before, after, leg, stop_on_new_prey=plan.watch_prey)
        verdicts.append(verdict)
        if not verdict.ok:
            break
        before = after
    return sent, verdicts


def arrive_at(plan: RoutePlan, **extra):
    return [snap(leg.waypoint.x, leg.waypoint.y, **extra) for leg in plan.legs]


def test_route_runner_completes_when_every_leg_arrives():
    plan = route({"direction": "e"}, urgency=1, verb="explore")
    sent, verdicts = run_route(plan, arrive_at(plan), start=snap())
    assert len(sent) == len(plan.legs) == 4
    assert [v.kind for v in verdicts] == ["arrived"] * 4
    assert sent[0] == "goto 56,50 maxsteps=18"


def test_route_runner_stops_on_a_blocked_leg_and_the_stuck_detector_sees_no_progress():
    plan = route({"direction": "e"}, urgency=1, verb="explore")
    states = arrive_at(plan)
    states[1] = snap(56, 50)                 # a 2.ª leg não saiu do sítio
    sent, verdicts = run_route(plan, states, start=snap())
    assert [v.kind for v in verdicts] == ["arrived", "blocked"] and len(sent) == 2
    # alimenta o StuckDetector existente: o hash do estado não mudou entre os dois snapshots
    assert state_fingerprint(states[0]) == state_fingerprint(states[1])
    detector = StuckDetector(pattern_sink=False)
    events = [detector.observe(states[1]) for _ in range(3)]
    assert events[:2] == [None, None]
    assert events[-1] is not None and events[-1].kind == "stuck"
    assert events[-1].escalation == "force_explore"


def test_route_runner_tries_the_relative_move_once_when_goto_is_blocked():
    plan = route({"direction": "e"}, urgency=1, verb="explore")
    leg, before = plan.legs[0], snap()
    first = evaluate_states(before, snap(50, 50), leg)            # o goto não saiu do sítio
    assert first.kind == "blocked"
    assert plan.fallback_dsl(leg) == "move e times=6"             # plano B validado
    assert evaluate_states(before, snap(56, 50), leg).kind == "arrived"
    assert evaluate_states(before, snap(53, 50), leg).kind == "continue"   # andou meio caminho


def test_route_runner_aborts_on_a_new_hostile_between_legs():
    plan = route({"direction": "e"}, urgency=0, verb="explore")
    states = arrive_at(plan)
    states[1] = snap(70, 50, hostiles=[hostile("goblin skulker", 8, "N")])
    sent, verdicts = run_route(plan, states, start=snap())
    assert [v.kind for v in verdicts] == ["arrived", "abort"]
    assert verdicts[-1].reason == "new_hostile" and verdicts[-1].opportunity is False


def test_route_runner_stops_for_prey_only_on_routes_that_watch_for_it():
    deer = [animal("deer", 5, "N", x=68, y=45)]
    explore = route({"direction": "e"}, urgency=1, verb="explore")
    states = arrive_at(explore)
    states[2] = snap(68, 50, units=deer)         # a presa aparece ao fim do 3.º leg
    _sent, verdicts = run_route(explore, states, start=snap())
    assert [v.kind for v in verdicts] == ["arrived", "arrived", "abort"]
    assert verdicts[-1].reason == "new_prey" and verdicts[-1].opportunity is True

    travel = route({"x": 74, "y": 50}, urgency=1, verb="navigate_to")
    assert [leg.waypoint.xy for leg in travel.legs] == [(56, 50), (62, 50), (68, 50), (74, 50)]
    states = arrive_at(travel)
    states[2] = snap(68, 50, units=deer)
    _sent, verdicts = run_route(travel, states, start=snap())   # viagem com propósito: não pára
    assert [v.kind for v in verdicts] == ["arrived"] * 4


def test_route_runner_survives_a_lost_snapshot_by_aborting_not_guessing():
    plan = route({"direction": "e"}, urgency=1, verb="explore")
    states = arrive_at(plan)
    states[0] = None
    _sent, verdicts = run_route(plan, states, start=snap())
    assert [(v.kind, v.reason) for v in verdicts] == [("abort", "position_unknown")]


# ================================================================ H. resumo EN


def test_route_summary_matches_the_documented_line():
    plan = route({"direction": "ne"}, origin=(50, 50), urgency=1, verb="explore",
                 profiles={"pressing": (8, 3, 1)})
    assert route_summary(plan) == "Travel ~24 tiles NE in 3 legs, re-assessing between legs"
    single = route({"direction": "s", "steps": 5}, urgency=0, verb="flee")
    assert route_summary(single) == "Travel ~5 tiles S in 1 leg, re-assessing on arrival"


def test_route_summary_names_the_destination_and_is_plain_english():
    reached = route({"x": 80, "y": 50, "destination": "the old watchtower"}, urgency=0,
                    verb="navigate_to")
    assert route_summary(reached) == \
        "Travel ~30 tiles E to the old watchtower in 3 legs, re-assessing between legs"
    partial = route({"x": 200, "y": 50, "destination": "the old watchtower"}, urgency=2,
                    verb="navigate_to")
    assert route_summary(partial) == \
        "Travel ~6 tiles E toward the old watchtower in 2 legs, re-assessing between legs"
    assert route_summary(reached).isascii()


def test_route_summary_caps_long_names_and_ignores_non_plans():
    long_name = "x" * 500
    plan = route({"x": 80, "y": 50, "destination": long_name}, urgency=0, verb="navigate_to")
    assert len(route_summary(plan)) < 120
    for junk in (None, "plan", 5, {}, object(), plan.to_params()):
        assert route_summary(junk) == ""


# ====================================================== I. rumo por tentar


def test_next_unvisited_direction_prefers_small_turns_and_is_deterministic():
    assert next_unvisited_direction() == "n"
    assert next_unvisited_direction([], None) == "n"
    assert next_unvisited_direction(["n"]) == "ne"
    assert next_unvisited_direction(["n", "ne", "e"], None) == "se"
    visited: list[str] = []
    order = []
    for _ in range(7):
        step = next_unvisited_direction(visited + ["n"], "n")
        order.append(step)
        visited.append(step)
    assert order == ["ne", "nw", "e", "w", "se", "sw", "s"]   # ±45°, ±90°, ±135°, 180°
    assert len(set(order)) == 7 and "n" not in order


def test_next_unvisited_direction_never_runs_out_and_ignores_garbage():
    everything = list(COMPASS)
    assert next_unvisited_direction(everything, "e") == "se"  # roda 45°
    assert next_unvisited_direction(everything, None) == "n"
    assert next_unvisited_direction("n", "n") == "ne"          # string única, não letra a letra
    assert next_unvisited_direction(["NE", "north-west", "up", None, 5, "n; x"], "n") == "e"
    assert next_unvisited_direction(None, "xyz") == "n"
    assert next_unvisited_direction(5, 5) == "n"
    assert next_unvisited_direction(iter(["n", "ne"]), "n") == "nw"


# ======================================== J. pureza e robustez (nunca levanta)


def test_the_module_stays_pure_and_standard_library_only():
    tree = ast.parse(inspect.getsource(mv))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"__future__", "dataclasses", "hashlib", "itertools", "json", "logging", "math",
                        "re", "types", "typing", "unicodedata"}
    assert not any(hasattr(mv, name) for name in ("subprocess", "socket", "random", "time", "os"))


def test_public_api_is_exported_and_importable():
    for name in mv.__all__:
        assert hasattr(mv, name), name
    for name in ("Waypoint", "RouteLeg", "RoutePlan", "plan_waypoints", "urgency_profile", "build_route",
                 "evaluate_leg", "route_summary", "next_unvisited_direction"):
        assert name in mv.__all__


def test_public_functions_never_raise_on_garbage():
    garbage = [None, "", "x; key A_TALK", 5, -1, 10**30, 1.5, math.nan, math.inf, True, [], [1], (1, 2),
               {}, {"x": 1, "y": 2}, {"legs": [1]}, object(), b"bytes", ["a"] * 5, {"direction": 1}]
    for a, b in itertools.product(garbage, repeat=2):
        plan_waypoints(a, b, direction=b, distance=a, stride=a, max_legs=b, bounds=a, avoid=b)
        build_route(a, b, a, b, verb=a, avoid=b, bounds=a, profiles=b, goal_name=a)
        build_route({"x": a, "y": b, "direction": a, "steps": b, "waypoints": [a, b], "destination": a},
                    (50, 50), a, b, verb="explore")
        urgency_level(a)
        urgency_profile(a, profiles=b)
        RoutePlan.from_params(a)
        RoutePlan.from_params({"legs": [{"x": a, "y": b, "dir": a, "steps": b}], "id": "r", "stride": a})
        route_summary(a)
        next_unvisited_direction(a, b)
        normalise_compass(a)
        as_xy(a)
        observe_state(a)
        evaluate_states(a, b, a)
    plan = route({"direction": "e"}, verb="explore")
    for junk in garbage:
        assert plan.to_dsl(junk) in ("", ) or plan.to_dsl(junk).startswith("goto ")


def test_game_text_is_capped_and_stripped_of_control_characters():
    wp = Waypoint(1, 2, name="  Gate\x00 of\tthe\nNorth ; #x" + "y" * 300)
    assert wp.name is not None and len(wp.name) <= 60
    assert "\x00" not in wp.name and "\t" not in wp.name and "\n" not in wp.name
    assert ";" not in wp.name and "#" not in wp.name and wp.name.startswith("Gate of the North")
    assert Waypoint(1, 2, name="\x00\x01").name is None
    assert Waypoint(1, 2, name=5).name is None
