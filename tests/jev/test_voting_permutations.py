"""Shuffle + voto do JEV-A (Mudança 05): permutações distintas, apuramento e
compatibilidade com o loop.

Tudo offline e determinístico: sementes fixas, resultados construídos com os
tipos **reais** do cliente (``Answer``/``DecisionResult``/``Usage``) e, onde
interessa, o ``AgentLoop`` verdadeiro (``_record_decision``/``_read_action``/
``_routing_reason``) como consumidor — a prova de que o loop não precisa de
mudanças para ler um resultado agregado.
"""
from __future__ import annotations

import json
import math
from typing import Any

import pytest

from system_one_dwarf.jev import (
    Answer,
    DecisionResult,
    Usage,
    band_for_choice_or_score,
    band_for_noul,
    build_action,
    validate_request,
)
from system_one_dwarf.jev.voting import (
    CRITICAL_VERBS,
    DEFAULT_VOTES,
    MAX_VOTES,
    VoteOutcome,
    build_vote_requests,
    cast_votes,
    tally_votes,
    vote_count_for,
)

SUMMARY = "SITUATION: a goblin skulker is 4 tiles NE; the adventurer is wounded."

CANDIDATES = [
    {"id": "attack:goblin_skulker", "name": "Attack the goblin skulker", "description": "Close in and strike."},
    {"id": "flee:s", "name": "Flee south", "description": "Break away from the threat."},
    {"id": "explore:n", "name": "Explore north", "description": "Walk the road north."},
    {"id": "wait:one_turn", "name": "Wait one turn", "description": "Pass a turn and watch."},
]
IDS = [c["id"] for c in CANDIDATES]


def orders(requests: list[dict[str, Any]]) -> list[tuple[str, ...]]:
    return [tuple(request["questions"]["action"]["criteria"]) for request in requests]


def result_for(
    request: dict[str, Any],
    pick: str | None,
    *,
    confidence: Any = 0.95,
    probabilities: Any = "auto",
    needs_human: Any = 0.05,
    urgency: Any = 1.0,
    positional: bool = False,
    cost: float = 0.0001,
    latency: int = 100,
    model: str = "typesafe/jev-1.13",
    issues: tuple[str, ...] = (),
    with_action: bool = True,
) -> DecisionResult:
    """Uma resposta do JEV-A a ``request`` que escolheu ``pick``."""
    keys = list(request["ordering"]["action"])

    def label(key: str) -> str:
        return f"opt_{keys.index(key) + 1}" if positional and key in keys else key

    if probabilities == "auto":
        top = confidence if isinstance(confidence, float) and 0.0 < confidence <= 1.0 else 0.8
        rest = [k for k in keys if k != pick]
        share = (1.0 - top) / max(1, len(rest))
        probabilities = {label(k): share for k in rest}
        if pick is not None:
            probabilities[label(pick)] = top
    elif isinstance(probabilities, dict) and positional:
        probabilities = {label(k): v for k, v in probabilities.items()}

    value = None if pick is None else label(pick)
    try:
        needs_band = band_for_noul(needs_human)
    except Exception:  # lixo (NaN, 1.7…): o cliente real também fica em hitl
        needs_band = "hitl"
    answers: dict[str, Answer] = {
        "urgency": Answer("urgency", "score", urgency, 0.8, None, None, "hitl", None),
        "needs_human": Answer("needs_human", "noul", needs_human, None, None, None, needs_band, None),
    }
    if with_action:
        try:
            band = band_for_choice_or_score(confidence)
        except Exception:  # lixo (NaN…): o cliente real também cai em hitl
            band = "hitl"
        answers["action"] = Answer(
            "action", "choice", value, confidence, probabilities, None, band,
            (probabilities or {}).get(value) if value is not None else None, issues=issues,
        )
    return DecisionResult(
        answers=answers, usage=Usage(input_tokens=800, output_tokens=40, cost_usd=cost),
        latency_ms=latency, model=model, raw={}, provider="typesafe", request_id="gen-1",
    )


def vote(picks: list[str | None], *, seed: int = 11, k: int | None = None, **kwargs: Any):
    """Pedidos reais + um resultado por pedido (``None`` = voto falhado)."""
    requests = build_vote_requests(CANDIDATES[:k] if k else CANDIDATES, SUMMARY, n=len(picks), base_seed=seed)
    assert len(requests) == len(picks)
    results = [None if pick is None else result_for(req, pick, **kwargs)
               for req, pick in zip(requests, picks)]
    return requests, results


# --------------------------------------------------------------------------- #
# build_vote_requests
# --------------------------------------------------------------------------- #
def test_requests_have_distinct_orders_with_the_exit_pinned_last():
    requests = build_vote_requests(CANDIDATES, SUMMARY, n=3, base_seed=7)
    assert len(requests) == 3 == DEFAULT_VOTES
    seen = orders(requests)
    assert len(set(seen)) == 3
    for order in seen:
        assert order[-1] == "other"
        assert sorted(order[:-1]) == sorted(IDS)
    for request in requests:
        assert request["ordering"]["action"] == list(request["questions"]["action"]["criteria"])
        assert request["meta"]["role"] == "JEV-A"
        assert request["meta"]["vote"]["seed"] == 7
        report = validate_request(request)
        assert report.errors == []


@pytest.mark.parametrize("seed", range(25))
def test_each_vote_puts_a_different_candidate_first(seed):
    requests = build_vote_requests(CANDIDATES, SUMMARY, n=3, base_seed=seed)
    firsts = [order[0] for order in orders(requests)]
    assert len(set(firsts)) == 3  # o viés da 1.ª opção parte-se em votos diferentes


