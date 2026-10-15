"""Retrato de prontidão de combate no loop (mudança 02): estado, candidatos, regras.

Aqui vive a Definição de Pronto do Passo 4 de ``PROXIMOS-PASSOS.md``:

* **(b) ataque REJEITADO por desvantagem de skill** — o candidato ``attack:*``
  não é gerado quando o retrato medido mostra skill abaixo do limiar contra um
  alvo ``moderate``+ (ou matchup ``deadly``), e a razão fica visível na fuga;
* **(c) fuga disparada abaixo de 60% HP** — ``flee`` sobe para sobrevivência com
  a razão ``blood at NN% (< 60%)`` a partir do retrato (``RETREAT_BLOOD_RATIO``);
* o bloco COMPACTO de prontidão no estado que o JEV-A vê (orçamento respeitado);
* estabilidade total sem retrato: sem medições nada muda (os prompts/evals não
  podem mudar às cegas) — guardado por ``tests/jev/test_golden_regression.py``.

Offline: fakes reais de ``fakes_loop`` + ``GameState.from_dict``.
"""
from __future__ import annotations

from system_one_dwarf.agent.candidates import (
    AttackGate,
    attack_gate,
    combat_readiness,
    generate_actions,
)
from system_one_dwarf.agent.loop import (
    READINESS_BLOCK_CHARS,
    build_state_summary,
    combat_readiness_block,
)
from system_one_dwarf.agent.types import LoopConfig
from system_one_dwarf.game.readiness import RETREAT_BLOOD_RATIO, TRAIN_MIN_LEVEL

from .fakes_loop import animal, game_state, hostile

CONFIG = LoopConfig()


# --------------------------------------------------------------------- helpers


def portrait(*, skill: int = 8, fighter: int | None = None, dodger: int | None = None,
             blood_ratio: float = 1.0, pain: float = 0.0, exhaustion: float = 0.0,
             strength: int = 1200, name: str = "Urist") -> dict:
    """``adventurer.combat`` como o ``dfb-state.lua`` o emite (retrato rico, arma branca)."""
    return {
        "name": name, "race": "Dwarf", "profession": "Adventurer",
        "combat": {
            "weapon": {"name": "iron spear", "skill_token": "SPEAR", "skill_noun": "Spearman",
                       "skill": skill, "ranged": False, "armed": True},
            "skills": {"fighter": fighter if fighter is not None else skill,
                       "dodger": dodger if dodger is not None else skill,
                       "shield_user": 2, "armor_user": 3, "wrestler": 1,
                       "striker": 2, "kicker": 1},
            "attributes": {"strength": strength, "agility": 1100, "toughness": 1100,
                           "endurance": 1100, "spatial_sense": 1000,
                           "kinesthetic_sense": 1000, "willpower": 1000},
            "condition": {"blood_ratio": blood_ratio, "pain": pain, "exhaustion": exhaustion,
                          "winded": 0, "stunned": 0, "nausea": 0, "dizziness": 0, "webbed": 0,
                          "wound_count": 1, "bleeding": 0},
            "armor": {"pieces": 2, "slots": 2, "shield": False},
        },
    }


def actions_of(state, objective: str = ""):
    return generate_actions(state, CONFIG, objective=objective)


def verb_set(actions) -> set[str]:
    return {action.verb for action in actions}


def action_of(actions, verb: str):
    return next((action for action in actions if action.verb == verb), None)


# ======================= (b) ataque rejeitado por desvantagem de skill


def test_attack_rejected_when_the_skill_is_below_the_threshold():
    """Skill 3 (Adequate < Proficient) contra mula (moderate): ataque NÃO é candidato."""
    state = game_state(adventurer=portrait(skill=3, fighter=3, dodger=3),
                       units=[animal("stray mule", 2, "N")], hour=12.0)
    actions = actions_of(state, "MISSION: find an animal and kill it.")
    assert "attack" not in verb_set(actions)          # o golpe desperdiçado não é opção
    gate = attack_gate(combat_readiness(state), {"name": "stray mule", "kind": "animal"})
    assert gate.engage is False
    assert "skill disadvantage" in gate.reason
    assert f"below Proficient ({TRAIN_MIN_LEVEL:g})" in gate.reason


