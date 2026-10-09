"""Fleet request builders for JEV-R / JEV-A / JEV-M / JEV-D.

Each builder's output is a ready-to-send request: ``{state, questions, meta,
ordering}``. Every test asserts the output passes offline validation
(:func:`validate_request`), which is exactly what the client does before
spending tokens.
"""

from __future__ import annotations

import pytest

from system_one_dwarf.jev import (
    DEFAULT_URGENCY_LEVELS,
    JevRequestError,
    NARRATOR_EFFORTS,
    QUESTION_FORMATS,
    RECORD_TYPES,
    build_action,
    build_doubt,
    build_memory,
    build_retrieval,
    shuffle_options,
    unshuffle,
    validate_request,
)

CANDIDATES = [
    {
        "id": "mem-42",
        "title": "River crossing hazards",
        "summary": "Fords are shallow but the current is strong at night.",
        "theme": "travel",
    },
    {
        "id": "mem-7",
        "title": "Wolf pack behaviour",
        "summary": "Wolves hunt in packs after dusk and avoid open ground.",
        "theme": "combat",
    },
]

ACTIONS = [
    {"id": "goto:river_bank", "name": "Go to the river bank", "description": "Walk east to water."},
    {"id": "move:north", "name": "Move north", "description": "Head into the hills."},
    {"id": "make_camp", "name": "Make camp", "description": "Stop and rest where it is safe."},
]


def _clean(request):
    report = validate_request(request)
    assert report.errors == []
    assert report.warnings == []
    return report


# --------------------------------------------------------------------------- #
# JEV-R
# --------------------------------------------------------------------------- #
def test_retrieval_builds_one_noul_per_candidate():
    request = build_retrieval(CANDIDATES, "Night is falling and the river is close.")
    questions = request["questions"]
    assert list(questions) == ["include_mem-42", "include_mem-7", "dominant_theme"]
    assert all(questions[name]["type"] == "noul" for name in ("include_mem-42", "include_mem-7"))

    first = questions["include_mem-42"]
    assert first["instructions"].startswith("[C1] ")
    assert "Would including this knowledge improve the next action decision" in first["instructions"]
    assert first["criteria"]["true"] and first["criteria"]["false"]

    assert request["meta"]["role"] == "JEV-R"
    assert request["meta"]["candidate_labels"] == {"C1": "mem-42", "C2": "mem-7"}
    assert "[C1] River crossing hazards" in request["state"]
    assert "SITUATION: Night is falling" in request["state"]
    _clean(request)


def test_retrieval_handles_the_documented_maximum_of_candidates():
    candidates = [
        {"id": f"mem-{i}", "title": f"Fact {i}", "summary": f"Detail {i}."} for i in range(20)
    ]
    request = build_retrieval(candidates, "Situation.")
    assert len([name for name in request["questions"] if name.startswith("include_")]) == 20
    _clean(request)


def test_retrieval_rejects_more_than_the_maximum():
    candidates = [{"id": f"mem-{i}", "title": f"Fact {i}", "summary": "s"} for i in range(21)]
    with pytest.raises(JevRequestError, match="top-K"):
        build_retrieval(candidates, "Situation.")


def test_retrieval_rejects_empty_or_invalid_candidates():
    with pytest.raises(JevRequestError, match="at least one candidate"):
        build_retrieval([], "Situation.")
    with pytest.raises(JevRequestError, match="id"):
        build_retrieval([{"title": "No id", "summary": "s"}], "Situation.")
    with pytest.raises(JevRequestError, match="duplicate"):
        build_retrieval([CANDIDATES[0], dict(CANDIDATES[0])], "Situation.")
    with pytest.raises(JevRequestError, match="title or a summary"):
        build_retrieval([{"id": "x", "title": "", "summary": ""}], "Situation.")
    with pytest.raises(JevRequestError, match="situation"):
        build_retrieval(CANDIDATES, "   ")