def test_requests_are_reproducible_by_seed_and_vary_across_seeds():
    first = build_vote_requests(CANDIDATES, SUMMARY, n=3, base_seed=42)
    again = build_vote_requests(CANDIDATES, SUMMARY, n=3, base_seed=42)
    assert [r["state"] for r in first] == [r["state"] for r in again]
    assert orders(first) == orders(again)
    variety = {tuple(orders(build_vote_requests(CANDIDATES, SUMMARY, n=3, base_seed=s)))
               for s in range(8)}
    assert len(variety) > 1


def test_a_fresh_seed_is_recorded_and_replays_the_same_permutations():
    requests = build_vote_requests(CANDIDATES, SUMMARY, n=3)
    seed = requests[0]["meta"]["vote"]["seed"]
    assert isinstance(seed, int)
    assert {r["meta"]["vote"]["seed"] for r in requests} == {seed}
    replay = build_vote_requests(CANDIDATES, SUMMARY, n=3, base_seed=seed)
    assert orders(replay) == orders(requests)


def test_state_and_labels_follow_the_permutation():
    for request in build_vote_requests(CANDIDATES, SUMMARY, n=3, base_seed=3):
        order = request["meta"]["vote"]["order"]
        labels = request["meta"]["action_labels"]
        assert [labels[f"A{i}"] for i in range(1, len(order) + 1)] == order
        assert request["state"].index(order[0]) < request["state"].index(order[-1])
        assert request["meta"]["exit_key"] == "other"


def test_vote_count_is_clamped_and_never_repeats_an_order():
    assert len(build_vote_requests(CANDIDATES, SUMMARY, n=0, base_seed=1)) == 1
    assert len(build_vote_requests(CANDIDATES, SUMMARY, n=99, base_seed=1)) == MAX_VOTES
    assert len(build_vote_requests(CANDIDATES, SUMMARY, n="lixo", base_seed=1)) == DEFAULT_VOTES
    assert len(build_vote_requests(CANDIDATES[:1], SUMMARY, n=3, base_seed=1)) == 1  # nada a permutar
    two = build_vote_requests(CANDIDATES[:2], SUMMARY, n=5, base_seed=1)  # só 2! = 2 ordens
    assert len(two) == 2 and len(set(orders(two))) == 2


@pytest.mark.parametrize("size", [2, 3, 4, 7])
@pytest.mark.parametrize("seed", range(10))
def test_distinct_orders_for_every_size_and_seed(size, seed):
    candidates = [{"id": f"explore:p{i}", "name": f"Point {i}", "description": "go"} for i in range(size)]
    requests = build_vote_requests(candidates, SUMMARY, n=MAX_VOTES, base_seed=seed)
    expected = min(MAX_VOTES, math.factorial(size))
    assert len(requests) == expected
    assert len(set(orders(requests))) == expected


def test_a_large_candidate_list_stays_distinct_and_fast():
    candidates = [{"id": f"explore:p{i}", "name": f"Point {i}", "description": "go"} for i in range(60)]
    requests = build_vote_requests(candidates, SUMMARY, n=5, base_seed=9)
    assert len(requests) == 5 and len(set(orders(requests))) == 5
    assert len({order[0] for order in orders(requests)}) == 5


def test_shuffle_false_means_one_canonical_request():
    requests = build_vote_requests(CANDIDATES, SUMMARY, n=3, base_seed=5, shuffle=False)
    assert len(requests) == 1
    assert orders(requests)[0] == (*IDS, "other")
    assert requests[0]["ordering"]["action"] == [*IDS, "other"]


def test_invalid_input_never_raises_and_falls_back_to_an_empty_list():
    bad_inputs: list[Any] = [
        [], None, "attack:x", 42, [{"id": ""}], [{"id": "a:b"}, {"id": "a:b"}],
        ["not-a-candidate"], [{"id": "other", "name": "exit", "description": "x"}],
    ]
    for candidates in bad_inputs:
        assert build_vote_requests(candidates, SUMMARY, n=3, base_seed=1) == []
    assert build_vote_requests(CANDIDATES, "", n=3, base_seed=1) == []  # estado vazio: o builder recusa


def test_macro_actions_are_accepted_and_the_exit_candidate_is_dropped():
    from system_one_dwarf.agent.types import MacroAction

    actions = [
        MacroAction(id="attack:wolf", name_en="Attack the wolf", description_en="Strike it.",
                    verb="attack", category="threat"),
        MacroAction(id="flee:s", name_en="Flee south", description_en="Run.", verb="flee",
                    category="survival"),
        MacroAction(id="other", name_en="None of these", description_en="Nothing fits.",
                    verb="other"),
    ]
    requests = build_vote_requests(actions, SUMMARY, n=3, base_seed=2)
    assert len(requests) == 2  # 2 candidatos reais ⇒ 2! ordens
    for order in orders(requests):
        assert sorted(order) == ["attack:wolf", "flee:s", "other"]
        assert order[-1] == "other"


def test_custom_builder_receives_permuted_candidates_and_forced_shuffle_off():
    seen: list[dict[str, Any]] = []

    def builder(candidates, summary, **kwargs):
        seen.append({"ids": [c["id"] for c in candidates], "kwargs": dict(kwargs)})
        return build_action(candidates, summary, **kwargs)

    requests = build_vote_requests(
        CANDIDATES, SUMMARY, n=3, base_seed=1, builder=builder,
        urgency_levels=("Calm — nothing is moving", "Danger — something is closing in"),
        shuffle=True, shuffle_seed=999,
    )
    assert len(requests) == len(seen) == 3
    assert all(call["kwargs"]["shuffle"] is False for call in seen)
    assert all("shuffle_seed" not in call["kwargs"] for call in seen)
    assert all(len(call["kwargs"]["urgency_levels"]) == 2 for call in seen)
    assert len({tuple(call["ids"]) for call in seen}) == 3
    assert len(requests[0]["questions"]["urgency"]["criteria"]) == 2


