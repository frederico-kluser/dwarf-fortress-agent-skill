"""Candidatos de ação: determinismo, viabilidade e triagem.

Cinco cenários obrigatórios (sem hostis → sem ``attack``; a morrer de fome →
``eat`` com prioridade de sobrevivência; sonolento + noite → ``rest``; mundo
vazio → só ``wait``/``explore``/``other``; muitos itens → lista cortada), mais as
regras que impedem oferecer ao JEV uma ação que não é executável agora.
"""
from __future__ import annotations

from system_one_dwarf.agent.candidates import (
    EXIT_ACTION_ID,
    WAIT_ACTION_ID,
    direction_from_text,
    feasible_verbs,
    generate_actions,
    normalise_direction,
    opposite_direction,
    visible_ground_items,
)
import pytest

from system_one_dwarf.agent.types import LoopConfig

from .fakes_loop import friendly, game_state, hostile

CONFIG = LoopConfig()


def verbs(actions) -> list[str]:
    return [action.verb for action in actions]


def ids(actions) -> list[str]:
    return [action.id for action in actions]


def action_of(actions, verb: str):
    return next((action for action in actions if action.verb == verb), None)


def actions_of(actions, verb: str):
    return [action for action in actions if action.verb == verb]


# ------------------------------------------------------------------ cenário 1


def test_no_hostiles_no_attack():
    state = game_state(region="The Hills", hunger="none", thirst="none", sleep="rested")
    actions = generate_actions(state, CONFIG)
    assert "attack" not in verbs(actions)
    assert "flee" not in verbs(actions)
    assert actions[-1].id == EXIT_ACTION_ID
    assert action_of(actions, "explore") is not None


def test_attack_per_visible_hostile_and_eval_aligned_ids():
    state = game_state(hostiles=[hostile("goblin skulker", 6, "NE"),
                                hostile("kobold thief", 3, "S")])
    actions = generate_actions(state, CONFIG)
    attacks = actions_of(actions, "attack")
    assert len(attacks) == 2
    # ids no formato dos evals: <verbo>:<alvo>
    assert "attack:goblin_skulker" in ids(actions)
    assert "attack:kobold_thief" in ids(actions)
    # a ordem dentro do mesmo nível é por distância (o mais perto primeiro)
    assert attacks[0].target == "kobold thief"
    assert attacks[0].category == "threat"
    assert attacks[0].params["steps"] == 2  # 3 tiles - 1 (aproximar até adjacente)
    assert attacks[0].params["direction"] == "s"


def test_flee_opposite_to_nearest_threat_when_dangerous():
    state = game_state(hostiles=[hostile("bronze colossus", 4, "N", great_danger=True)],
                       hunger="none", thirst="none", sleep="rested")
    actions = generate_actions(state, CONFIG)
    flee = action_of(actions, "flee")
    assert flee is not None
    assert flee.id == "flee:s"
    assert flee.category == "survival"
    assert flee.params["steps"] == CONFIG.flee_steps
    # sobrevivência vem antes de responder à ameaça
    assert ids(actions).index(flee.id) < ids(actions).index("attack:bronze_colossus")


# ------------------------------------------------------------------ cenário 2


def test_starving_with_food_prioritises_eat():
    state = game_state(
        hunger="starving", thirst="none", sleep="rested",
        region="The Hills",
        items=[{"name": "dried meat", "quantity": 2, "weight_kg": 0.4},
               {"name": "copper dagger", "weight_kg": 1.2, "equipped": True}],
        weapon="copper dagger",
    )
    actions = generate_actions(state, CONFIG)
    eat = action_of(actions, "eat")
    assert eat is not None
    assert eat.id == "eat:dried_meat"
    assert eat.category == "survival"
    assert eat.priority == 40
    explore = action_of(actions, "explore")
    assert ids(actions).index(eat.id) < ids(actions).index(explore.id)
    # o item já está na mochila: o executor sabe qual é
    assert eat.params["item"] == "dried meat"
    assert eat.needs_manual_mapping is True


def test_hungry_without_food_asks_to_forage():
    state = game_state(hunger="hungry", thirst="none", sleep="rested")
    actions = generate_actions(state, CONFIG)
    eat = action_of(actions, "eat")
    assert eat is not None and eat.id == "eat:forage" and eat.params["search"] is True


def test_eat_and_drink_absent_when_needs_none_and_no_items():
    state = game_state(hunger="none", thirst="none", sleep="rested")
    actions = generate_actions(state, CONFIG)
    assert "eat" not in verbs(actions) and "drink" not in verbs(actions)
    assert "drink_potion" not in verbs(actions)


