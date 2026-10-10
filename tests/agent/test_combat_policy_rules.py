"""Políticas de combate (``agent/combat_policy.py``): tabelas de decisão e segurança.

Funções puras e determinísticas — cada teste fixa uma regra documentada
(``PROBLEMAS-E-SOLUCOES.md`` H5, mudança 02 da ``ARQUITETURA.md``): cabeça mata /
pescoço paraliza, perna só para imobilizar, arma presa deixa o menu sem ``stab``,
recuo com sangue < 60% e sapientes só com aprovação humana. Tudo offline.
"""
from __future__ import annotations

import dataclasses
import math
from types import SimpleNamespace

import pytest

from system_one_dwarf.agent import combat_policy as cp
from system_one_dwarf.agent.combat_policy import (
    AimChoice,
    EngagementDecision,
    RetreatDecision,
    WeaponAdvice,
    attack_preference,
    blood_ratio_of,
    choose_aim,
    choose_attack_type,
    engagement_advice,
    rank_attack_types,
    retreat_advice,
    weapon_kind_of,
    weapon_management_advice,
    weapon_state_from_events,
    weapon_state_report,
    wounds_from_events,
)
from system_one_dwarf.game.attack_chain import AttackOption, list_attack_options, next_step

SPEAR_MENU = ["a stab/iron spear", "b bash/shaft/iron spear", "c strike/copper buckler",
              "d punch/hands"]
LODGED_EVENTS = [
    "You stab the stray mule in the upper body from behind with your iron spear, tearing the "
    "muscle and shattering the right floating ribs!",
    "A tendon in the right floating ribs has been torn!",
    "The iron spear has lodged firmly in the wound!",
]


# ================================================================ weapon_kind_of


@pytest.mark.parametrize("name,kind", [
    ("iron spear", "piercing"), ("steel pike", "piercing"), ("copper dagger", "piercing"),
    ("iron pickaxe", "piercing"), ("iron short sword", "edged"), ("silver long sword", "edged"),
    ("copper battle axe", "edged"), ("iron halberd", "edged"), ("steel scimitar", "edged"),
    ("steel war hammer", "blunt"), ("copper mace", "blunt"), ("wooden club", "blunt"),
    ("iron maul", "blunt"), ("☼steel war hammer☼", "blunt"), ("-copper mace-", "blunt"),
    ("wooden training axe", "blunt"), ("wooden training spear", "blunt"),
    ("bone crossbow", "ranged"), ("oak longbow", "ranged"), ("iron bolts", "ranged"),
    ("copper buckler", "shield"), ("iron shield", "shield"), ("steel tower shield", "shield"),
    ("", "unarmed"), (None, "unarmed"), ("   ", "unarmed"), ("fists", "unarmed"),
    ("none", "unarmed"), ("mystery contraption", "unknown"), ("☼☼", "unknown"), (42, "unarmed"),
])
def test_weapon_kind_of_table(name, kind):
    assert weapon_kind_of(name) == kind
    assert kind in cp.WEAPON_KINDS


def test_a_buckler_is_a_shield_never_a_weapon():
    """O menu oferece ``strike/copper buckler``, mas o escudo não conta como arma."""
    assert weapon_kind_of("copper buckler") == "shield"
    assert weapon_kind_of("copper buckler") not in ("edged", "piercing", "blunt")
    advice = weapon_management_advice("broken", "iron spear", ["copper buckler"])
    assert advice.action == "fists"  # o escudo não é arma sobresselente


# ============================================================ attack_preference


@pytest.mark.parametrize("kind,first,last", [
    ("edged", "slash", "kick"), ("piercing", "stab", "kick"), ("blunt", "bash", "kick"),
    ("ranged", "bash", "kick"), ("unarmed", "punch", "bash"),
])
def test_attack_preference_orders_by_weapon_with_punch_last_for_armed(kind, first, last):
    prefs = attack_preference(kind)
    assert (prefs[0], prefs[-1]) == (first, last)
    if kind in ("edged", "piercing", "blunt"):
        assert prefs.index("punch") > prefs.index(first)