def test_retrieval_theme_choice_can_be_explicit_or_derived():
    derived = build_retrieval(CANDIDATES, "Situation.", shuffle_seed=7)
    assert set(derived["questions"]["dominant_theme"]["criteria"]) == {"travel", "combat", "other"}

    explicit = build_retrieval(CANDIDATES, "Situation.", themes=["survival"], shuffle_seed=7)
    assert "dominant_theme" not in explicit["questions"]
    assert "fewer than two theme options" in explicit["meta"]["theme_choice_skipped"]

    without = build_retrieval(CANDIDATES, "Situation.", include_theme_choice=False)
    assert "dominant_theme" not in without["questions"]


def test_retrieval_theme_choice_keeps_the_exit_option_last():
    request = build_retrieval(CANDIDATES, "Situation.", shuffle_seed=3)
    criteria = list(request["questions"]["dominant_theme"]["criteria"])
    assert criteria[-1] == "other"
    assert sorted(criteria[:-1]) == ["combat", "travel"]
    assert request["ordering"]["dominant_theme"] == criteria


def test_retrieval_truncates_long_summaries():
    candidates = [{"id": "big", "title": "T", "summary": "detail " * 500}]
    request = build_retrieval(candidates, "Situation.", summary_chars=100)
    line = [line for line in request["state"].splitlines() if line.startswith("[C1]")][0]
    assert len(line) < 150 and line.endswith("…")


# --------------------------------------------------------------------------- #
# JEV-A
# --------------------------------------------------------------------------- #
def test_action_request_shape():
    request = build_action(ACTIONS, "Standing in a clearing, night is falling.", shuffle_seed=5)
    questions = request["questions"]
    assert list(questions) == ["action", "urgency", "needs_human"]

    action = questions["action"]
    assert action["type"] == "choice"
    assert set(action["criteria"]) == {"goto:river_bank", "move:north", "make_camp", "other"}
    assert list(action["criteria"])[-1] == "other"  # exit option always last
    assert action["criteria"]["goto:river_bank"].startswith("[A1] Go to the river bank")

    urgency = questions["urgency"]
    assert urgency["type"] == "score"
    assert urgency["criteria"] == list(DEFAULT_URGENCY_LEVELS)
    assert len(urgency["criteria"]) == 3

    needs_human = questions["needs_human"]
    assert needs_human["type"] == "noul"
    assert "human help" in needs_human["instructions"]

    assert request["meta"]["role"] == "JEV-A"
    assert request["meta"]["action_labels"] == {
        "A1": "goto:river_bank",
        "A2": "move:north",
        "A3": "make_camp",
    }
    assert request["ordering"]["action"] == list(action["criteria"])
    _clean(request)


def test_action_always_offers_an_exit_option_even_with_one_candidate():
    request = build_action([ACTIONS[0]], "Situation.", shuffle_seed=0)
    assert list(request["questions"]["action"]["criteria"])[-1] == "other"
    _clean(request)


def test_action_accepts_custom_urgency_levels():
    levels = [
        "Safe — nothing threatens the adventurer",
        "Watchful — a threat is nearby but not engaged",
        "Pressing — act soon, the threat is closing in",
        "Critical — act now, health or position is at risk",
        "Desperate — the adventurer may die this turn",
    ]
    request = build_action(ACTIONS, "Situation.", urgency_levels=levels)
    assert request["questions"]["urgency"]["criteria"] == levels
    _clean(request)


def test_action_rejects_invalid_urgency_levels():
    with pytest.raises(JevRequestError, match="levels"):
        build_action(ACTIONS, "Situation.", urgency_levels=["Only one level"])