def test_attack_rejected_by_skill_disadvantage_against_a_hostile():
    """DoD (b): hostil + retrato fraco ⇒ sem ``attack``; a fuga carrega a razão."""
    state = game_state(adventurer=portrait(skill=1, fighter=1, dodger=1),
                       hostiles=[hostile("wolf", 4, "N")],
                       hunger="none", thirst="none", sleep="rested")
    actions = actions_of(state)
    assert "attack" not in verb_set(actions)
    flee = action_of(actions, "flee")
    assert flee is not None and flee.category == "survival"
    assert "attack rejected" in flee.description_en
    assert "skill disadvantage" in flee.description_en
    assert any("skill disadvantage" in reason for reason in flee.params["reasons"])


def test_the_matchup_deadly_rule_also_rejects_the_attack():
    state = game_state(adventurer=portrait(skill=0, fighter=0, dodger=0, strength=700),
                       hostiles=[hostile("polar bear", 3, "S")],
                       hunger="none", thirst="none", sleep="rested")
    actions = actions_of(state)
    assert "attack" not in verb_set(actions)
    gate = attack_gate(combat_readiness(state), {"name": "polar bear", "race": "polar bear"})
    assert gate.engage is False and "deadly" in gate.reason


def test_attack_offered_when_the_skill_pays_and_the_lethal_aim_comes_with_it():
    """Contraprova: skill alta ⇒ ataque com mira letal (``c neck`` paralisa ``d head`` mata)."""
    state = game_state(adventurer=portrait(skill=8),
                       hostiles=[hostile("wolf", 4, "N")],
                       hunger="none", thirst="none", sleep="rested")
    actions = actions_of(state)
    attack = action_of(actions, "attack")
    assert attack is not None
    assert attack.params["aim"] == "neck"            # skill que compensa vs alvo perigoso
    assert attack.params["aim_menu"] == "c neck"
    assert "Aim at the neck (c neck)" in attack.description_en


def test_low_skill_never_gets_the_small_neck_target():
    """Mira letal só quando a skill compensa: skill baixa ⇒ cabeça (``d head``), nunca pescoço."""
    state = game_state(adventurer=portrait(skill=3, fighter=3, dodger=3, strength=1500),
                       units=[animal("stray mule", 2, "N")], hour=12.0)
    gate = attack_gate(combat_readiness(state), {"name": "stray mule", "kind": "animal"},
                       min_skill=0)                   # força o engage para inspecionar a mira
    assert gate.engage is True
    assert gate.aim_name == "head" and gate.aim_menu == "d head"


def test_without_a_portrait_nothing_is_vetted():
    state = game_state(hostiles=[hostile("wolf", 4, "N")],
                       hunger="none", thirst="none", sleep="rested")
    actions = actions_of(state)
    assert "attack" in verb_set(actions)              # sem medições, comportamento antigo
    assert attack_gate(None, {"name": "wolf", "race": "wolf"}).engage is True
    assert attack_gate({}, "wolf").engage is True


def test_gate_is_deterministic_and_never_raises():
    ready = combat_readiness(game_state(adventurer=portrait(skill=4)))
    first = attack_gate(ready, {"name": "wolf", "race": "wolf", "hostile": True})
    for _ in range(3):
        assert attack_gate(ready, {"name": "wolf", "race": "wolf", "hostile": True}) == first
    for junk in (None, 42, ["x"], {"name": "??? "}):
        assert isinstance(attack_gate(ready, junk), AttackGate)


# =============================== (c) fuga disparada abaixo de 60% HP


def test_flee_triggered_below_60_percent_hp():
    """DoD (c): HP 55% (< 60%) ⇒ ``flee`` de sobrevivência com a razão do sangue."""
    state = game_state(adventurer=portrait(skill=8, blood_ratio=0.55),
                       hostiles=[hostile("wolf", 9, "N")],   # longe: não é "dangerous"
                       hunger="none", thirst="none", sleep="rested", status="healthy")
    actions = actions_of(state)
    flee = action_of(actions, "flee")
    assert flee is not None
    assert flee.category == "survival"
    assert "blood at 55% (< 60%)" in flee.description_en
    assert "blood at 55% (< 60%)" in flee.params["reasons"]
    assert RETREAT_BLOOD_RATIO == 0.60
    # a fuga de sobrevivência aparece antes do ataque na triagem
    ids = [action.id for action in actions]
    attack = action_of(actions, "attack")
    if attack is not None:
        assert ids.index(flee.id) < ids.index(attack.id)


