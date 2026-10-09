"""Builder and validator behaviour for the three Jev primitives."""

from __future__ import annotations

import json
import warnings

import pytest

from system_one_dwarf.jev import (
    JevRequestError,
    JevWarning,
    Question,
    asks_for_text,
    check_question,
    choice,
    is_exit_option,
    looks_numeric_level,
    noul,
    score,
)

GOOD_NOUL = ("Is this report a defect?", "It describes broken behaviour.", "It is a request.")


def _captured_warnings():
    return warnings.catch_warnings(record=True)


def _jev_warnings(caught) -> list:
    return [w for w in caught if issubclass(w.category, JevWarning)]


# --------------------------------------------------------------------------- #
# shapes
# --------------------------------------------------------------------------- #
def test_noul_shape():
    question = noul("is_bug", *GOOD_NOUL)
    assert isinstance(question, dict)
    assert question.name == "is_bug"
    assert question["type"] == "noul"
    assert question["instructions"] == GOOD_NOUL[0]
    assert question["criteria"] == {"true": GOOD_NOUL[1], "false": GOOD_NOUL[2]}
    assert sorted(question) == ["criteria", "instructions", "type"]


def test_choice_shape():
    question = choice("team", "Which team?", {"payments": "Billing.", "other": "Nobody."})
    assert question.name == "team"
    assert question["type"] == "choice"
    assert question["criteria"] == {"payments": "Billing.", "other": "Nobody."}


def test_score_shape():
    levels = ["Can wait", "This week", "Blocking now"]
    question = score("urgency", "How urgent?", levels)
    assert question.name == "urgency"
    assert question["type"] == "score"
    assert question["criteria"] == levels
    assert question["criteria"] is not levels  # copied, not aliased


def test_questions_serialise_as_plain_wire_bodies():
    request = {
        "model": "typesafe/jev-1.13",
        "state": "s",
        "questions": {"a": noul("a", *GOOD_NOUL), "b": score("b", "How bad?", ["Mild pain, can keep going", "Severe pain, must stop"])},
    }
    payload = json.dumps(request)
    decoded = json.loads(payload)
    assert decoded["questions"]["a"] == {
        "type": "noul",
        "instructions": GOOD_NOUL[0],
        "criteria": {"true": GOOD_NOUL[1], "false": GOOD_NOUL[2]},
    }
    assert set(decoded["questions"]["b"]) == {"type", "instructions", "criteria"}


# --------------------------------------------------------------------------- #
# choice rules
# --------------------------------------------------------------------------- #
def test_choice_without_exit_option_warns_but_builds():
    with pytest.warns(JevWarning, match="exit option"):
        question = choice("team", "Which team?", {"payments": "Billing.", "frontend": "UI."})
    assert question["type"] == "choice"


@pytest.mark.parametrize("key", ["other", "none", "undecided", "skip", "No_Action", "none of the above"])
def test_choice_with_exit_option_is_quiet(key):
    with _captured_warnings() as caught:
        warnings.simplefilter("always")
        choice("team", "Which team?", {"payments": "Billing.", key: "Escape hatch."})
    assert _jev_warnings(caught) == []


def test_choice_accepts_the_documented_maximum():
    criteria = {f"opt_{i}": f"Option {i}." for i in range(254)}
    criteria["other"] = "Nobody."
    question = choice("many", "Pick one.", criteria)
    assert len(question["criteria"]) == 255


def test_choice_over_maximum_options_raises():
    criteria = {f"opt_{i}": f"Option {i}." for i in range(255)}
    criteria["other"] = "Nobody."
    with pytest.raises(JevRequestError, match="255"):
        choice("many", "Pick one.", criteria)


def test_choice_without_options_raises():
    with pytest.raises(JevRequestError, match="no options"):
        choice("team", "Which team?", {})


def test_choice_with_empty_option_key_raises():
    with pytest.raises(JevRequestError, match="empty option key"):
        choice("team", "Which team?", {"": "Billing.", "other": "Nobody."})


def test_choice_with_empty_description_raises():
    with pytest.raises(JevRequestError, match="description"):
        choice("team", "Which team?", {"payments": "  ", "other": "Nobody."})


def test_choice_criteria_must_be_a_mapping():
    with pytest.raises(JevRequestError, match="mapping"):
        choice("team", "Which team?", ["payments", "frontend"])


# --------------------------------------------------------------------------- #
# score rules
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("levels", [["Only one"], []])
def test_score_below_two_levels_raises(levels):
    with pytest.raises(JevRequestError, match="levels"):
        score("urgency", "How urgent?", levels)


def test_score_above_ten_levels_raises():
    with pytest.raises(JevRequestError, match="levels"):
        score("urgency", "How urgent?", [f"Situation {i}" for i in range(11)])


@pytest.mark.parametrize("count", [2, 10])
def test_score_accepts_documented_bounds(count):
    question = score("urgency", "How urgent?", [f"Situation number {i}" for i in range(count)])
    assert len(question["criteria"]) == count