def test_a_builder_that_raises_yields_no_requests():
    def broken(candidates, summary, **kwargs):
        raise RuntimeError("builder exploded")

    assert build_vote_requests(CANDIDATES, SUMMARY, n=3, base_seed=1, builder=broken) == []


# --------------------------------------------------------------------------- #
# cast_votes
# --------------------------------------------------------------------------- #
def test_cast_votes_keeps_failures_aligned_and_never_raises():
    requests = build_vote_requests(CANDIDATES, SUMMARY, n=3, base_seed=1)
    calls: list[int] = []

    def decide(request):
        calls.append(len(calls))
        if len(calls) == 2:
            raise TimeoutError("jev timed out")
        return result_for(request, "flee:s")

    results = cast_votes(decide, requests)
    assert len(results) == 3
    assert isinstance(results[1], TimeoutError)
    assert results[0].answers["action"].value == "flee:s"
    outcome = tally_votes(results, requests)
    assert outcome.votes_valid == 2
    assert outcome.ballots[1].reason == "error:TimeoutError"


def test_cast_votes_uses_the_injected_map_and_survives_a_broken_one():
    requests = build_vote_requests(CANDIDATES, SUMMARY, n=3, base_seed=1)
    used: list[int] = []

    def reversed_map(call, items):
        used.append(len(items))
        return [call(item) for item in items]

    cast_votes(lambda request: result_for(request, "flee:s"), requests, map_fn=reversed_map)
    assert used == [3]

    def broken_map(call, items):
        raise RuntimeError("pool is gone")

    results = cast_votes(lambda request: None, requests, map_fn=broken_map)
    assert len(results) == 3 and all(isinstance(r, RuntimeError) for r in results)
    assert cast_votes(lambda request: None, None) == []  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# tally_votes: unânime / dividido / empate / votos falhados
# --------------------------------------------------------------------------- #
def test_unanimous_vote_keeps_the_mean_confidence():
    requests, results = vote(["flee:s"] * 3, confidence=0.95)
    outcome = tally_votes(results, requests)
    assert isinstance(outcome, VoteOutcome)
    assert outcome.winner == "flee:s"
    assert outcome.consensus and not outcome.split and not outcome.tie
    assert (outcome.votes_cast, outcome.votes_valid, outcome.votes_failed) == (3, 3, 0)
    assert outcome.agreement == 1.0
    assert outcome.position_stability == 1.0
    assert outcome.confidence == pytest.approx(0.95)
    assert outcome.issues[0] == "vote:3/3"
    assert outcome.first_position_rate <= 1 / 3 + 1e-9  # 1 cabeça distinta por voto


def test_a_two_of_three_split_can_never_reach_the_auto_band():
    requests, results = vote(["flee:s", "flee:s", "attack:goblin_skulker"], confidence=0.97)
    outcome = tally_votes(results, requests)
    assert outcome.winner == "flee:s" and outcome.consensus and outcome.split
    assert outcome.agreement == pytest.approx(2 / 3)
    assert outcome.confidence == pytest.approx((0.97 + 0.97) / 2 * 2 / 3, abs=1e-6)
    assert outcome.confidence < 0.90
    assert band_for_choice_or_score(outcome.confidence) == "hitl"
    assert outcome.issues[0] == "vote_split:2/3"


def test_a_two_of_three_split_with_perfect_confidence_still_misses_auto():
    requests, results = vote(["flee:s", "flee:s", "explore:n"], confidence=1.0)
    outcome = tally_votes(results, requests)
    assert outcome.confidence == pytest.approx(2 / 3, abs=1e-6)
    assert band_for_choice_or_score(outcome.confidence) != "auto"


def test_a_three_way_split_has_no_safe_winner_and_reads_as_abstain():
    requests, results = vote(["flee:s", "explore:n", "attack:goblin_skulker"], confidence=0.9)
    outcome = tally_votes(results, requests)
    assert outcome.winner == "other" and not outcome.consensus
    assert outcome.tie and outcome.split
    assert outcome.agreement == 0.0
    assert outcome.confidence == pytest.approx(0.9 / 3, abs=1e-6)
    assert band_for_choice_or_score(outcome.confidence) == "abstain"
    assert {"vote_tie", "vote_no_consensus"} <= set(outcome.issues)
    assert outcome.issues[0] == "vote_split:1/3"


def test_one_one_with_a_failed_ballot_is_a_tie_and_goes_to_the_exit():
    requests, results = vote(["flee:s", "explore:n", None], confidence=0.9)
    outcome = tally_votes(results, requests)
    assert outcome.winner == "other" and outcome.tie and not outcome.consensus
    assert (outcome.votes_cast, outcome.votes_valid) == (3, 2)
    assert "vote_failed:1/3" in outcome.issues


def test_no_strict_majority_means_no_winner_even_without_a_tie_in_votes():
    # 5 votos, 1 falhado: 2-1-1 entre 4 válidos — líder sem maioria estrita
    requests, results = vote(["flee:s", "flee:s", "explore:n", "attack:goblin_skulker", None], confidence=0.9)
    outcome = tally_votes(results, requests)
    assert (outcome.votes_cast, outcome.votes_valid) == (5, 4)
    assert not outcome.consensus and outcome.winner == "other"
    assert outcome.votes["flee:s"] == 2