def test_action_rejects_bad_candidates_and_exit_key():
    with pytest.raises(JevRequestError, match="at least one action"):
        build_action([], "Situation.")
    with pytest.raises(JevRequestError, match="duplicate"):
        build_action([ACTIONS[0], dict(ACTIONS[0])], "Situation.")
    with pytest.raises(JevRequestError, match="collides"):
        build_action(ACTIONS, "Situation.", exit_key="move:north")
    with pytest.raises(JevRequestError, match="state_summary"):
        build_action(ACTIONS, "  ")


def test_action_rejects_more_candidates_than_the_choice_limit():
    actions = [{"id": f"a{i}", "name": f"Action {i}", "description": "d"} for i in range(255)]
    with pytest.raises(JevRequestError, match="254"):
        build_action(actions, "Situation.")


# --------------------------------------------------------------------------- #
# JEV-M
# --------------------------------------------------------------------------- #
def test_memory_request_shape():
    request = build_memory(
        ["Killed a wolf at dusk.", "Reached the eastern bank."],
        "The adventurer walks east along the river.",
    )
    questions = request["questions"]
    assert list(questions) == ["update_memory", "narrator_effort", "record_type"]
    assert questions["update_memory"]["type"] == "noul"
    assert "narrative memory be updated" in questions["update_memory"]["instructions"]

    effort = questions["narrator_effort"]
    assert effort["type"] == "choice"
    assert list(effort["criteria"]) == list(NARRATOR_EFFORTS)  # ordered scale, never shuffled
    assert len(effort["criteria"]) == 7

    record = questions["record_type"]
    assert list(record["criteria"]) == list(RECORD_TYPES)
    assert record["criteria"]["skip"]  # exit option present -> no warning

    assert "NARRATIVE TAIL" in request["state"]
    assert "2. Reached the eastern bank." in request["state"]
    assert request["meta"]["role"] == "JEV-M"
    _clean(request)


def test_memory_caps_events_and_truncates_them():
    events = [f"event {i}" for i in range(30)]
    request = build_memory(events, "", max_events=5, event_chars=20)
    assert request["meta"]["events_sent"] == 5
    assert request["meta"]["events_omitted"] == 25
    assert "last 5 of 30" in request["state"]
    assert "event 29" in request["state"] and "event 25" in request["state"]
    assert "event 24" not in request["state"]
    _clean(request)


def test_memory_needs_something_to_look_at():
    with pytest.raises(JevRequestError, match="narrative tail"):
        build_memory([], "   ")
    with pytest.raises(JevRequestError, match="string"):
        build_memory([1, 2], "narrative")


def test_memory_accepts_narrative_only():
    request = build_memory([], "The adventurer has just arrived.")
    assert "update_memory" in request["questions"]
    _clean(request)


# --------------------------------------------------------------------------- #
# JEV-D
# --------------------------------------------------------------------------- #
def test_doubt_request_shape():
    request = build_doubt("The river may be too deep to ford at night.", 0.62, shuffle_seed=11)
    questions = request["questions"]
    assert list(questions) == ["ask_human", "question_format"]
    assert questions["ask_human"]["type"] == "noul"
    assert set(questions["question_format"]["criteria"]) == set(QUESTION_FORMATS)
    assert request["meta"]["action_confidence"] == 0.62
    assert "0.62" in request["state"]
    assert "do not re-estimate" in request["state"]
    _clean(request)


@pytest.mark.parametrize("confidence", [None, 0.0])
def test_doubt_marks_unavailable_confidence(confidence):
    request = build_doubt("Unclear what to do next.", confidence)
    assert "unavailable" in request["state"]
    _clean(request)


@pytest.mark.parametrize("confidence", [-0.1, 1.1, "high", True])
def test_doubt_rejects_invalid_confidence(confidence):
    with pytest.raises(JevRequestError, match="action_confidence"):
        build_doubt("Unclear what to do next.", confidence)


def test_doubt_requires_context():
    with pytest.raises(JevRequestError, match="context"):
        build_doubt("", 0.5)