def test_attack_preference_defaults_to_the_documented_chain_order():
    assert attack_preference("unknown") == ("stab", "bash", "strike", "punch")
    assert attack_preference(None) == ("stab", "bash", "strike", "punch")
    assert attack_preference(object()) == ("stab", "bash", "strike", "punch")


# =================================================================== choose_aim


@pytest.mark.parametrize("kind", ["edged", "piercing", "blunt", "ranged", "unarmed", "shield",
                                  "unknown", None, "???"])
def test_default_aim_is_the_head_that_kills(kind):
    choice = choose_aim(kind, "animal")
    assert isinstance(choice, AimChoice)
    assert choice.aim == "head" and choice.reason
    aim, reason = choice  # NamedTuple: desempacota como (aim, reason)
    assert (aim, reason) == (choice.aim, choice.reason)


def test_blunt_goes_upper_body_only_when_the_head_is_unreachable():
    assert choose_aim("blunt", "animal", available=["upper body", "lower body", "neck", "head"]).aim == "head"
    low = choose_aim("blunt", "animal", available=["upper body", "lower body", "neck"])
    assert low.aim == "upper body" and "head is not in the menu" in low.reason


def test_edged_and_piercing_fall_back_to_the_neck_before_the_body():
    assert choose_aim("piercing", "animal", available=["upper body", "neck"]).aim == "neck"
    assert choose_aim("edged", "animal", available=["upper body", "lower body"]).aim == "upper body"
    nothing = choose_aim("edged", "animal", available=["left front leg"])
    assert nothing.aim == "head" and "reachable" in nothing.reason  # nunca inventa uma perna
    assert choose_aim("edged", "animal", available=[]).aim == "head"


def test_neck_wound_means_finish_at_the_head():
    for wounds in (["neck"], ["tearing the upper spine's nervous tissue"], ["the throat"],
                   [{"body_part": "neck", "severity": 80}],
                   [SimpleNamespace(body_part="upper spine")]):
        choice = choose_aim("piercing", "animal", wounds=wounds)
        assert choice.aim == "head" and "coup de grace" in choice.reason
    blunt = choose_aim("blunt", "animal", wounds=["neck"], available=["upper body", "neck", "head"])
    assert blunt.aim == "head"


def test_body_wounds_do_not_change_the_lethal_order():
    wounds = ["tearing the left lung", "shattering the right floating ribs", "upper body"]
    assert choose_aim("piercing", "animal", wounds=wounds).aim == "head"


def test_skilled_attacker_paralyses_a_dangerous_target_at_the_neck_first():
    choice = choose_aim("piercing", "dangerous", readiness={"weapon_skill": cp.HIGH_SKILL_LEVEL})
    assert choice.aim == "neck" and "paralyse" in choice.reason
    obj = SimpleNamespace(effective_skill=9)
    assert choose_aim("edged", "great_danger", readiness=obj).aim == "neck"
    # sem skill alta, sem alvo perigoso, ou com arma contundente: continua a cabeça
    assert choose_aim("piercing", "dangerous", readiness={"weapon_skill": 4}).aim == "head"
    assert choose_aim("piercing", "animal", readiness={"weapon_skill": 9}).aim == "head"
    assert choose_aim("blunt", "dangerous", readiness={"weapon_skill": 9}).aim == "head"


def test_low_skill_never_picks_the_small_neck_target():
    choice = choose_aim("piercing", "dangerous", readiness={"weapon_skill": 1})
    assert choice.aim == "head" and "low weapon skill" in choice.reason
    unavailable_head = choose_aim("piercing", "animal", readiness={"skill": 1},
                                  available=["neck", "upper body"])
    assert unavailable_head.aim == "upper body"


@pytest.mark.parametrize("readiness", [None, {}, {"weapon_skill": None}, {"weapon_skill": "9"},
                                       {"weapon_skill": True}, {"weapon_skill": math.nan},
                                       {"weapon_skill": math.inf}, object(), 7, "high"])
def test_unreadable_readiness_means_the_default_policy(readiness):
    assert choose_aim("piercing", "dangerous", readiness=readiness).aim == "head"