def test_failed_ballots_are_ignored_but_lower_the_confidence_ceiling():
    requests, results = vote(["flee:s", "flee:s", None], confidence=0.97)
    outcome = tally_votes(results, requests)
    assert outcome.winner == "flee:s" and outcome.consensus
    assert outcome.agreement == 1.0           # 2/2 dos válidos
    assert not outcome.split
    assert outcome.confidence == pytest.approx(0.97 * 2 / 3, abs=1e-6)  # teto: 2 de 3 lançados
    assert band_for_choice_or_score(outcome.confidence) == "hitl"
    assert outcome.issues[:2] == ("vote:2/2", "vote_failed:1/3")


@pytest.mark.parametrize("bad", ["none", "exception", "no_answers", "no_action", "unknown_option"])
def test_every_kind_of_unusable_ballot_is_counted_as_failed(bad):
    requests, results = vote(["flee:s"] * 3, confidence=0.95)
    replacement: Any = {
        "none": None,
        "exception": RuntimeError("boom"),
        "no_answers": object(),
        "no_action": result_for(requests[1], "flee:s", with_action=False),
        "unknown_option": result_for(requests[1], "attack:dragon_that_was_never_offered"),
    }[bad]
    results[1] = replacement
    outcome = tally_votes(results, requests)
    assert outcome.votes_valid == 2 and not outcome.ballots[1].valid
    assert outcome.winner == "flee:s"
    assert outcome.confidence == pytest.approx(0.95 * 2 / 3, abs=1e-6)


def test_all_ballots_failed_gives_the_exit_key_and_no_confidence():
    requests, _ = vote(["flee:s"] * 3)
    outcome = tally_votes([None, RuntimeError("x"), None], requests)
    assert outcome.winner == "other" and not outcome.consensus
    assert outcome.votes_valid == 0 and outcome.confidence is None
    assert outcome.agreement == 0.0 and not outcome.split
    assert outcome.issues == ("vote_failed:3/3",)
    assert outcome.first_position_rate is None


def test_unanimous_exit_is_a_consensus_on_none_of_these():
    requests, results = vote(["other"] * 3, confidence=0.9)
    outcome = tally_votes(results, requests)
    assert outcome.winner == "other" and outcome.consensus
    assert outcome.agreement == 1.0
    assert outcome.confidence == pytest.approx(0.9)


# --------------------------------------------------------------------------- #
# probabilidades somadas, plurality de recurso, posições
# --------------------------------------------------------------------------- #
def test_without_probabilities_the_tally_falls_back_to_plurality():
    requests, results = vote(["flee:s", "flee:s", "explore:n"], confidence=0.9, probabilities=None)
    outcome = tally_votes(results, requests)
    assert outcome.winner == "flee:s" and outcome.consensus
    assert outcome.tally == {"explore:n": 1.0, "flee:s": 2.0}
    assert outcome.votes == {"explore:n": 1, "flee:s": 2}


def test_partial_probabilities_mix_with_one_hot_ballots():
    requests = build_vote_requests(CANDIDATES, SUMMARY, n=3, base_seed=4)
    results = [
        result_for(requests[0], "flee:s", probabilities={"flee:s": 0.6, "explore:n": 0.4}),
        result_for(requests[1], "explore:n", probabilities=None, confidence=0.9),
        result_for(requests[2], "explore:n", probabilities=None, confidence=0.9),
    ]
    outcome = tally_votes(results, requests)
    assert outcome.winner == "explore:n"
    assert outcome.tally["explore:n"] == pytest.approx(0.4 + 2.0)
    assert outcome.tally["flee:s"] == pytest.approx(0.6)


def test_a_probability_tie_has_no_winner():
    requests = build_vote_requests(CANDIDATES, SUMMARY, n=2, base_seed=4)
    results = [
        result_for(requests[0], "flee:s", probabilities={"flee:s": 0.5, "explore:n": 0.5}),
        result_for(requests[1], "explore:n", probabilities={"flee:s": 0.5, "explore:n": 0.5}),
    ]
    outcome = tally_votes(results, requests)
    assert outcome.tie and outcome.winner == "other" and not outcome.consensus


def test_a_flat_distribution_is_a_tie_even_when_every_ballot_picked_the_same_option():
    requests = build_vote_requests(CANDIDATES, SUMMARY, n=3, base_seed=4)
    flat = {"flee:s": 0.5, "explore:n": 0.5}
    # ``explore:n`` ordena antes de ``flee:s``: o desempate por chave não pode "salvar" o voto
    results = [result_for(req, "explore:n", probabilities=flat, confidence=0.9) for req in requests]
    outcome = tally_votes(results, requests)
    assert outcome.votes == {"explore:n": 3}       # voto duro unânime...
    assert outcome.tie and outcome.winner == "other" and not outcome.consensus  # ...mas massa empatada
    assert tally_votes(results[:1], requests[:1]).tie  # também com um só voto válido


def test_soft_and_hard_votes_disagreeing_means_no_safe_winner():
    requests = build_vote_requests(CANDIDATES, SUMMARY, n=3, base_seed=4)
    results = [
        result_for(requests[0], "flee:s", probabilities={"flee:s": 0.51, "explore:n": 0.49}),
        result_for(requests[1], "flee:s", probabilities={"flee:s": 0.51, "explore:n": 0.49}),
        result_for(requests[2], "explore:n", probabilities={"explore:n": 0.99, "flee:s": 0.01}),
    ]
    outcome = tally_votes(results, requests)
    # 2 votos para flee (maioria dura) mas a massa é de explore: sem consenso
    assert outcome.votes["flee:s"] == 2
    assert outcome.tally["explore:n"] > outcome.tally["flee:s"]
    assert outcome.winner == "other" and not outcome.consensus and not outcome.tie


