"""Classes de risco da ação e limiares de ``auto`` por classe (mudança 06).

Cobre o mapeamento do vocabulário do loop, os pisos da política (§5.3 de
``docs/calibracao.md``), a regra "a calibração só sobe", o teto de injeção e o
contrato de nunca levantar por entrada inválida.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pytest

from system_one_dwarf.agent.candidates import SAPIENT_WORDS, generate_actions
from system_one_dwarf.agent.types import LoopConfig, MacroAction
from system_one_dwarf.game.models import GameState
from system_one_dwarf.jev import risk
from system_one_dwarf.jev.bands import band_for_choice_or_score, band_for_noul
from system_one_dwarf.jev.risk import (
    DEFAULT_POLICY,
    RISK_CLASSES,
    RISK_COSTLY,
    RISK_DESTRUCTIVE,
    RISK_IRREVERSIBLE,
    RISK_REVERSIBLE,
    RiskPolicy,
)


# ------------------------------------------------------------------- helpers


@dataclass
class Entry:
    """Entrada de calibração (a forma de ``ClassCalibration``, sem importar a tabela)."""

    threshold: float | None
    verified: bool = False
    support: int = 0


class Table:
    """Tabela mínima com o contrato duck-typed que ``risk`` espera (``entry_for``)."""

    def __init__(self, **entries: Entry) -> None:
        self.entries = entries

    def entry_for(self, risk_class: str) -> Entry | None:
        return self.entries.get(risk_class)


def action(verb: str, id_: str | None = None, **params) -> MacroAction:
    category = params.pop("category", "objective")
    return MacroAction(
        id=id_ or f"{verb}:target", name_en=verb, description_en=verb, target=params.pop("target", None),
        verb=verb, category=category, params=params,
    )


# ---------------------------------------------------------------- constantes


def test_class_order_and_policy_floors_are_monotonic():
    assert RISK_CLASSES == ("reversible", "costly", "irreversible", "destructive")
    assert risk.POLICY_FLOORS == {
        "reversible": 0.90, "costly": 0.95, "irreversible": 0.95, "destructive": 0.97,
    }
    floors = [risk.POLICY_FLOORS[c] for c in RISK_CLASSES]
    assert floors == sorted(floors)
    assert [risk.risk_rank(c) for c in RISK_CLASSES] == [0, 1, 2, 3]
    assert risk.risk_rank("nonsense") == 3  # inválido ⇒ o mais severo


def test_target_risk_is_one_minus_floor():
    assert risk.target_risk(RISK_REVERSIBLE) == pytest.approx(0.10)
    assert risk.target_risk(RISK_COSTLY) == pytest.approx(0.05)
    assert risk.target_risk(RISK_DESTRUCTIVE) == pytest.approx(0.03)


def test_default_destructive_has_auto_disabled():
    assert risk.DESTRUCTIVE_AUTO_DEFAULT is False
    assert risk.auto_allowed(RISK_REVERSIBLE) and risk.auto_allowed(RISK_IRREVERSIBLE)
    assert not risk.auto_allowed(RISK_DESTRUCTIVE)
    assert risk.auto_allowed(RISK_DESTRUCTIVE, RiskPolicy(destructive_auto=True))


# ------------------------------------------------------------- classificação


@pytest.mark.parametrize("verb", ["explore", "wait", "navigate_to", "talk", "rest", "pick_up"])
def test_reversible_vocabulary(verb):
    assert risk.risk_class(verb) == RISK_REVERSIBLE
    assert risk.risk_class(f"{verb}:somewhere") == RISK_REVERSIBLE


@pytest.mark.parametrize("verb", ["eat", "drink", "drink_potion", "flee", "hunt"])
def test_costly_vocabulary(verb):
    assert risk.risk_class(verb) == RISK_COSTLY
    assert risk.risk_class(f"{verb}:x") == RISK_COSTLY


def test_exit_option_is_reversible():
    assert risk.risk_class("other") == RISK_REVERSIBLE
    assert risk.risk_class(action("other", "other")) == RISK_REVERSIBLE


def test_attack_on_non_sapient_is_irreversible():
    assert risk.risk_class("attack:stray_mule") == RISK_IRREVERSIBLE
    assert risk.risk_class("attack:giant_cave_spider") == RISK_IRREVERSIBLE
    prey = action("attack", "attack:deer", target="deer", kind="animal")
    assert risk.risk_class(prey) == RISK_IRREVERSIBLE


def test_attack_on_sapient_is_destructive():
    assert risk.risk_class("attack:human_farmer") == RISK_DESTRUCTIVE
    assert risk.risk_class("attack:goblin_skulker") == RISK_DESTRUCTIVE
    assert risk.risk_class(action("attack", "attack:wanderer", target="wanderer", kind="sapient")) == RISK_DESTRUCTIVE
    # por nome, mesmo sem ``kind``
    assert risk.risk_class(action("attack", "attack:x", target="dwarf merchant")) == RISK_DESTRUCTIVE


def test_hostile_sapient_follows_the_policy_constant():
    threat = action("attack", "attack:kobold_thief", target="kobold thief", category="threat")
    assert risk.risk_class(threat) == RISK_DESTRUCTIVE  # política por omissão
    relaxed = RiskPolicy(hostile_sapient_class=RISK_IRREVERSIBLE)
    assert risk.risk_class(threat, policy=relaxed) == RISK_IRREVERSIBLE
    flagged = action("attack", "attack:kobold_thief", target="kobold thief", hostile=True)
    assert risk.risk_class(flagged, policy=relaxed) == RISK_IRREVERSIBLE
    # a exceção de legítima defesa nunca desprotege um sapiente NÃO hostil...
    friendly = action("attack", "attack:villager", target="villager", kind="sapient")
    assert risk.risk_class(friendly, policy=relaxed) == RISK_DESTRUCTIVE
    # ...nem um id sem contexto (não se sabe se é um agressor)
    assert risk.risk_class("attack:kobold_thief", policy=relaxed) == RISK_DESTRUCTIVE


def test_destructive_flag_and_verbs():
    assert risk.risk_class(action("explore", "explore:x", destructive=True)) == RISK_DESTRUCTIVE
    assert risk.risk_class({"id": "steal:from_caravan"}) == RISK_DESTRUCTIVE
    assert risk.risk_class({"verb": "talk", "params": {"destructive": "yes"}}) == RISK_DESTRUCTIVE


def test_annotation_can_only_raise_the_class():
    low = action("attack", "attack:wolf", target="wolf", risk_class="reversible")
    assert risk.risk_class(low) == RISK_IRREVERSIBLE  # nunca baixa
    high = action("explore", "explore:x", risk_class="irreversible")
    assert risk.risk_class(high) == RISK_IRREVERSIBLE


def test_unknown_or_empty_verbs_are_conservative_not_reversible():
    for value in ("teleport:home", "zzz", "", None, 42, object(), {}, {"id": ""}):
        assert risk.risk_class(value) == risk.UNKNOWN_VERB_CLASS == RISK_IRREVERSIBLE
    verdict = risk.classify_action("teleport:home")
    assert "unknown verb" in verdict.reason


def test_verb_attribute_wins_over_id_prefix():
    # o loop usa ``drink:healing_potion`` com verb ``drink_potion``; ambos custam 0.95
    potion = action("drink_potion", "drink:healing_potion")
    assert risk.risk_class(potion) == RISK_COSTLY
    nav = action("navigate_to", "navigate:cragmouth")
    assert risk.risk_class(nav) == RISK_REVERSIBLE


def test_sapient_words_cover_the_candidate_generator_list():
    missing = set(SAPIENT_WORDS) - set(risk.SAPIENT_NAME_WORDS)
    assert not missing, f"risk.SAPIENT_NAME_WORDS is missing {sorted(missing)}"


def test_classes_of_the_actions_generate_actions_really_emits():
    state = GameState.from_dict({
        "metrics": {
            "health_injuries": {"status": "healthy"},
            "needs_state": {"hunger": "hungry", "thirst": "thirsty", "sleep": "drowsy"},
            "inventory": {"items": [{"name": "dried meat"}, {"name": "waterskin"},
                                    {"name": "healing potion"}], "equipped_weapon": "iron spear"},
            "threats": {"count": 1, "level": "high", "hostiles": [
                {"name": "wolf", "distance": 3, "direction": "N", "hostile": True},
                {"name": "goblin skulker", "distance": 8, "direction": "E", "hostile": True}]},
            "units_nearby": {"units": [
                {"name": "deer", "distance": 4, "direction": "S", "kind": "animal"},
                {"name": "human farmer", "distance": 5, "direction": "W", "kind": "sapient"}]},
            "location": {"region": "The Hills", "surroundings": "a field"},
            "game_clock": {"hour": 23},
        },
    })
    expected_verb_class = {
        "attack:wolf": RISK_IRREVERSIBLE, "attack:goblin_skulker": RISK_DESTRUCTIVE,
        "attack:deer": RISK_IRREVERSIBLE, "wait:one_turn": RISK_REVERSIBLE, "other": RISK_REVERSIBLE,
    }
    actions = generate_actions(state, LoopConfig(), max_candidates=40, objective="hunt an animal")
    ids = {a.id for a in actions}
    for action_id, klass in expected_verb_class.items():
        assert action_id in ids
        found = next(a for a in actions if a.id == action_id)
        assert risk.risk_class(found) == klass, action_id
    for generated in actions:  # nenhuma ação gerada cai em "desconhecida" por omissão
        assert generated.verb in risk.VERB_CLASSES, generated.verb
    assert not any(a.id == "attack:human_farmer" for a in actions)


# ------------------------------------------------------------------ limiares


def test_floors_without_calibration():
    assert {c: risk.thresholds_for(c) for c in RISK_CLASSES} == {
        "reversible": 0.90, "costly": 0.95, "irreversible": 0.95, "destructive": 0.97,
    }
    assert risk.class_thresholds() == {c: risk.POLICY_FLOORS[c] for c in RISK_CLASSES}


def test_base_threshold_is_a_floor_for_every_class():
    assert risk.thresholds_for(RISK_REVERSIBLE, base=0.93) == pytest.approx(0.93)
    assert risk.thresholds_for(RISK_COSTLY, base=0.93) == pytest.approx(0.95)
    assert risk.thresholds_for(RISK_IRREVERSIBLE, base=0.99) == pytest.approx(0.99)
    assert risk.thresholds_for(RISK_REVERSIBLE, base="garbage") == pytest.approx(0.90)


def test_calibration_may_raise_a_threshold():
    table = Table(reversible=Entry(0.94, support=40), irreversible=Entry(0.985, support=12))
    assert risk.thresholds_for(RISK_REVERSIBLE, table) == pytest.approx(0.94)
    assert risk.thresholds_for(RISK_IRREVERSIBLE, table) == pytest.approx(0.985)
    assert risk.thresholds_for(RISK_COSTLY, table) == pytest.approx(0.95)  # sem entrada


def test_calibration_never_lowers_an_unverified_threshold():
    table = Table(reversible=Entry(0.80, verified=False, support=500),
                  irreversible=Entry(0.70, verified=False, support=500))
    assert risk.thresholds_for(RISK_REVERSIBLE, table) == pytest.approx(0.90)
    assert risk.thresholds_for(RISK_IRREVERSIBLE, table) == pytest.approx(0.95)


def test_verified_entry_needs_enough_support_to_lower():
    thin = Table(reversible=Entry(0.85, verified=True, support=risk.MIN_SUPPORT_TO_LOWER - 1))
    assert risk.thresholds_for(RISK_REVERSIBLE, thin) == pytest.approx(0.90)
    solid = Table(reversible=Entry(0.85, verified=True, support=risk.MIN_SUPPORT_TO_LOWER))
    assert risk.thresholds_for(RISK_REVERSIBLE, solid) == pytest.approx(0.85)


def test_verified_lowering_has_an_absolute_floor():
    table = Table(costly=Entry(0.55, verified=True, support=10_000))
    assert risk.thresholds_for(RISK_COSTLY, table) == pytest.approx(risk.LOWEST_AUTO_THRESHOLD)


def test_destructive_is_never_lowered_even_if_verified():
    table = Table(destructive=Entry(0.60, verified=True, support=10_000))
    assert risk.thresholds_for(RISK_DESTRUCTIVE, table) == pytest.approx(0.97)


def test_garbage_calibration_is_ignored():
    for junk in (object(), "x", 7, [1], {"classes": "nope"}, Table(reversible=Entry(float("nan")))):
        assert risk.thresholds_for(RISK_REVERSIBLE, junk) == pytest.approx(0.90)
    assert risk.thresholds_for("not-a-class") == pytest.approx(0.95)  # conservadora
    mapping = {"classes": {"reversible": {"threshold": 0.93, "verified": False, "support": 5}}}
    assert risk.thresholds_for(RISK_REVERSIBLE, mapping) == pytest.approx(0.93)
    assert risk.thresholds_for(RISK_REVERSIBLE, {"reversible": 0.92}) == pytest.approx(0.92)


def test_threshold_never_leaves_unit_interval():
    huge = Table(reversible=Entry(7.5, support=50))
    assert risk.thresholds_for(RISK_REVERSIBLE, huge) == 1.0
    assert 0.0 <= risk.thresholds_for(RISK_REVERSIBLE, base=-3) <= 1.0


def test_auto_threshold_for_action_uses_the_class():
    assert risk.auto_threshold_for_action("explore:north") == pytest.approx(0.90)
    assert risk.auto_threshold_for_action("attack:stray_mule") == pytest.approx(0.95)
    assert risk.auto_threshold_for_action("attack:human_farmer") == pytest.approx(0.97)
    table = Table(irreversible=Entry(0.98, support=30))
    assert risk.auto_threshold_for_action("attack:stray_mule", 0.90, table) == pytest.approx(0.98)
    assert risk.auto_threshold_for_action("explore:north", 0.92) == pytest.approx(0.92)


# ---------------------------------------------------------- injeção e bandas


def test_has_injection_signal_forms():
    assert risk.has_injection_signal(True) is True
    assert risk.has_injection_signal(False) is False
    assert risk.has_injection_signal(0.5) is True
    assert risk.has_injection_signal(0.49) is False
    assert risk.has_injection_signal({"noul": 0.9}) is True
    assert risk.has_injection_signal("true") is True
    assert risk.has_injection_signal(None) is False
    assert risk.has_injection_signal(float("nan")) is False

    @dataclass
    class Answer:
        value: float

    assert risk.has_injection_signal(Answer(0.97)) is True
    assert risk.has_injection_signal(Answer(0.02)) is False


def test_band_for_action_applies_the_class_threshold():
    assert risk.band_for_action(0.92, "explore:x") == "auto"
    assert risk.band_for_action(0.92, "attack:stray_mule") == "hitl"
    assert risk.band_for_action(0.96, "attack:stray_mule") == "auto"
    assert risk.band_for_action(0.30, "attack:stray_mule") == "abstain"
    assert risk.band_for_action(0.945, "flee:s") == "hitl"
    assert risk.band_for_action(0.95, "flee:s") == "auto"  # limite inclusivo


def test_band_for_action_matches_the_plain_bands_for_reversible_actions():
    for confidence in (None, 0.0, 0.2, 0.5, 0.7, 0.89, 0.9, 0.99, 1.0):
        assert risk.band_for_action(confidence, "explore:x") == band_for_choice_or_score(confidence)


def test_destructive_actions_never_reach_auto_by_default():
    for confidence in (0.97, 0.99, 1.0):
        assert risk.band_for_action(confidence, "attack:human_farmer") == "hitl"
    optin = RiskPolicy(destructive_auto=True)
    assert risk.band_for_action(0.96, "attack:human_farmer", policy=optin) == "hitl"
    assert risk.band_for_action(0.97, "attack:human_farmer", policy=optin) == "auto"
    assert risk.band_ceiling("attack:human_farmer") == "hitl"
    assert risk.band_ceiling("attack:human_farmer", policy=optin) == "auto"


def test_has_injection_caps_every_class_at_hitl():
    for target in ("explore:x", "flee:s", "attack:stray_mule", "other", None):
        assert risk.band_for_action(1.0, target, has_injection=True) == "hitl"
    assert risk.band_ceiling("explore:x", has_injection=True) == "hitl"
    # e nunca eleva: confiança baixa continua abstain
    assert risk.band_for_action(0.2, "explore:x", has_injection=True) == "abstain"


def test_unavailable_or_invalid_confidence_is_never_auto():
    for confidence in (None, 0, 0.0, "high", float("nan"), -1, 2.5, True):
        band = risk.band_for_action(confidence, "explore:x")
        assert band in ("hitl", "abstain"), confidence
        assert band != "auto"


def test_noul_kind_uses_certainty():
    assert risk.band_for_action(0.97, "explore:x", kind="noul") == "auto"   # |2p-1| = .94
    assert risk.band_for_action(0.5, "explore:x", kind="noul") == "abstain"
    assert risk.band_for_action(0.02, "explore:x", kind="noul") == "auto"   # simétrico
    assert risk.band_for_action(0.97, "attack:stray_mule", kind="noul") == band_for_noul(0.97, 0.95, 0.5)


def test_band_for_action_with_calibration_and_thresholds():
    table = Table(reversible=Entry(0.95, support=30))
    assert risk.band_for_action(0.93, "explore:x", calibration=table) == "hitl"
    assert risk.band_for_action(0.95, "explore:x", calibration=table) == "auto"
    # limiares do loop mais estritos do que o piso
    assert risk.band_for_action(0.91, "explore:x", auto=0.93) == "hitl"
    # hitl acima do auto é inválido ⇒ nunca levanta, cai em hitl
    assert risk.band_for_action(0.99, "explore:x", auto=0.4, hitl=0.9) in ("hitl", "auto")


def test_band_for_action_never_raises():
    class Boom:
        def entry_for(self, _):
            raise RuntimeError("boom")

    assert risk.band_for_action(0.99, object(), calibration=Boom()) in ("hitl", "auto")
    assert risk.band_for_action(object(), "explore:x") == "hitl"
    assert risk.band_for_action(0.99, "explore:x", auto=math.nan) in ("hitl", "auto")
    assert risk.thresholds_for(RISK_REVERSIBLE, Boom()) == pytest.approx(0.90)


def test_policy_floor_accepts_overrides():
    strict = RiskPolicy(floors={**risk.POLICY_FLOORS, "reversible": 0.92})
    assert risk.thresholds_for(RISK_REVERSIBLE, policy=strict, base=0.90) == pytest.approx(0.92)
    assert DEFAULT_POLICY.floor("destructive") == pytest.approx(0.97)
    broken = RiskPolicy(floors={"reversible": "x"})
    assert broken.floor("reversible") == pytest.approx(0.90)  # volta ao piso da política


# ------------------------------------- entradas NÃO-ACEITE e snapshot trocado


def test_not_accepted_entries_leave_the_policy_floors():
    # entradas NÃO-ACEITE (o que o pipeline produz com n<30): threshold None
    table = Table(reversible=Entry(None, support=17), costly=Entry(None, support=8),
                  irreversible=Entry(None, support=3), destructive=Entry(None, support=6))
    assert risk.class_thresholds(table) == {c: risk.POLICY_FLOORS[c] for c in RISK_CLASSES}
    assert {a: risk.auto_threshold_for_action(a, calibration=table) for a in
            ("explore:x", "flee:s", "attack:stray_mule", "attack:human_farmer")} == {
        "explore:x": 0.90, "flee:s": 0.95, "attack:stray_mule": 0.95, "attack:human_farmer": 0.97}
    # entrada não-aceite com limiar ACIMA do piso ainda pode SUBIR (direção
    # segura, §12 regra 1); nunca baixa — para isso exige verified + suporte ≥ 100
    assert risk.thresholds_for(RISK_COSTLY, Table(costly=Entry(0.98, support=5))) == pytest.approx(0.98)
    assert risk.thresholds_for(RISK_COSTLY, Table(costly=Entry(0.85, support=5))) == pytest.approx(0.95)


def test_snapshot_swap_falls_back_to_policy_bands():
    # tabela sem entrada para o snapshot em uso (missing/stale_resolved ⇒
    # defaults, "recalibrar"): as bandas voltam aos pisos da política — 0.92
    # chega para reversible mas nunca para costly/irreversible/destructive
    stale = Table()
    assert risk.band_for_action(0.92, "explore:x", calibration=stale) == "auto"
    assert risk.band_for_action(0.92, "flee:s", calibration=stale) == "hitl"
    assert risk.band_for_action(0.949, "attack:stray_mule", calibration=stale) == "hitl"
    assert risk.band_for_action(0.95, "attack:stray_mule", calibration=stale) == "auto"
    assert risk.band_for_action(0.96, "attack:human_farmer", calibration=stale) == "hitl"