def test_thirsty_without_water_searches_and_potion_heals_wounds():
    state = game_state(
        thirst="thirsty", hunger="none", sleep="rested", status="bleeding",
        wounds=[{"body_part": "left upper arm", "severity": 40, "bleeding": True}],
        items=[{"name": "healing potion", "quantity": 1, "weight_kg": 0.3}],
    )
    actions = generate_actions(state, CONFIG)
    assert action_of(actions, "drink").id == "drink:nearest_water"
    potion = action_of(actions, "drink_potion")
    assert potion is not None and potion.id == "drink:healing_potion"
    assert potion.category == "survival" and potion.priority == 10
    # ferido + com poção + com sede: a poção vem primeiro (sobrevivência)
    assert ids(actions).index(potion.id) < ids(actions).index("drink:nearest_water")


# ------------------------------------------------------------------ cenário 3


def test_drowsy_night_rests():
    state = game_state(sleep="drowsy", hour=23.0, season="Autumn", region="The Hills")
    actions = generate_actions(state, CONFIG)
    rest = action_of(actions, "rest")
    assert rest is not None
    assert rest.id == "rest:here"           # sonolento: dorme onde está
    assert rest.params["reason"] == "drowsy"
    assert rest.category == "survival"


def test_night_without_sleepiness_rests_until_morning():
    state = game_state(sleep="rested", hour=22.5, region="The Hills")
    rest = action_of(generate_actions(state, CONFIG), "rest")
    assert rest is not None
    assert rest.id == "rest:until_morning"
    assert rest.params["night"] is True


def test_rest_absent_when_rested_and_daytime_even_if_safe():
    state = game_state(sleep="rested", hour=12.0, region="The Hills")
    assert "rest" not in verbs(generate_actions(state, CONFIG))


def test_no_rest_with_hostile_on_top():
    state = game_state(sleep="drowsy", hour=23.0, hostiles=[hostile("goblin", 3, "S")])
    rest = action_of(generate_actions(state, CONFIG), "rest")
    assert rest is None


# ------------------------------------------------------------------ cenário 4


def test_empty_world_only_wait_explore_other():
    state = game_state()
    actions = generate_actions(state, CONFIG)
    assert set(verbs(actions)) == {"explore", "wait", "other"}
    assert actions[-1].id == EXIT_ACTION_ID
    assert actions[-2].id == WAIT_ACTION_ID
    assert feasible_verbs(state, CONFIG) == ("explore", "wait", "other")
    # o mundo vazio não inventa alvos
    assert all(action.needs_manual_mapping is False for action in actions)


# ------------------------------------------------------------------ cenário 5


def test_many_ground_items_are_capped_and_unique():
    ground = [f"iron dagger {index}" for index in range(20)]
    state = game_state(ground=ground, region="The Hills")
    actions = generate_actions(state, LoopConfig(max_candidates=5))
    assert len(actions) == 5
    assert len(ids(actions)) == len(set(ids(actions)))
    assert actions[-1].id == EXIT_ACTION_ID
    assert actions[-2].id == WAIT_ACTION_ID
    assert len(actions_of(actions, "pick_up")) == 3  # teto - wait - other


def test_hostile_world_keeps_survival_threat_objective_convenience_order():
    state = game_state(
        hostiles=[hostile(f"goblin {index}", 6 + index, "N") for index in range(2)],
        hunger="starving", thirst="dehydrated", sleep="drowsy", hour=23.0,
        items=[{"name": "dried meat", "weight_kg": 0.4},
               {"name": "water flask", "weight_kg": 0.5},
               {"name": "healing potion", "weight_kg": 0.3}],
        status="bleeding", blood_current=40, blood_max=100,
        ground=["silver coin"],
        units=[friendly("hunter", 2, "S")],
        region="The Hills", travel_destination="Boatmurdered",
    )
    actions = generate_actions(state, LoopConfig(max_candidates=12))
    ranks = [action.category_rank for action in actions]
    assert ranks == sorted(ranks)  # triagem não decrescente
    assert len(actions) <= 12
    assert actions[-1].id == EXIT_ACTION_ID
    assert actions[-2].id == WAIT_ACTION_ID
    assert {"pick_up", "talk", "navigate_to", "explore", "wait", "other"} <= set(verbs(actions))


# --------------------------------------------------------------- determinismo


def test_generation_is_deterministic():
    state = game_state(hostiles=[hostile("goblin skulker", 6, "NE")], hunger="hungry",
                       items=[{"name": "dried meat"}], region="The Hills",
                       ground=["iron dagger", "copper coin"])
    first = generate_actions(state, CONFIG)
    second = generate_actions(state, CONFIG)
    assert ids(first) == ids(second)
    assert [action.name_en for action in first] == [action.name_en for action in second]
    assert [dict(action.params) for action in first] == [dict(action.params) for action in second]