def test_probabilities_are_normalised_so_every_ballot_weighs_the_same():
    requests = build_vote_requests(CANDIDATES, SUMMARY, n=2, base_seed=6)
    results = [
        result_for(requests[0], "flee:s", probabilities={"flee:s": 0.3, "explore:n": 0.1}),  # soma 0.4
        result_for(requests[1], "flee:s", probabilities={"flee:s": 0.9, "explore:n": 0.1}),
    ]
    outcome = tally_votes(results, requests)
    assert outcome.tally["flee:s"] == pytest.approx(0.75 + 0.9)
    assert sum(outcome.tally.values()) == pytest.approx(2.0)


def test_garbage_probabilities_fall_back_to_one_hot():
    requests = build_vote_requests(CANDIDATES, SUMMARY, n=3, base_seed=6)
    garbage = [
        {"flee:s": float("nan")},
        {"flee:s": -1.0, "explore:n": "x"},
        {"not-an-option": 1.0},
    ]
    results = [result_for(req, "flee:s", probabilities=bad, confidence=0.9)
               for req, bad in zip(requests, garbage)]
    outcome = tally_votes(results, requests)
    assert outcome.tally == {"flee:s": 3.0}
    assert outcome.consensus


def test_positional_answers_are_unshuffled_with_each_requests_own_order():
    requests = build_vote_requests(CANDIDATES, SUMMARY, n=3, base_seed=13)
    assert len({tuple(r["ordering"]["action"]) for r in requests}) == 3
    results = [result_for(req, "flee:s", positional=True, confidence=0.93) for req in requests]
    for req, res in zip(requests, results):
        assert res.answers["action"].value.startswith("opt_")  # o ``value`` não é uma chave
    outcome = tally_votes(results, requests)
    assert outcome.winner == "flee:s" and outcome.consensus
    assert set(outcome.tally) == set(IDS) | {"other"}  # chaves da massa também canónicas
    assert outcome.confidence == pytest.approx(0.93)


def test_integer_and_out_of_range_positions():
    requests = build_vote_requests(CANDIDATES, SUMMARY, n=3, base_seed=13)
    order = requests[0]["ordering"]["action"]
    results = [result_for(requests[0], "flee:s"), result_for(requests[1], "flee:s"),
               result_for(requests[2], "flee:s")]
    results[0].answers["action"] = Answer(
        "action", "choice", 2, 0.95, None, None, "hitl", None)
    results[1].answers["action"] = Answer(
        "action", "choice", "opt_99", 0.95, None, None, "hitl", None)
    outcome = tally_votes(results, requests)
    assert outcome.ballots[0].action == order[1]            # posição 2 (1-based)
    assert not outcome.ballots[1].valid and outcome.ballots[1].reason == "unknown_option"


def test_a_position_biased_model_is_exposed_by_the_vote():
    """O modelo que escolhe SEMPRE a 1.ª opção não passa a ``auto``."""
    requests = build_vote_requests(CANDIDATES, SUMMARY, n=3, base_seed=21)

    def always_first(request):
        first = next(iter(request["questions"]["action"]["criteria"]))
        return result_for(request, first, confidence=0.99)

    outcome = tally_votes(cast_votes(always_first, requests), requests)
    assert outcome.first_position_rate == 1.0
    assert outcome.position_stability == pytest.approx(1 / 3)
    assert outcome.winner == "other" and not outcome.consensus
    assert band_for_choice_or_score(outcome.confidence) == "abstain"


def test_a_stable_model_is_unaffected_by_the_order():
    requests = build_vote_requests(CANDIDATES, SUMMARY, n=3, base_seed=21)
    outcome = tally_votes(cast_votes(lambda r: result_for(r, "flee:s", confidence=0.96), requests), requests)
    assert outcome.winner == "flee:s" and outcome.consensus
    assert band_for_choice_or_score(outcome.confidence) == "auto"
    assert outcome.position_stability == 1.0


# --------------------------------------------------------------------------- #
# confiança: aritmética e indisponibilidade
# --------------------------------------------------------------------------- #
def test_confidence_is_the_mean_of_the_backers_times_their_share_of_the_cast():
    requests, results = vote(["flee:s", "flee:s", "flee:s"], confidence=0.9)
    for res, conf in zip(results, (0.99, 0.93, 0.90)):
        res.answers["action"] = Answer("action", "choice", "flee:s", conf, None, None, "auto", None)
    outcome = tally_votes(results, requests)
    assert outcome.confidence == pytest.approx((0.99 + 0.93 + 0.90) / 3, abs=1e-6)


def test_the_disagreeing_ballots_confidence_does_not_leak_into_the_aggregate():
    requests, results = vote(["flee:s", "flee:s", "explore:n"], confidence=0.9)
    results[2].answers["action"] = Answer(
        "action", "choice", results[2].answers["action"].value, 0.99, None, None, "auto", None)
    outcome = tally_votes(results, requests)
    assert outcome.confidence == pytest.approx(0.9 * 2 / 3, abs=1e-6)


@pytest.mark.parametrize("unusable", [None, 0.0, float("nan"), True, "high", 1.5, -0.2])
def test_unavailable_confidence_makes_the_aggregate_unavailable(unusable):
    requests, results = vote(["flee:s"] * 3, confidence=0.95)
    results[1].answers["action"] = Answer(
        "action", "choice", results[1].answers["action"].value, unusable, None, None, "hitl", None)
    outcome = tally_votes(results, requests)
    assert outcome.winner == "flee:s" and outcome.consensus
    assert outcome.confidence is None
    assert "vote_confidence_unavailable" in outcome.issues
    decision = outcome.as_decision_result()
    assert decision.answers["action"].confidence is None
    assert decision.answers["action"].band == "hitl"  # nunca ``auto``