def test_legs_are_aimed_only_to_immobilise():
    kinds = ["edged", "piercing", "blunt", "ranged", "unarmed", "shield", "unknown"]
    targets = [None, "animal", "sapient", "dangerous", "fast"]
    wound_sets = [(), ["neck"], ["head"], ["upper body"], ["left leg"], ["left front hoof"]]
    for kind in kinds:
        for target in targets:
            for wounds in wound_sets:
                for available in (None, ["head", "neck", "upper body", "left front leg"], ["left front leg"]):
                    aim = choose_aim(kind, target, wounds=wounds, available=available).aim
                    assert "leg" not in aim and "hoof" not in aim and "foot" not in aim
    held = choose_aim("piercing", "animal", immobilise=True)
    assert held.aim == "leg" and "immobilise" in held.reason
    picked = choose_aim("piercing", "animal", immobilise=True,
                        available=["head", "right hind leg", "left front leg"])
    assert picked.aim == "right hind leg"
    assert choose_aim("blunt", "animal", immobilise=True, available=["head"]).aim == "leg"


def test_sapient_targets_carry_the_approval_note_in_the_reason():
    assert "explicit human approval" in choose_aim("edged", "sapient").reason
    assert "explicit human approval" not in choose_aim("edged", "animal").reason


def test_choose_aim_is_deterministic_and_never_raises_on_hostile_input():
    args = ("piercing", "dangerous", {"weapon_skill": 7}, ["neck"])
    assert choose_aim(*args) == choose_aim(*args)
    hostile = choose_aim(object(), object(), readiness=object(), wounds=object(),
                         available=3, immobilise=object())
    assert isinstance(hostile, AimChoice) and hostile.aim
    gen = choose_aim("piercing", "animal", wounds=(w for w in ["neck"]))
    assert gen.aim == "head"


# ============================================================= choose_attack_type


@pytest.mark.parametrize("kind,expected", [
    ("piercing", "a stab/iron spear"), ("unknown", "a stab/iron spear"),
    ("blunt", "b bash/shaft/iron spear"), ("ranged", "b bash/shaft/iron spear"),
    ("unarmed", "d punch/hands"), ("shield", "d punch/hands"),
])
def test_choose_attack_type_for_the_proven_spear_menu(kind, expected):
    assert choose_attack_type(SPEAR_MENU, kind) == expected


def test_edged_prefers_the_slashing_entry_over_the_stab():
    menu = ["a stab/iron short sword", "b slash/iron short sword", "c bash/iron short sword",
            "d punch/hands"]
    assert choose_attack_type(menu, "edged") == "b slash/iron short sword"
    axe = ["a hack/copper battle axe", "b bash/copper battle axe", "c punch/hands"]
    assert choose_attack_type(axe, "edged") == "a hack/copper battle axe"


def test_lodged_weapon_menus_degrade_gracefully_and_never_invent_a_strike():
    """Ao vivo: com a lança presa o menu perdeu o ``stab`` (e depois tudo)."""
    no_stab = SPEAR_MENU[1:]
    assert choose_attack_type(no_stab, "piercing", weapon_state="lodged") == "b bash/shaft/iron spear"
    only_fists = ["d punch/left hand", "e punch/right hand"]
    assert choose_attack_type(only_fists, "piercing", weapon_state="lodged") == "d punch/left hand"
    assert choose_attack_type([], "piercing", weapon_state="lodged") is None
    assert choose_attack_type(["a grab/left hand", "b throw/item"], "piercing") is None
    ranked = rank_attack_types(no_stab, "piercing", weapon_state="lodged")
    assert [r.verb for r in ranked] == ["bash", "strike", "punch"]
    assert "weapon lodged" in ranked[0].reason


def test_choose_attack_type_accepts_labels_objects_dicts_and_generators():
    options = list_attack_options({
        "focus": ["dungeonmode/Attack"],
        "lines": ["", "Attack head:", " a stab/iron spear", " d punch/hands"]})
    assert choose_attack_type(options, "piercing") is options[0]  # devolve o elemento original
    assert isinstance(choose_attack_type(options, "unarmed"), AttackOption)
    dicts = [{"label": "a bash/shaft/iron spear"}, {"text": "b stab/iron spear"}, {"name": "c punch"}]
    assert choose_attack_type(dicts, "piercing") is dicts[1]
    objs = [SimpleNamespace(text="a punch/hands"), SimpleNamespace(label="b stab/iron spear")]
    assert choose_attack_type(objs, "piercing") is objs[1]
    assert choose_attack_type((o for o in SPEAR_MENU[2:]), "piercing") == "c strike/copper buckler"
    assert choose_attack_type("a stab/iron spear", "piercing") == "a stab/iron spear"
    assert choose_attack_type([None, 3, object(), "", {"x": 1}], "piercing") is None