@pytest.mark.parametrize(
    "levels",
    [
        ["0", "1"],
        ["1", "2", "3"],
        ["low", "medium", "high"],
        ["Low", "Medium", "High"],
        ["level 0", "level 1"],
    ],
)
def test_score_numeric_levels_warn_but_build(levels):
    with pytest.warns(JevWarning, match="numeric"):
        question = score("urgency", "How urgent?", levels)
    assert question["criteria"] == levels


def test_score_concrete_levels_are_quiet():
    with _captured_warnings() as caught:
        warnings.simplefilter("always")
        score(
            "urgency",
            "How urgent?",
            ["Safe — no time pressure", "Pressing — act soon", "Critical — act now"],
        )
    assert _jev_warnings(caught) == []


def test_score_duplicate_levels_warn():
    with pytest.warns(JevWarning, match="duplicated"):
        score("urgency", "How urgent?", ["Act now", "Act now"])


def test_score_levels_must_be_strings():
    with pytest.raises(JevRequestError, match="non-empty string"):
        score("urgency", "How urgent?", ["Act now", 3])


# --------------------------------------------------------------------------- #
# instructions rules
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "instructions",
    [
        "Explain the situation.",
        "Write a summary of the fight.",
        "Summarize the last events.",
        "Describe why the agent should move.",
        "Translate the screen text.",
    ],
)
def test_text_generation_instructions_are_rejected(instructions):
    with pytest.raises(JevRequestError, match="generated text"):
        noul("x", instructions, "Yes.", "No.")


@pytest.mark.parametrize(
    "instructions",
    [
        "Should the narrative memory be updated now?",
        "Does the summary of the fight matter for the next action?",
        "Is this report a defect?",
        "Is the adventurer writing anything down?",
    ],
)
def test_atomic_typed_instructions_are_accepted(instructions):
    question = noul("x", instructions, "Yes.", "No.")
    assert question["instructions"] == instructions


@pytest.mark.parametrize("instructions", ["", "   ", None, 42])
def test_empty_or_non_string_instructions_raise(instructions):
    with pytest.raises(JevRequestError, match="instructions"):
        noul("x", instructions, "Yes.", "No.")


# --------------------------------------------------------------------------- #
# noul rules
# --------------------------------------------------------------------------- #
def test_noul_identical_criteria_warn():
    with pytest.warns(JevWarning, match="identical"):
        noul("x", "Is it a defect?", "Broken behaviour.", "Broken behaviour.")


def test_noul_missing_criteria_key_raises_via_validator():
    issues = check_question(
        "x", {"type": "noul", "instructions": "Is it a defect?", "criteria": {"true": "Yes."}}
    )
    assert not issues.ok
    assert any("missing" in error for error in issues.errors)


def test_noul_criteria_must_be_described():
    with pytest.raises(JevRequestError, match="criteria"):
        noul("x", "Is it a defect?", "Yes.", "")


# --------------------------------------------------------------------------- #
# check_question never raises
# --------------------------------------------------------------------------- #
def test_check_question_collects_errors_and_warnings():
    issues = check_question(
        "broken",
        {"type": "choice", "instructions": "Explain it.", "criteria": {"": ""}},
    )
    assert not issues.ok
    assert issues.errors and issues.warnings
    assert "generated text" in issues.format()
    assert "WARNING" in issues.format()


def test_check_question_unknown_type():
    issues = check_question("x", {"type": "essay", "instructions": "Go.", "criteria": {}})
    assert any("unknown type" in error for error in issues.errors)


def test_check_question_rejects_non_mapping():
    issues = check_question("x", ["not", "a", "question"])
    assert any("mapping" in error for error in issues.errors)


def test_check_question_accepts_question_objects():
    issues = check_question("is_bug", noul("is_bug", *GOOD_NOUL))
    assert issues.ok and not issues.warnings


# --------------------------------------------------------------------------- #
# public helpers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "key,expected",
    [("other", True), ("none", True), ("undecided", True), ("skip", True), ("payments", False)],
)
def test_is_exit_option(key, expected):
    assert is_exit_option(key) is expected


@pytest.mark.parametrize(
    "level,expected",
    [("0", True), ("1", True), ("low", True), ("medium", True), ("Level 2", True), ("Safe now", False)],
)
def test_looks_numeric_level(level, expected):
    assert looks_numeric_level(level) is expected


@pytest.mark.parametrize(
    "instructions,expected",
    [("Explain it", "explain"), ("Just do it", None), ("Summarize now", "summarize")],
)
def test_asks_for_text(instructions, expected):
    found = asks_for_text(instructions)
    assert (found.lower() if found else None) == expected


def test_question_name_must_be_a_string():
    with pytest.raises(JevRequestError, match="name"):
        noul("", "Is it a defect?", "Yes.", "No.")


def test_question_equality_with_plain_dict():
    question = noul("is_bug", *GOOD_NOUL)
    assert question == {
        "type": "noul",
        "instructions": GOOD_NOUL[0],
        "criteria": {"true": GOOD_NOUL[1], "false": GOOD_NOUL[2]},
    }
    assert isinstance(question, Question)