def test_all_unavailable_confidences_stay_unavailable_not_zero():
    requests, results = vote(["flee:s"] * 3, confidence=None)
    outcome = tally_votes(results, requests)
    assert outcome.confidence is None and outcome.winner == "flee:s"


# --------------------------------------------------------------------------- #
# needs_human / urgência / custo / metadados
# --------------------------------------------------------------------------- #
def test_needs_human_is_the_max_and_urgency_the_most_urgent():
    requests = build_vote_requests(CANDIDATES, SUMMARY, n=3, base_seed=8)
    results = [
        result_for(requests[0], "flee:s", needs_human=0.05, urgency=0.4),
        result_for(requests[1], "flee:s", needs_human=0.62, urgency=1.8),
        result_for(requests[2], "flee:s", needs_human=0.10, urgency=1.0),
    ]
    outcome = tally_votes(results, requests)
    assert outcome.needs_human == pytest.approx(0.62)
    assert outcome.urgency == pytest.approx(1.8)
    decision = outcome.as_decision_result()
    assert decision.answers["needs_human"].value == pytest.approx(0.62)
    assert decision.answers["urgency"].value == pytest.approx(1.8)


def test_a_worried_ballot_counts_even_when_its_action_is_unusable():
    requests = build_vote_requests(CANDIDATES, SUMMARY, n=3, base_seed=8)
    results = [
        result_for(requests[0], "flee:s", needs_human=0.05),
        result_for(requests[1], "flee:s", needs_human=0.05),
        result_for(requests[2], "unknown:thing", needs_human=0.9),
    ]
    outcome = tally_votes(results, requests)
    assert outcome.votes_valid == 2 and outcome.needs_human == pytest.approx(0.9)


def test_invalid_needs_human_and_urgency_are_ignored():
    requests = build_vote_requests(CANDIDATES, SUMMARY, n=2, base_seed=8)
    results = [result_for(requests[0], "flee:s", needs_human=float("nan"), urgency=None),
               result_for(requests[1], "flee:s", needs_human=1.7, urgency=float("inf"))]
    outcome = tally_votes(results, requests)
    assert outcome.needs_human is None and outcome.urgency is None
    decision = outcome.as_decision_result()
    assert decision.answers["needs_human"].value is None  # o loop lê: indisponível ⇒ pergunta


def test_usage_is_summed_latency_is_the_max_and_metadata_comes_from_the_ballots():
    requests = build_vote_requests(CANDIDATES, SUMMARY, n=3, base_seed=8)
    results = [
        result_for(requests[0], "flee:s", cost=0.0001, latency=90, model="typesafe/jev-1.13-a"),
        result_for(requests[1], "flee:s", cost=0.0002, latency=210),
        None,
    ]
    outcome = tally_votes(results, requests)
    assert outcome.usage.input_tokens == 1600 and outcome.usage.output_tokens == 80
    assert outcome.usage.cost_usd == pytest.approx(0.0003)
    assert outcome.latency_ms == 210
    assert outcome.model == "typesafe/jev-1.13-a" and outcome.provider == "typesafe"
    assert outcome.request_id == "gen-1" and outcome.attempts == 2


def test_inherited_issues_are_deduplicated_and_capped():
    requests, results = vote(["flee:s"] * 3, confidence=0.95,
                             issues=("confidence unavailable (absent or 0.0) → band capped at hitl",))
    outcome = tally_votes(results, requests)
    assert outcome.issues.count("confidence unavailable (absent or 0.0) → band capped at hitl") == 1
    many = tuple(f"issue-{i}" for i in range(30))
    requests, results = vote(["flee:s"] * 3, issues=many)
    assert len(tally_votes(results, requests).issues) <= 8


# --------------------------------------------------------------------------- #
# robustez do apuramento
# --------------------------------------------------------------------------- #
def test_tally_never_raises_on_garbage():
    assert tally_votes(None, None).winner == "other"  # type: ignore[arg-type]
    assert tally_votes([], []).issues == ("vote_failed:0/0",)
    garbage = tally_votes([object(), 5, "x"], [None, 7, {"questions": 3}])  # type: ignore[list-item]
    assert garbage.votes_valid == 0 and garbage.winner == "other"
    weird = tally_votes([result_for({"ordering": {"action": ["a", "other"]}}, "a")],
                        [{"ordering": "oops"}])
    assert weird.votes_cast == 1


def test_missing_results_count_as_failed_and_extra_ones_are_ignored():
    requests, results = vote(["flee:s"] * 3, confidence=0.95)
    short = tally_votes(results[:2], requests)
    assert (short.votes_cast, short.votes_valid) == (3, 2)
    extra = tally_votes([*results, results[0]], requests)
    assert (extra.votes_cast, extra.votes_valid) == (3, 3)


def test_the_exit_key_comes_from_the_requests_meta_unless_given():
    requests = build_vote_requests(CANDIDATES, SUMMARY, n=3, base_seed=5, exit_key="none")
    assert requests[0]["meta"]["exit_key"] == "none"
    results = [result_for(req, pick, confidence=0.9)
               for req, pick in zip(requests, ["flee:s", "explore:n", "attack:goblin_skulker"])]
    assert tally_votes(results, requests).winner == "none"
    assert tally_votes(results, requests, exit_key="other").winner == "other"