def test_rank_attack_types_is_stable_for_ties_and_complete():
    menu = ["a punch/left hand", "b punch/right hand", "c stab/iron spear"]
    ranked = rank_attack_types(menu, "piercing")
    assert [r.entry for r in ranked] == ["c stab/iron spear", "a punch/left hand", "b punch/right hand"]
    assert ranked[0].score < ranked[1].score <= ranked[2].score
    unlisted = rank_attack_types(["a bite/teeth", "b stab/iron spear"], "piercing")
    assert [r.verb for r in unlisted] == ["stab", "bite"]
    assert "not in the weapon preference" in unlisted[1].reason


# ============================================================ wounds_from_events


def test_wounds_from_events_lists_the_parts_the_adventurer_already_hit():
    events = [
        "You stab the stray mule in the upper body from the side with your iron spear, tearing "
        "the muscle and tearing the left lung!",
        "The stray mule is having trouble breathing!",
        "You stab the stray mule in the upper body from behind with your iron spear!",
        "You stab the stray mule in the neck from the side with your iron spear, tearing the "
        "upper spine's nervous tissue!",
        "The stray mule falls over.",
    ]
    assert wounds_from_events(events) == ("upper body", "neck")
    assert wounds_from_events(events, "Mule (tame)") == ("upper body", "neck")
    assert wounds_from_events(events, "Reindeer Cow (tame)") == ()
    assert choose_aim("piercing", "animal", wounds=wounds_from_events(events)).aim == "head"
    assert wounds_from_events(["You punch the stray rabbit in the head!"], "Buck Rabbit") == ("head",)


def test_wounds_from_events_ignores_blows_taken_and_garbage():
    taken = ["The stray mule kicks you in the head!", "The goblin stabs you in the neck!",
             "You attack the stray mule but He jumps away!", None, 5, object(), ""]
    assert wounds_from_events(taken) == ()
    assert wounds_from_events(None) == ()
    assert wounds_from_events(e for e in ["You bash the stray mule in the head."]) == ("head",)
    assert wounds_from_events([{"text": "You slash the stray mule in the neck."}]) == ("neck",)


# ========================================================= weapon_state_from_events


@pytest.mark.parametrize("events,expected", [
    ([], "ok"),
    (["You walk north."], "ok"),
    (["The iron spear has lodged firmly in the wound!"], "lodged"),
    (["You maintain possession of the iron spear."], "lodged"),
    (LODGED_EVENTS, "lodged"),
    (LODGED_EVENTS + ["You maintain possession of the iron spear."], "lodged"),
    (["The iron spear has lodged firmly in the wound!",
      "You stab the stray mule in the head from behind with your iron spear, tearing the brain!"],
     "ok"),  # golpe seguinte do aventureiro: a arma saiu
    (["You stab the stray mule in the neck with your iron spear, tearing the nerves!",
      "The iron spear has lodged firmly in the wound!"], "lodged"),  # mesmo golpe volta a prender
    (["The iron spear has lodged firmly in the wound!", "The iron spear comes free."], "ok"),
    (["You drop the iron spear."], "dropped"),
    (["Your iron spear is knocked from your grasp!"], "dropped"),
    (["You drop the iron spear.", "You pick up the iron spear."], "ok"),
    (["Your iron spear shatters!"], "broken"),
    (["The goblin drops the iron sword."], "ok"),  # outras unidades não contam
    (["The goblin maintains possession of the iron sword."], "ok"),
    (["The goblin's iron spear has lodged firmly in the wound!"], "ok"),
    (["The stray mule is stuck in the web."], "ok"),
    (["The goblin's iron sword shatters!"], "ok"),
    (["The stray mule is having trouble breathing!"], "ok"),
    (["You stab the stray mule in the upper body with your iron spear, shattering the ribs!"], "ok"),
])
def test_weapon_state_from_events_table(events, expected):
    assert weapon_state_from_events(events) == expected
    assert expected in cp.WEAPON_STATES