# --------------------------------------------------------------------------- #
# option-order bias helpers
# --------------------------------------------------------------------------- #
def test_shuffle_options_is_deterministic_given_a_seed():
    criteria = {"a": "A.", "b": "B.", "c": "C.", "d": "D."}
    first, order_a = shuffle_options(criteria, 42)
    second, order_b = shuffle_options(criteria, 42)
    assert first == second and order_a == order_b
    assert sorted(first) == sorted(criteria)
    assert sorted(order_a) == sorted(criteria)
    assert order_a != sorted(criteria)  # a different order was actually produced


def test_shuffle_options_without_a_seed_varies():
    criteria = {f"opt_{i}": f"Option {i}." for i in range(12)}
    orders = {tuple(shuffle_options(criteria)[1]) for _ in range(12)}
    assert len(orders) > 1


def test_shuffle_options_can_pin_the_exit_option_last():
    criteria = {"a": "A.", "b": "B.", "other": "None."}
    shuffled, order = shuffle_options(criteria, 1, pin_last="other")
    assert order[-1] == "other"
    assert set(shuffled) == set(criteria)


def test_unshuffle_round_trip():
    criteria = {"goto:river": "Walk east.", "wait": "Hold.", "other": "None."}
    shuffled, order = shuffle_options(criteria, 5, pin_last="other")

    # choice answers are keys: identity
    for key in shuffled:
        assert unshuffle(key, order) == key
    # positional labels map back through the presented order
    for position, key in enumerate(order, 1):
        assert unshuffle(position, order) == key
        assert unshuffle(str(position), order) == key
        assert unshuffle(f"opt_{position}", order) == key
        assert unshuffle(f"option {position}", order) == key
    assert unshuffle(0, order) == order[0]


def test_unshuffle_rejects_unknown_values():
    with pytest.raises(JevRequestError, match="cannot unshuffle"):
        unshuffle("marketing", ["a", "b"])
    with pytest.raises(JevRequestError, match="cannot unshuffle"):
        unshuffle(9, ["a", "b"])
    with pytest.raises(JevRequestError, match="shuffle_options"):
        unshuffle("a", [])


def test_builders_can_keep_the_canonical_order():
    request = build_action(ACTIONS, "Situation.", shuffle=False)
    assert list(request["questions"]["action"]["criteria"]) == [
        "goto:river_bank",
        "move:north",
        "make_camp",
        "other",
    ]
    assert request["ordering"] == {}


# --------------------------------------------------------------------------- #
# every builder stays inside the context budget
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "build",
    [
        lambda: build_retrieval(CANDIDATES, "Situation."),
        lambda: build_action(ACTIONS, "Situation."),
        lambda: build_memory(["event"], "narrative"),
        lambda: build_doubt("context", 0.5),
    ],
)
def test_fleet_requests_are_compact(build):
    from system_one_dwarf.jev import WARN_CONTEXT_TOKENS

    report = validate_request(build())
    assert report.ok
    assert report.total_tokens < WARN_CONTEXT_TOKENS


def test_fleet_requests_are_json_serializable():
    import json

    requests = [
        build_retrieval(CANDIDATES, "Situation.", shuffle_seed=1),
        build_action(ACTIONS, "Situation.", shuffle_seed=1),
        build_memory(["event"], "narrative"),
        build_doubt("context", 0.5, shuffle_seed=1),
    ]
    for request in requests:
        decoded = json.loads(json.dumps(request))
        assert decoded["state"] and decoded["questions"]
        for body in decoded["questions"].values():
            assert set(body) == {"type", "instructions", "criteria"}


def test_question_objects_survive_copy_and_pickle():
    import copy
    import pickle

    question = build_retrieval(CANDIDATES, "Situation.")["questions"]["include_mem-42"]
    for clone in (copy.deepcopy(question), pickle.loads(pickle.dumps(question))):
        assert clone == question
        assert clone.name == "include_mem-42"
        assert clone.to_body() == question.to_body()