def test_the_outcome_is_deterministic_and_json_serialisable():
    requests, results = vote(["flee:s", "flee:s", "attack:goblin_skulker"], confidence=0.9)
    first, second = tally_votes(results, requests), tally_votes(results, requests)
    assert first == second
    payload = json.loads(json.dumps(first.as_dict()))
    assert payload["winner"] == "flee:s" and payload["votes_cast"] == 3
    assert [b["valid"] for b in payload["ballots"]] == [True, True, True]


# --------------------------------------------------------------------------- #
# as_decision_result: um DecisionResult verdadeiro
# --------------------------------------------------------------------------- #
def test_as_decision_result_is_a_real_decision_result_with_typed_answers():
    requests, results = vote(["flee:s", "flee:s", "attack:goblin_skulker"], confidence=0.97,
                             needs_human=0.1, urgency=1.4)
    outcome = tally_votes(results, requests)
    decision = outcome.as_decision_result(template=results[0])
    assert isinstance(decision, DecisionResult)
    assert set(decision.answers) == {"action", "urgency", "needs_human"}
    assert all(isinstance(a, Answer) for a in decision.answers.values())
    action = decision.answers["action"]
    assert (action.type, action.value) == ("choice", "flee:s")
    assert action.confidence == outcome.confidence
    assert action.band == band_for_choice_or_score(outcome.confidence)
    assert action.probabilities and sum(action.probabilities.values()) == pytest.approx(1.0, abs=1e-4)
    assert action.chosen_probability == action.probabilities["flee:s"]
    assert decision.usage == outcome.usage and decision.usage.input_tokens == 2400
    assert decision.latency_ms == 100
    assert decision.model == "typesafe/jev-1.13" and decision.provider == "typesafe"
    assert decision.issues == outcome.issues and "vote_split:2/3" in decision.issues
    assert decision.raw["vote"]["winner"] == "flee:s"
    assert decision.value("action") == "flee:s"
    assert decision.band("needs_human") == band_for_noul(0.1)


def test_as_decision_result_without_a_valid_ballot_has_an_unanswered_action():
    requests, _ = vote(["flee:s"] * 3)
    decision = tally_votes([None, None, None], requests).as_decision_result()
    assert decision.answers["action"].value is None
    assert decision.answers["action"].confidence is None
    assert decision.answers["action"].band == "hitl"
    assert decision.issues == ("vote_failed:3/3",)


def test_as_decision_result_survives_invalid_thresholds():
    requests, results = vote(["flee:s"] * 3, confidence=0.95)
    decision = tally_votes(results, requests).as_decision_result(auto_threshold=0.2, hitl_threshold=0.9)
    assert decision.answers["action"].band == "auto"  # voltou aos limiares por omissão


# --------------------------------------------------------------------------- #
# o loop lê o resultado agregado SEM mudanças
# --------------------------------------------------------------------------- #
def _macro_actions():
    from system_one_dwarf.agent.types import MacroAction

    return [
        MacroAction(id="attack:goblin_skulker", name_en="Attack the goblin skulker",
                    description_en="Close in and strike.", target="goblin skulker",
                    verb="attack", category="threat"),
        MacroAction(id="flee:s", name_en="Flee south", description_en="Break away.",
                    target="s", verb="flee", category="survival"),
        MacroAction(id="explore:n", name_en="Explore north", description_en="Walk north.",
                    verb="explore", category="objective"),
        MacroAction(id="wait:one_turn", name_en="Wait one turn", description_en="Pass a turn.",
                    verb="wait"),
        MacroAction(id="other", name_en="None of these", description_en="Nothing fits.",
                    verb="other"),
    ]


def _route(decision, requests):
    from system_one_dwarf.agent.loop import _read_action, _routing_reason
    from system_one_dwarf.agent.types import ControlState, LoopConfig

    config = LoopConfig()
    choice = _read_action(decision, _macro_actions(), config, requests[0])
    reason = _routing_reason(ControlState(mode="autopilot"), choice.band, choice.needs_human, choice)
    return choice, reason


def _run_vote(picks, *, seed=11, **kwargs):
    requests = build_vote_requests(_macro_actions(), SUMMARY, n=len(picks), base_seed=seed)
    results = [None if p is None else result_for(req, p, **kwargs) for req, p in zip(requests, picks)]
    return requests, tally_votes(results, requests)


def test_loop_executes_a_unanimous_confident_vote():
    requests, outcome = _run_vote(["flee:s"] * 3, confidence=0.96, needs_human=0.03)
    choice, reason = _route(outcome.as_decision_result(), requests)
    assert choice.action is not None and choice.action.id == "flee:s"
    assert choice.band == "auto" and not choice.needs_human
    assert reason is None


def test_loop_routes_a_split_vote_to_the_human_by_band():
    requests, outcome = _run_vote(["flee:s", "flee:s", "explore:n"], confidence=0.97)
    choice, reason = _route(outcome.as_decision_result(), requests)
    assert choice.action.id == "flee:s"
    assert choice.band == "hitl" and reason == "band_hitl"


def test_loop_reads_a_no_consensus_vote_as_the_exit_option():
    requests, outcome = _run_vote(["flee:s", "explore:n", "attack:goblin_skulker"], confidence=0.9)
    choice, reason = _route(outcome.as_decision_result(), requests)
    assert choice.is_exit and choice.action is not None and choice.action.is_exit
    assert reason == "exit_option"


def test_loop_reads_a_vote_without_valid_ballots_as_an_unanswered_action():
    requests, outcome = _run_vote([None, None, None])
    choice, reason = _route(outcome.as_decision_result(), requests)
    assert choice.action is None and choice.confidence is None
    assert reason == "action_not_in_candidates"