def test_weapon_state_report_names_the_weapon_and_quotes_the_line():
    report = weapon_state_report(LODGED_EVENTS)
    assert (report.state, report.weapon) == ("lodged", "iron spear")
    assert report.evidence == "The iron spear has lodged firmly in the wound!"
    kept = weapon_state_report(LODGED_EVENTS + ["You maintain possession of the iron spear."])
    assert kept.weapon == "iron spear"
    assert weapon_state_report(["You drop the iron spear."]).weapon == "iron spear"
    assert weapon_state_report([]) == ("ok", "", "")


def test_weapon_state_reads_game_events_dicts_generators_and_hostile_text():
    game_event = SimpleNamespace(text="The iron spear has lodged firmly in the wound!", category="combat")
    assert weapon_state_from_events([game_event]) == "lodged"
    assert weapon_state_from_events([{"text": "You drop the iron spear."}]) == "dropped"
    assert weapon_state_from_events(e for e in LODGED_EVENTS) == "lodged"
    assert weapon_state_from_events("The iron spear has lodged firmly in the wound!") == "lodged"
    assert weapon_state_from_events([None, 5, object(), "", "\x1b[31m"]) == "ok"
    noisy = "x" * 10_000 + " The iron spear has lodged firmly in the wound!"
    assert weapon_state_from_events([noisy]) == "ok"  # linha cortada nos 300 caracteres
    many = ["noise"] * 1000 + ["The iron spear has lodged firmly in the wound!"]
    assert weapon_state_from_events(many) == "ok"  # só os últimos MAX_EVENTS contam... mas é o último
    assert weapon_state_from_events(["The iron spear has lodged firmly in the wound!"] + ["noise"] * 5) == "lodged"


# ==================================================== weapon_management_advice


def test_lodged_weapon_advice_is_to_step_away_from_the_target():
    advice = weapon_management_advice("lodged", "iron spear", ["iron short sword"], target_direction="E")
    assert isinstance(advice, WeaponAdvice)
    assert (advice.action, advice.item, advice.direction) == ("step_away", "iron spear", "w")
    assert "one tile" in advice.reason
    assert weapon_management_advice("lodged", "iron spear", target_direction="nw").direction == "se"
    assert weapon_management_advice("lodged", "iron spear", target_direction="up").direction == ""
    assert weapon_management_advice("lodged", "iron spear").direction == ""


def test_dropped_and_broken_weapon_advice():
    spare = ["copper buckler", "leather armor", "iron short sword", "steel war hammer"]
    assert weapon_management_advice("dropped", "iron spear", spare) == WeaponAdvice(
        "swap", "iron short sword", "weapon dropped: a spare weapon is faster than fetching it", "")
    assert weapon_management_advice("dropped", "iron spear", []).action == "pick_up"
    assert weapon_management_advice("dropped", "iron spear", []).item == "iron spear"
    assert weapon_management_advice("broken", "iron spear", spare).action == "swap"
    assert weapon_management_advice("broken", "iron spear", ["iron spear"]).action == "fists"
    assert weapon_management_advice("broken", "iron spear", ["copper buckler"]).action == "fists"


def test_ok_and_unknown_states_keep_the_weapon():
    assert weapon_management_advice("ok", "iron spear").action == "keep"
    assert weapon_management_advice("???", "iron spear").action == "keep"
    assert weapon_management_advice(None, None).action == "keep"
    gen = weapon_management_advice("broken", "x", (n for n in ["iron mace"]))
    assert (gen.action, gen.item) == ("swap", "iron mace")


# ================================================================ retreat_advice