def test_hp_exactly_at_60_percent_does_not_trigger_the_flee_rule():
    state = game_state(adventurer=portrait(skill=8, blood_ratio=0.60),
                       hostiles=[hostile("wolf", 9, "N")],
                       hunger="none", thirst="none", sleep="rested", status="healthy")
    actions = actions_of(state)
    flee = action_of(actions, "flee")
    # limiar estrito: sem razão de recuo, sem fuga de sobrevivência (só o recuo
    # genérico de conveniência, que já existia e é categoria "threat")
    assert flee is None or (flee.category == "threat" and not flee.params.get("reasons", []))


def test_flee_also_fires_on_heavy_pain_not_only_on_blood():
    state = game_state(adventurer=portrait(skill=8, pain=90.0),
                       hostiles=[hostile("wolf", 12, "S")],
                       hunger="none", thirst="none", sleep="rested", status="healthy")
    actions = actions_of(state)
    flee = action_of(actions, "flee")
    assert flee is not None and flee.category == "survival"
    assert "heavy pain" in flee.description_en


# ======================================== bloco compacto no estado do JEV-A


def test_state_summary_carries_the_compact_readiness_block():
    state = game_state(adventurer=portrait(skill=7), hostiles=[hostile("wolf", 9, "N")])
    summary = build_state_summary(state, objective="survive")
    assert "Combat readiness (measured data):" in summary
    assert "Spearman 7" in summary                    # skill efetiva da arma
    assert "Fighter 7" in summary                     # skills de combate
    assert "Readiness: power" in summary              # resumo em números
    # o bloco entra depois do personagem e antes das necessidades
    assert summary.index("Character:") < summary.index("Combat readiness") < summary.index("Needs:")


def test_state_summary_is_byte_stable_without_a_portrait():
    state = game_state(hostiles=[hostile("wolf", 9, "N")])
    summary = build_state_summary(state, objective="survive")
    assert "Combat readiness" not in summary
    assert combat_readiness_block(state) == []


def test_readiness_block_respects_its_budget():
    state = game_state(adventurer=portrait(skill=7, blood_ratio=0.55))
    block = combat_readiness_block(state)
    assert 1 < len(block) <= 10                       # cabeçalho + linhas (MAX_PROMPT_LINES)
    assert sum(len(line) for line in block) <= READINESS_BLOCK_CHARS
    tight = combat_readiness_block(state, max_chars=250, max_lines=3)
    assert sum(len(line) for line in tight) <= 250
    assert len(tight) <= 3
    for line in block + tight:
        assert line == " ".join(line.split())          # linhas limpas, uma por linha


def test_readiness_block_never_raises_on_hostile_input():
    class Weird:
        readiness = "not a portrait"
        adventurer = ["junk"]

    assert combat_readiness_block(Weird()) == []
    assert combat_readiness_block(None) == []


# ================================================ candidatos: forma e estabilidade


def test_attack_candidates_carry_aim_params_for_the_future_executor():
    state = game_state(adventurer=portrait(skill=8),
                       units=[animal("stray mule", 2, "N")], hour=12.0)
    attack = action_of(actions_of(state), "attack")
    assert attack is not None
    assert attack.params["aim"] in ("head", "neck", "upper body")
    assert attack.params["aim_menu"].startswith(("a ", "b ", "c ", "d "))
    assert attack.params["target_name"] == "stray mule"


def test_generation_with_a_portrait_stays_deterministic():
    state = game_state(adventurer=portrait(skill=3, blood_ratio=0.55),
                       hostiles=[hostile("wolf", 4, "N")], units=[animal("stray mule", 2, "N")])
    first, second = actions_of(state), actions_of(state)
    assert [a.id for a in first] == [a.id for a in second]
    assert [dict(a.params) for a in first] == [dict(a.params) for a in second]
    assert [a.description_en for a in first] == [a.description_en for a in second]