def test_loop_respects_the_aggregated_needs_human():
    requests, outcome = _run_vote(["flee:s"] * 3, confidence=0.96, needs_human=0.05)
    requests2 = build_vote_requests(_macro_actions(), SUMMARY, n=3, base_seed=11)
    results = [result_for(requests2[0], "flee:s", confidence=0.96, needs_human=0.05),
               result_for(requests2[1], "flee:s", confidence=0.96, needs_human=0.7),
               result_for(requests2[2], "flee:s", confidence=0.96, needs_human=0.05)]
    choice, reason = _route(tally_votes(results, requests2).as_decision_result(), requests2)
    assert choice.needs_human and reason == "needs_human"
    assert _route(outcome.as_decision_result(), requests)[1] is None


def test_loop_record_decision_logs_the_aggregated_numbers():
    from system_one_dwarf.agent.loop import AgentLoop
    from system_one_dwarf.agent.types import LoopConfig

    loop = AgentLoop(jev=None, game=None, memory=None, config=LoopConfig(),
                     executor=object(), stuck=object())
    requests, outcome = _run_vote(["flee:s", "flee:s", "explore:n"], confidence=0.97, cost=0.0002,
                                  latency=130)
    record, usage = loop._record_decision("JEV-A", outcome.as_decision_result(), summary="vote")
    assert record.agent == "JEV-A"
    assert record.answers["action"] == "flee:s"
    assert record.confidence == outcome.confidence and record.band == "hitl"
    assert record.cost_usd == pytest.approx(0.0006)
    assert record.latency_ms == 130
    assert "vote_split:2/3" in record.issues
    assert record.probabilities and "flee:s" in record.probabilities
    assert usage.input_tokens == 2400


# --------------------------------------------------------------------------- #
# vote_count_for: só decisões com consequências votam
# --------------------------------------------------------------------------- #
def ids(*values: str) -> list[dict[str, str]]:
    return [{"id": value, "name": value, "description": value} for value in values]


def test_routine_decisions_cost_a_single_call():
    assert vote_count_for(ids("explore:n", "wait:one_turn", "eat:bread", "other")) == 1
    assert vote_count_for(ids("explore:n", "wait:one_turn", "navigate:village")) == 1


@pytest.mark.parametrize("critical", ["attack:wolf", "flee:s", "hunt:search_prey"])
def test_a_critical_verb_in_the_candidates_triggers_the_vote(critical):
    assert vote_count_for(ids("explore:n", critical, "wait:one_turn", "other")) == DEFAULT_VOTES


def test_macro_actions_are_judged_by_their_semantic_verb():
    actions = _macro_actions()
    assert vote_count_for(actions) == DEFAULT_VOTES            # attack + flee
    assert vote_count_for([a for a in actions if a.verb in ("explore", "wait", "other")]) == 1
    from system_one_dwarf.agent.types import MacroAction

    potion = MacroAction(id="drink:healing_potion", name_en="Drink the potion",
                         description_en="Heal.", verb="drink_potion", category="survival")
    water = MacroAction(id="drink:water", name_en="Drink water", description_en="Drink.",
                        verb="drink", category="survival")
    wait = actions[3]
    assert vote_count_for([potion, wait]) == 2  # 2 candidatos reais ⇒ no máximo 2! votos
    assert vote_count_for([water, wait]) == 1


def test_critical_urgency_triggers_the_vote_in_every_shape():
    routine = ids("explore:n", "wait:one_turn", "eat:bread", "other")
    assert vote_count_for(routine, urgency="Safe — no time pressure") == 1
    assert vote_count_for(routine, urgency=None) == 1
    for critical in ("Critical — act now, delay costs health", "critical", 2, 1.7, "2",
                     Answer("urgency", "score", 1.9, 0.9, None, None, "auto", None), {"value": 2.0}):
        assert vote_count_for(routine, urgency=critical) == DEFAULT_VOTES, critical
    for calm in (0, 1, 1.2, "Pressing — act soon", True, float("nan")):
        assert vote_count_for(routine, urgency=calm) == 1, calm


def test_configured_votes_are_clamped_and_k_factorial_bounds_them():
    attack = ids("attack:wolf", "explore:n", "wait:one_turn", "other")
    assert vote_count_for(attack, configured=1) == 1
    assert vote_count_for(attack, configured=0) == 1
    assert vote_count_for(attack, configured=2) == 2
    assert vote_count_for(attack, configured=99) == MAX_VOTES
    assert vote_count_for(ids("attack:wolf", "flee:s", "other"), configured=5) == 2  # 2! = 2


def test_a_single_real_candidate_has_nothing_to_permute():
    assert vote_count_for(ids("attack:wolf", "other")) == 1
    assert vote_count_for(ids("other")) == 1
    assert vote_count_for([]) == 1


def test_custom_critical_verbs_replace_the_defaults():
    candidates = ids("steal:coin", "explore:n", "wait:one_turn", "other")
    assert vote_count_for(candidates) == 1
    assert vote_count_for(candidates, critical_verbs=("steal",)) == DEFAULT_VOTES
    assert vote_count_for(ids("attack:wolf", "explore:n"), critical_verbs=()) == 1
    assert "attack" in CRITICAL_VERBS and "hunt" in CRITICAL_VERBS


@pytest.mark.parametrize("garbage", [None, "attack:wolf", 7, object(), [None, 3], {"id": "attack:x"}])
def test_vote_count_never_raises(garbage):
    assert vote_count_for(garbage) >= 1
    assert vote_count_for(ids("attack:wolf", "explore:n"), configured=garbage) >= 1  # type: ignore[arg-type]