@pytest.mark.parametrize("kwargs,retreat,urgency", [
    ({}, False, "none"),
    (dict(blood_ratio=1.0), False, "none"),
    (dict(blood_ratio=0.85), False, "none"),
    (dict(blood_ratio=0.79), False, "low"),
    (dict(blood_ratio=0.6), False, "low"),  # exatamente no limiar não recua
    (dict(blood_ratio=0.59), True, "medium"),
    (dict(blood_ratio=0.35), True, "medium"),
    (dict(blood_ratio=0.34), True, "critical"),
    (dict(blood_ratio=0.0), True, "critical"),
    (dict(pain=0), False, "none"),
    (dict(pain=cp.PAIN_HIGH - 0.1), False, "none"),
    (dict(pain=cp.PAIN_HIGH), True, "medium"),
    (dict(pain=cp.PAIN_SEVERE), True, "high"),
    (dict(stunned=True), True, "high"),
    (dict(exhausted=True), True, "medium"),
    (dict(outnumbered=True), True, "medium"),
    (dict(great_danger=True), True, "medium"),
    (dict(blood_ratio=0.5, exhausted=True), True, "high"),
    (dict(blood_ratio=0.5, pain=cp.PAIN_HIGH), True, "high"),
    (dict(exhausted=True, outnumbered=True), True, "high"),
    (dict(stunned=True, exhausted=True), True, "critical"),
    (dict(blood_ratio=0.3, stunned=True), True, "critical"),
    (dict(blood_ratio=0.7, exhausted=True), True, "medium"),  # a cautela não conta como fator forte
    (dict(blood_ratio=0.7, pain=10), False, "low"),
    (dict(blood_ratio=50), True, "medium"),  # percentagem tolerada
    (dict(blood_ratio=150), False, "none"),  # 150% não é um rácio: ignora
    (dict(blood_ratio=-0.2), False, "none"),
    (dict(blood_ratio=True), False, "none"),
    (dict(blood_ratio="0.1"), False, "none"),  # texto não é número
    (dict(pain=math.nan, blood_ratio=math.inf), False, "none"),
])
def test_retreat_matrix_with_the_default_threshold(kwargs, retreat, urgency):
    decision = retreat_advice(**kwargs)
    assert isinstance(decision, RetreatDecision)
    assert (decision.retreat, decision.urgency) == (retreat, urgency)
    assert decision.urgency in cp.URGENCY_LEVELS
    assert decision.level == cp.URGENCY_LEVELS.index(urgency)
    assert bool(decision.reasons) == (urgency != "none")


def test_retreat_reasons_name_the_factors_in_english():
    decision = retreat_advice(0.5, cp.PAIN_SEVERE, True, True, True, True)
    text = " | ".join(decision.reasons)
    for needle in ("blood 50%", "stunned", "severe pain", "exhausted", "outnumbered",
                   "great danger", "compound"):
        assert needle in text
    assert decision.urgency == "critical"


def test_retreat_threshold_is_configurable_and_clamped():
    assert retreat_advice(0.7, threshold=0.8).retreat is True
    assert retreat_advice(0.7, threshold=0.6).retreat is False
    assert retreat_advice(0.1, threshold=0.2).urgency == "critical"
    assert retreat_advice(0.25, threshold=0.2).urgency == "low"
    assert retreat_advice(0.9, threshold=5).retreat is True  # limiar preso a 0.95: 0.9 < 0.95
    assert retreat_advice(0.96, threshold=5).retreat is False
    assert retreat_advice(0.04, threshold=-1).urgency == "critical"  # → 0.05
    for junk in ("x", None, math.nan, object()):
        assert retreat_advice(0.59, threshold=junk).retreat is True  # volta ao 0.6 por omissão
        assert retreat_advice(0.61, threshold=junk).retreat is False


@pytest.mark.parametrize("current,maximum,expected", [
    (4000, 5000, 0.8), (5000, 5000, 1.0), (6000, 5000, 1.0), (0, 5000, 0.0),
    (None, 5000, None), (4000, None, None), (4000, 0, None), (-1, 5000, None),
    ("4000", 5000, None), (True, 5000, None), (math.nan, 5000, None),
])
def test_blood_ratio_of_is_defensive(current, maximum, expected):
    got = blood_ratio_of(current, maximum)
    assert got == expected if expected is not None else got is None