def test_exit_option_always_present_and_last():
    for state in (game_state(), game_state(hostiles=[hostile()]),
                  game_state(hunger="starving", hour=22.0, sleep="drowsy")):
        actions = generate_actions(state, CONFIG)
        assert actions[-1].id == EXIT_ACTION_ID
        assert actions[-1].is_exit is True
        assert actions[-1].verb == "other"


def test_cap_is_exact_on_both_sides_of_the_limit():
    """Regressão: com o teto no mínimo legal (3) e abaixo dele (2) o corte é exato."""
    busy = game_state(
        hostiles=[hostile("goblin skulker", 6, "NE"), hostile("kobold thief", 3, "S")],
        hunger="starving", thirst="dehydrated", sleep="drowsy", hour=23.0,
        items=[{"name": "dried meat"}, {"name": "water flask"}, {"name": "healing potion"}],
        ground=["iron dagger", "copper coin"], units=[friendly("hunter", 2, "S")],
        region="The Hills", travel_destination="Boatmurdered", status="bleeding",
    )

    at_minimum = generate_actions(busy, LoopConfig(max_candidates=3))
    assert len(at_minimum) == 3                       # teto EXATO (era 3 com max=2)
    assert [action.id for action in at_minimum][-1] == EXIT_ACTION_ID
    assert WAIT_ACTION_ID in ids(at_minimum)
    assert len(set(ids(at_minimum))) == 3             # ids únicos no choice
    assert any(action.category == "survival" for action in at_minimum)

    below = generate_actions(busy, max_candidates=2)
    assert len(below) == 2                            # reservas encolhem, teto não estoura
    assert [action.id for action in below] == [below[0].id, EXIT_ACTION_ID]
    assert below[-1].id == EXIT_ACTION_ID
    assert "wait" not in verbs(below)                 # o wait só reserva com 3+

    only_exit = generate_actions(busy, max_candidates=1)
    assert ids(only_exit) == [EXIT_ACTION_ID]         # caso degenerado: só a saída

    # o teto nunca é excedido, em nenhum tamanho de 1 a 14
    for limit in range(1, 15):
        actions = generate_actions(busy, max_candidates=limit)
        assert len(actions) <= limit, limit
        assert actions[-1].id == EXIT_ACTION_ID, limit
        assert len(set(ids(actions))) == len(actions), limit

    with pytest.raises(ValueError):
        generate_actions(busy, max_candidates=0)


def test_loop_config_refuses_a_cap_below_the_legal_minimum():
    """O mínimo legal é 3 (ação real + wait + saída): o config recusa menos."""
    from system_one_dwarf.agent.types import MIN_MAX_CANDIDATES

    assert MIN_MAX_CANDIDATES == 3
    assert LoopConfig(max_candidates=3).max_candidates == 3
    with pytest.raises(ValueError):
        LoopConfig(max_candidates=2)


def test_candidate_shape_matches_build_action_contract():
    actions = generate_actions(game_state(hostiles=[hostile()]), CONFIG)
    for action in actions:
        candidate = action.as_jev_candidate()
        assert set(candidate) == {"id", "name", "description"}
        assert candidate["id"] and candidate["name"]
        assert action.category in ("survival", "threat", "objective", "convenience")


def test_talk_only_for_nearby_non_hostile_units():
    state = game_state(units=[friendly("hunter", 3, "S"), friendly("merchant", 30, "N")],
                       hostiles=[hostile("goblin", 12, "E")])
    actions = generate_actions(state, LoopConfig(talk_radius=8))
    talks = actions_of(actions, "talk")
    assert [action.target for action in talks] == ["hunter"]
    assert talks[0].params["steps"] == 2


def test_ground_items_need_an_explicit_snapshot_field():
    assert visible_ground_items(game_state(ground=["iron dagger", {"name": "copper coin"}])) == [
        "iron dagger", "copper coin",
    ]
    assert visible_ground_items(game_state()) == []
    # o inventário é o que está CARREGADO: nunca vira "item no chão"
    assert visible_ground_items(game_state(items=[{"name": "iron dagger"}])) == []


# ------------------------------------------------------------------ utilidades


def test_direction_helpers():
    assert normalise_direction("NE") == "ne"
    assert normalise_direction("N +2z") == "n"
    assert normalise_direction("here") == ""
    assert normalise_direction("sideways") == ""
    assert opposite_direction("ne") == "sw"
    assert opposite_direction("") == ""
    assert direction_from_text("a road runs north past the river") == "n"
    assert direction_from_text("", None) == ""