def test_retreat_decision_is_immutable_and_deterministic():
    decision = retreat_advice(0.5, exhausted=True)
    assert decision == retreat_advice(0.5, exhausted=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.retreat = False  # type: ignore[misc]


# =============================================================== engagement_advice


def test_a_passive_animal_in_good_shape_is_a_go():
    decision = engagement_advice("Mule (tame)", "animal", blood_ratio=0.95, pain=2)
    assert isinstance(decision, EngagementDecision)
    assert decision.decision == "go" and decision.go and not decision.avoid
    assert decision.decision in cp.DECISIONS


@pytest.mark.parametrize("kwargs", [
    dict(target_name="Hode Ijamosman", target_kind="sapient"),
    dict(target_name="Goblin"),
    dict(target_name="Dwarf (hostile)", target_hostile=True),
    dict(target_name='Hode "Clockshadows", Swordsman'),
    dict(target_name="the wild boar man"),
])
def test_sapient_targets_always_need_human_approval(kwargs):
    decision = engagement_advice(**kwargs)
    assert decision.needs_approval and not decision.go
    assert any("sapient" in reason for reason in decision.reasons)
    approved = engagement_advice(**kwargs, approved=True)
    assert approved.go and "explicitly approved" in " ".join(approved.reasons)


def test_great_danger_needs_approval_and_is_not_a_self_state_retreat():
    decision = engagement_advice("Titan", "animal", great_danger=True)
    assert decision.needs_approval and decision.urgency == "none"
    assert engagement_advice("Titan", "animal", great_danger=True, approved=True).go


def test_weapon_loss_needs_approval_but_a_lodged_weapon_does_not():
    assert engagement_advice("Mule", "animal", weapon_state="dropped").needs_approval
    assert engagement_advice("Mule", "animal", weapon_state="broken").needs_approval
    assert engagement_advice("Mule", "animal", weapon_state="lodged").go
    assert engagement_advice("Mule", "animal", weapon_state="broken", approved=True).go


@pytest.mark.parametrize("kwargs", [
    dict(blood_ratio=0.3), dict(blood_ratio=0.5), dict(stunned=True), dict(outnumbered=True),
    dict(exhausted=True), dict(pain=95), dict(blood_ratio=0.7, exhausted=True, pain=50),
])
def test_own_state_asking_for_a_retreat_means_avoid_even_with_approval(kwargs):
    decision = engagement_advice("Mule (tame)", "animal", **kwargs)
    assert decision.avoid and decision.reasons
    sapient = engagement_advice("Goblin", "sapient", approved=True, **kwargs)
    assert sapient.avoid  # a aprovação humana não levanta um recuo de estado próprio
    assert decision.urgency in ("medium", "high", "critical")


def test_avoid_wins_over_needs_approval_and_the_threshold_flows_through():
    assert engagement_advice("Goblin", "sapient", blood_ratio=0.2).avoid
    assert engagement_advice("Mule", "animal", blood_ratio=0.7).go
    assert engagement_advice("Mule", "animal", blood_ratio=0.7, threshold=0.8).avoid


def test_engagement_reads_a_visible_unit_like_the_candidate_generator_does():
    from system_one_dwarf.game.models import VisibleUnit

    mule = VisibleUnit(name="Mule (tame)", race="mule", kind="animal", distance=1)
    assert engagement_advice(mule).go
    goblin = VisibleUnit(name="Urist", race="goblin", hostile=True)  # a raça entrega-o
    assert engagement_advice(goblin).needs_approval
    titan = VisibleUnit(name="Mule", kind="animal", great_danger=True)
    assert engagement_advice(titan).needs_approval
    person = VisibleUnit(name="Hode", race="human", kind="sapient")
    assert engagement_advice(person, approved=True).go
    wolf = VisibleUnit(name="Wolf", kind="animal", hostile=True)
    assert any("self-defence" in r for r in engagement_advice(wolf).reasons)
    assert engagement_advice(SimpleNamespace(name="Rabbit")).go


def test_hostile_non_sapient_is_self_defence():
    decision = engagement_advice("Wolf", "animal", target_hostile=True)
    assert decision.go and any("self-defence" in r for r in decision.reasons)


def test_engagement_is_deterministic_and_never_raises():
    kwargs = dict(target_name="Mule", target_kind="animal", blood_ratio=0.9)
    assert engagement_advice(**kwargs) == engagement_advice(**kwargs)
    hostile = engagement_advice(object(), object(), blood_ratio=object(), pain=object(),
                                weapon_state=object(), threshold=object())
    assert isinstance(hostile, EngagementDecision)
    assert engagement_advice(None, None).go


# ======================================================= todas as funções: segurança

HOSTILE_ARGS = [None, object(), 1e308, -1, "\x00" * 50, "x" * 5000, [object()], {"a": object()},
                (1, 2), math.nan, True]


@pytest.mark.parametrize("junk", HOSTILE_ARGS)
def test_no_public_function_raises_on_hostile_input(junk):
    assert weapon_kind_of(junk) in cp.WEAPON_KINDS
    assert attack_preference(junk)
    assert choose_aim(junk, junk, junk, junk).aim
    assert choose_aim("piercing", junk, available=junk, immobilise=junk).aim
    choose_attack_type(junk, junk, weapon_state=junk, aim=junk)
    assert rank_attack_types(junk, junk) is not None
    assert weapon_state_from_events(junk) in cp.WEAPON_STATES
    assert weapon_management_advice(junk, junk, junk, target_direction=junk).action
    assert retreat_advice(junk, junk, junk, junk, junk, junk, junk).urgency in cp.URGENCY_LEVELS
    assert engagement_advice(junk, junk, blood_ratio=junk, pain=junk, weapon_state=junk,
                             threshold=junk).decision in cp.DECISIONS


def test_internal_failures_fall_back_to_safe_neutral_values(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(cp, "_effective_kind", boom)
    assert choose_aim("piercing", "animal") == AimChoice(
        "head", "internal error: documented lethal aim")
    monkeypatch.setattr(cp, "retreat_advice", boom)
    assert engagement_advice("Mule", "animal").needs_approval  # na dúvida pergunta ao humano
    monkeypatch.setattr(cp, "_clean", boom)
    assert weapon_state_from_events(LODGED_EVENTS) == "ok"  # sem dados fiáveis: neutro
    assert weapon_management_advice("lodged", "spear").action == "keep"


# ================================================ política + cadeia de menus juntas


def _menu(*entries, part="head"):
    lines = [""] * 72
    lines[33] = " " * 45 + f"Attack {part}:"
    for i, entry in enumerate(entries):
        lines[34 + i] = " " * 45 + f"{chr(97 + i)} {entry}"
    return {"focus": ["dungeonmode/Attack"], "lines": lines}


@pytest.mark.parametrize("weapon,entries,expected_reason", [
    ("iron spear", ["stab/iron spear", "bash/shaft/iron spear", "punch/hands"], "attack_selected:stab"),
    ("iron spear", ["bash/shaft/iron spear", "punch/hands"], "attack_selected:bash"),  # presa
    ("iron spear", ["punch/left hand", "punch/right hand"], "attack_selected:punch"),
    ("iron short sword", ["stab/iron short sword", "slash/iron short sword"], "attack_selected:slash"),
    ("steel war hammer", ["bash/steel war hammer", "punch/hands"], "attack_selected:bash"),
    ("", ["punch/left hand", "kick/left foot"], "attack_selected:punch"),
])
def test_policy_preference_drives_the_chain_to_the_same_entry(weapon, entries, expected_reason):
    prefs = attack_preference(weapon_kind_of(weapon))
    step = next_step(_menu(*entries), "Mule (tame)", attack_pref=prefs)
    assert step.reason == expected_reason
    options = list_attack_options(_menu(*entries))
    chosen = choose_attack_type(options, weapon_kind_of(weapon))
    assert chosen.verb == expected_reason.split(":")[1]  # as duas camadas concordam


def test_aim_choice_names_are_understood_by_the_chain_vocabulary():
    from system_one_dwarf.game.attack_chain import canonical_aim

    for kind in cp.WEAPON_KINDS:
        for wounds in ([], ["neck"]):
            aim = choose_aim(kind, "animal", wounds=wounds, immobilise=False).aim
            assert canonical_aim(aim) == aim
    assert canonical_aim(choose_aim("blunt", "animal", immobilise=True).aim) == "leg"
