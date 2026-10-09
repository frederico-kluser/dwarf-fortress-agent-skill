"""``agent.narrator``: the JEV-M decision, the effort ladder and the memory writes.

Offline throughout: the narrator talks to a ``DeepSeekLLM`` backed by
``FakeTransport`` (so the reasoning payload of each call is assertable) or to a
``ScriptedLLM``, and to a ``FakeMemory`` that records every write.
"""

from __future__ import annotations

import json

import pytest

from system_one_dwarf.agent import (
    EPISODE,
    FACT,
    PROCEDURE,
    DeepSeekLLM,
    LLMUsage,
    NarrationDraft,
    NarrationResult,
    Narrator,
    Skip,
    build_prompt_state,
    condense_to_budget,
    parse_narration,
    read_jev_answer,
    word_count,
)
from system_one_dwarf.agent.llm import LLMResult
from system_one_dwarf.agent.narrator import (
    EFFORT_ANSWER_KEYS,
    RECORD_TYPE_ANSWER_KEYS,
    UPDATE_ANSWER_KEYS,
)
from system_one_dwarf.jev.client import Answer, DecisionResult
from system_one_dwarf.jev.client import Usage as JevUsage

from .conftest import (
    FAKE_KEY,
    FakeMemory,
    FakeResponse,
    FakeTransport,
    ScriptedLLM,
    chat_payload,
    narration_payload,
)

NOW = "2026-10-09T00:00:00.000Z"
EVENTS = [
    "Woke up in a clearing near the river.",
    "Drank from the river and refilled the waterskin.",
    "Spotted a wolf pack to the north-east.",
]


def make_narrator(transport: FakeTransport, memory: FakeMemory | None = None, **kwargs) -> Narrator:
    llm = DeepSeekLLM(lambda: FAKE_KEY, transport=transport)
    return Narrator(llm, memory, **kwargs)


def jev_m_result(*, update: float = 0.93, effort: str = "high", record_type: str = EPISODE) -> DecisionResult:
    """A genuine ``DecisionResult`` in the shape ``build_memory`` produces."""
    return DecisionResult(
        answers={
            "update_memory": Answer(
                name="update_memory",
                type="noul",
                value=update,
                confidence=None,
                probabilities=None,
                legend=None,
                band="auto",
                chosen_probability=None,
            ),
            "narrator_effort": Answer(
                name="narrator_effort",
                type="choice",
                value=effort,
                confidence=0.81,
                probabilities={effort: 0.81},
                legend=None,
                band="hitl",
                chosen_probability=0.81,
            ),
            "record_type": Answer(
                name="record_type",
                type="choice",
                value=record_type,
                confidence=0.77,
                probabilities={record_type: 0.77},
                legend=None,
                band="hitl",
                chosen_probability=0.77,
            ),
        },
        usage=JevUsage(),
        latency_ms=12,
        model="typesafe/jev-1.13",
        raw={},
    )


# ------------------------------------------------------------------ skip paths


def test_update_false_skips_with_zero_calls(memory: FakeMemory):
    llm = ScriptedLLM()  # no answers: any call would explode
    narrator = Narrator(llm, memory)

    result = narrator.consider({"update": False, "effort": "max", "record_type": EPISODE}, EVENTS, "tail")

    assert isinstance(result, Skip)
    assert result.reason == "update_false"
    assert result.cost_usd == 0.0
    assert result.calls == 0
    assert llm.call_count == 0
    assert memory.calls == []


def test_update_below_threshold_skips(memory: FakeMemory):
    llm = ScriptedLLM()
    narrator = Narrator(llm, memory)

    result = narrator.consider({"update_memory": 0.31}, EVENTS, "tail")

    assert isinstance(result, Skip)
    assert result.reason == "update_below_threshold"
    assert result.update_probability == 0.31
    assert llm.call_count == 0


def test_record_type_skip_skips_even_when_update_is_true(memory: FakeMemory):
    llm = ScriptedLLM()
    narrator = Narrator(llm, memory)

    result = narrator.consider({"update": True, "effort": "high", "record_type": "skip"}, EVENTS, "tail")

    assert isinstance(result, Skip)
    assert result.reason == "record_type_skip"
    assert result.record_type == "skip"
    assert llm.call_count == 0


def test_nothing_to_narrate_skips(memory: FakeMemory):
    llm = ScriptedLLM()
    narrator = Narrator(llm, memory)

    result = narrator.consider({"update": True}, [], "   ")

    assert isinstance(result, Skip)
    assert result.reason == "no_input"
    assert llm.call_count == 0


def test_effort_none_can_skip_only_when_asked_to(memory: FakeMemory):
    answer = {"update": True, "effort": "none", "record_type": EPISODE}

    strict = Narrator(ScriptedLLM(), memory, skip_on_effort_none=True)
    skipped = strict.consider(answer, EVENTS, "tail")
    assert isinstance(skipped, Skip) and skipped.reason == "effort_none"

    # Default: `none` still writes the chronicle, with reasoning disabled.
    transport = FakeTransport(narration_payload("Chronicle.", "An episode."))
    narrator = make_narrator(transport, memory)
    result = narrator.consider(answer, EVENTS, "tail", now=NOW)
    assert isinstance(result, NarrationResult)
    assert transport.last_body["reasoning"] == {"enabled": False}


def test_skip_payload_is_json_serialisable():
    payload = Skip("update_false", effort="max", record_type=EPISODE, ts=NOW).as_dict()
    assert payload["skipped"] is True
    assert json.loads(json.dumps(payload))["cost_usd"] == 0.0


# --------------------------------------------------- the JEV-M answer adapter


def test_consider_reads_a_real_decision_result(memory: FakeMemory):
    transport = FakeTransport(narration_payload("The chronicle.", "Crossed the ford at dawn."))
    narrator = make_narrator(transport, memory)

    result = narrator.consider(jev_m_result(effort="max", record_type=FACT), EVENTS, "tail", now=NOW)

    assert isinstance(result, NarrationResult)
    assert result.effort == "max"
    assert result.record_type == FACT
    assert transport.last_body["reasoning"] == {"effort": "max"}
    assert memory.methods() == ["set_narrative", "add_fact"]


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ({"update_memory": 0.91}, 0.91),
        ({"update": 0.4}, 0.4),
        ({"should_update": True}, True),
        ({"narrative_update": "false"}, "false"),
        ({"update": "0.8"}, "0.8"),
    ],
)
def test_read_jev_answer_aliases(answer: dict, expected):
    assert (
        read_jev_answer(answer, *UPDATE_ANSWER_KEYS) == expected
    )


def test_update_flag_values_are_coerced_before_the_decision(memory: FakeMemory):
    llm = ScriptedLLM()
    narrator = Narrator(llm, memory)

    assert narrator.consider({"update": "false"}, EVENTS, "tail").reason == "update_false"
    assert narrator.consider({"update": "0.2"}, EVENTS, "tail").reason == "update_below_threshold"
    assert llm.call_count == 0


def test_read_jev_answer_from_attributes_and_decision_result():
    assert read_jev_answer(jev_m_result(), "narrator_effort") == "high"
    assert read_jev_answer(jev_m_result(), *EFFORT_ANSWER_KEYS) == "high"  # alias resolves
    assert read_jev_answer(jev_m_result(), *RECORD_TYPE_ANSWER_KEYS) == EPISODE
    assert read_jev_answer(jev_m_result(), "missing", default="fallback") == "fallback"
    assert read_jev_answer(None, "update", default=None) is None


# ---------------------------------------------------------------- effort ladder


@pytest.mark.parametrize(
    ("effort", "expected"),
    [
        ("max", {"effort": "max"}),
        ("xhigh", {"effort": "high"}),
        ("high", {"effort": "high"}),
        ("medium", {"effort": "low"}),
        ("low", {"effort": "low"}),
        ("minimal", {"enabled": False}),
        ("none", {"enabled": False}),
    ],
)
def test_narration_effort_comes_from_the_jev_m_answer(effort: str, expected: dict, memory: FakeMemory):
    transport = FakeTransport(narration_payload("Chronicle line.", "An episode."))
    narrator = make_narrator(transport, memory)

    result = narrator.narrate(
        {"update": True, "effort": effort, "record_type": EPISODE}, EVENTS, "tail", now=NOW
    )

    assert transport.last_body["reasoning"] == expected
    assert result.effort == effort
    assert result.reasoning_param == expected
    assert len(transport.calls) == 1  # narrative + record in ONE call


def test_unsupported_effort_degrades_and_never_loses_the_narration(memory: FakeMemory):
    transport = FakeTransport(
        FakeResponse(422, {"error": {"message": "unsupported reasoning effort"}}),
        narration_payload("Chronicle after fallback.", "An episode."),
    )
    narrator = make_narrator(transport, memory)

    result = narrator.narrate({"effort": "max", "record_type": EPISODE}, EVENTS, "tail", now=NOW)

    assert transport.reasons() == [{"effort": "max"}, {"effort": "high"}]
    assert result.narrative == "Chronicle after fallback."
    assert result.draft.reasoning == "effort:high"
    assert memory.narrative == "Chronicle after fallback."


def test_explicit_effort_override_wins_over_the_answer(memory: FakeMemory):
    transport = FakeTransport(narration_payload("Chronicle.", "An episode."))
    narrator = make_narrator(transport, memory)

    result = narrator.narrate({"effort": "low"}, EVENTS, "tail", effort="max", now=NOW)

    assert result.effort == "max"
    assert transport.last_body["reasoning"] == {"effort": "max"}


def test_unknown_effort_uses_the_default_level(memory: FakeMemory):
    transport = FakeTransport(narration_payload("Chronicle.", "An episode."))
    narrator = make_narrator(transport, memory)

    result = narrator.narrate({"effort": "turbo"}, EVENTS, "tail", now=NOW)

    assert result.effort == "medium"
    assert transport.last_body["reasoning"] == {"effort": "low"}


# ---------------------------------------------------------------- word budget


def test_rolling_line_is_condensed_into_the_word_budget(memory: FakeMemory):
    long_narrative = " ".join(f"Sentence number {index} with several words here." for index in range(60))
    assert word_count(long_narrative) > 250
    transport = FakeTransport(narration_payload(long_narrative, "An episode."))
    narrator = make_narrator(transport, memory, max_words=250)

    result = narrator.narrate({"effort": "max", "record_type": EPISODE}, EVENTS, "tail", now=NOW)

    assert result.word_count <= 250
    assert result.draft.trimmed is True
    assert memory.narrative == result.narrative  # the stored line is the trimmed one
    assert result.narrative.startswith("Sentence number 0")  # the goal/head survives
    assert any("condensed" in issue for issue in result.draft.issues)


def test_short_rolling_line_is_left_alone(memory: FakeMemory):
    transport = FakeTransport(narration_payload("A short chronicle line.", "An episode."))
    narrator = make_narrator(transport, memory)

    result = narrator.narrate({"effort": "low"}, EVENTS, "tail", now=NOW)

    assert result.draft.trimmed is False
    assert result.narrative == "A short chronicle line."


@pytest.mark.parametrize(
    ("text", "limit", "expected"),
    [
        ("one two three", 5, ("one two three", False)),
        ("one two three four five", 3, ("three four five", True)),
        ("First goal here. Middle detail. Newest event now.", 6, ("First goal here. Newest event now.", True)),
    ],
)
def test_condense_to_budget_is_deterministic(text: str, limit: int, expected: tuple):
    assert condense_to_budget(text, limit) == expected


def test_word_count_helper():
    assert word_count("  two   words ") == 2
    assert word_count("") == 0


# -------------------------------------------------------------- memory routing


@pytest.mark.parametrize(
    ("record_type", "method"),
    [(EPISODE, "add_episode"), (FACT, "add_fact"), (PROCEDURE, "add_procedure")],
)
def test_record_is_routed_to_the_right_memory_method(record_type: str, method: str, memory: FakeMemory):
    transport = FakeTransport(narration_payload("Chronicle line.", "The record text."))
    narrator = make_narrator(transport, memory)

    result = narrator.narrate(
        {"update": True, "effort": "high", "record_type": record_type}, EVENTS, "tail", now=NOW
    )

    assert memory.methods() == ["set_narrative", method]
    content = memory.contents(method)[0]
    if record_type == EPISODE:
        assert content == f"{NOW} — The record text."  # episodes are dated by code
    else:
        assert content == "The record text."
        assert not content.startswith(NOW)
    assert memory.kwargs_of(method)["origin"] == "agent"
    assert memory.kwargs_of("set_narrative")["origin"] == "agent"
    assert result.record_type == record_type
    assert result.memory["record"]["ok"] is True


def test_record_type_skip_never_reaches_the_memory(memory: FakeMemory):
    transport = FakeTransport(narration_payload("Chronicle line.", "Ignored record."))
    narrator = make_narrator(transport, memory)

    result = narrator.narrate({"record_type": "skip"}, EVENTS, "tail", now=NOW)

    assert memory.methods() == ["set_narrative"]


def test_episode_keeps_a_timestamp_the_model_already_wrote(memory: FakeMemory):
    transport = FakeTransport(narration_payload("Chronicle.", f"{NOW} — already dated."))
    narrator = make_narrator(transport, memory)

    narrator.narrate({"record_type": EPISODE}, EVENTS, "tail", now=NOW)

    assert memory.contents("add_episode") == [f"{NOW} — already dated."]


def test_narrative_only_update_when_records_are_disabled(memory: FakeMemory):
    transport = FakeTransport(narration_payload("Chronicle line.", "The record text."))
    narrator = make_narrator(transport, memory, write_records=False)

    narrator.narrate({"record_type": EPISODE}, EVENTS, "tail", now=NOW)

    assert memory.methods() == ["set_narrative"]


def test_memory_failure_is_reported_and_does_not_raise():
    memory = FakeMemory("add_episode")
    transport = FakeTransport(narration_payload("Chronicle line.", "The record text."))
    narrator = make_narrator(transport, memory)

    result = narrator.narrate({"record_type": EPISODE}, EVENTS, "tail", now=NOW)

    assert result.narrative == "Chronicle line."
    assert memory.narrative == "Chronicle line."
    assert any("add_episode failed" in error for error in result.errors)


def test_missing_memory_is_reported():
    transport = FakeTransport(narration_payload("Chronicle line.", "The record text."))
    narrator = make_narrator(transport, None)

    result = narrator.narrate({"record_type": EPISODE}, EVENTS, "tail", now=NOW)

    assert result.memory == {}
    assert any("no memory attached" in error for error in result.errors)


def test_compose_writes_nothing(memory: FakeMemory):
    transport = FakeTransport(narration_payload("Chronicle line.", "The record text."))
    narrator = make_narrator(transport, memory)

    draft = narrator.compose({"effort": "max", "record_type": EPISODE}, EVENTS, "tail", now=NOW)

    assert isinstance(draft, NarrationDraft)
    assert draft.narrative == "Chronicle line."
    assert draft.record == "The record text."
    assert draft.word_count == 2
    assert memory.calls == []


# ----------------------------------------------------------------- prompt state


def test_prompt_state_keeps_only_the_rolling_line_and_the_last_events():
    events = [f"EVENT {index} " + "word " * 100 for index in range(1, 51)]
    state = build_prompt_state(events, "The chronicle so far.", record_type=EPISODE, now=NOW)

    assert "NOW (UTC): 2026-10-09T00:00:00.000Z" in state
    assert "RECORD TO PRODUCE: episode" in state
    assert "The chronicle so far." in state
    assert "newest 20 of 50" in state
    assert "EVENT 30 " not in state
    assert "EVENT 31 " in state
    assert "EVENT 50 " in state
    # every event is truncated to the per-event budget
    assert max(len(line) for line in state.splitlines() if line.startswith("1. ")) <= 400
    assert len(state) < 12_000


def test_system_prompt_carries_the_word_budget_and_the_protocol():
    narrator = Narrator(ScriptedLLM(), None, min_words=150, max_words=250)
    prompt = narrator.system_prompt()

    assert "150 and 250 words" in prompt
    assert "English" in prompt
    assert "never invent" in prompt.lower()
    assert '"narrative"' in prompt and '"record"' in prompt


# ------------------------------------------------------------ output protocol


def test_parse_narration_reads_the_json_protocol():
    narrative, record, issues = parse_narration('```json\n{"narrative": "N", "record": "R"}\n```')
    assert (narrative, record, issues) == ("N", "R", ())


def test_parse_narration_falls_back_to_labelled_sections():
    narrative, record, issues = parse_narration("NARRATIVE: the line\nRECORD: the record")
    assert narrative == "the line"
    assert record == "the record"
    assert issues and "labelled" in issues[0]


def test_parse_narration_uses_bare_text_as_the_narrative():
    narrative, record, issues = parse_narration("Just a chronicle line.")
    assert narrative == "Just a chronicle line."
    assert record == ""
    assert issues and "whole answer" in issues[0]


def test_record_is_used_as_the_narrative_when_the_protocol_slips(memory: FakeMemory):
    transport = FakeTransport(narration_payload("", "Only the record came back."))
    narrator = make_narrator(transport, memory)

    result = narrator.narrate({"record_type": FACT}, EVENTS, "tail", now=NOW)

    assert result.narrative == "Only the record came back."
    assert any("record used as the narrative" in issue for issue in result.draft.issues)


def test_missing_record_still_updates_the_narrative(memory: FakeMemory):
    transport = FakeTransport(narration_payload("Chronicle only.", ""))
    narrator = make_narrator(transport, memory)

    result = narrator.narrate({"record_type": EPISODE}, EVENTS, "tail", now=NOW)

    assert memory.methods() == ["set_narrative"]
    assert result.record == ""


# --------------------------------------------------------------- accounting


def test_narration_reports_usage_and_purpose(memory: FakeMemory):
    events = []
    llm = DeepSeekLLM(lambda: FAKE_KEY, transport=FakeTransport(narration_payload("N", "R")), usage_callback=events.append)
    narrator = Narrator(llm, memory)

    result = narrator.narrate({"effort": "high", "record_type": EPISODE}, EVENTS, "tail", now=NOW)

    assert result.usage.cost_usd == 0.000023
    assert result.cost_usd == 0.000023
    assert result.latency_ms >= 0
    assert events[0].purpose == "narrator"
    assert narrator.calls == 1


def test_narration_result_is_json_serialisable(memory: FakeMemory):
    transport = FakeTransport(narration_payload("Chronicle line.", "The record text."))
    narrator = make_narrator(transport, memory)

    result = narrator.narrate({"effort": "max", "record_type": EPISODE}, EVENTS, "tail", now=NOW)
    payload = json.loads(json.dumps(result.as_dict()))

    assert payload["narrative"] == "Chronicle line."
    assert payload["effort"] == "max"
    assert payload["stored"] == ["narrative", "record"]
    assert payload["usage"]["output_tokens"] == 40


def test_llm_error_propagates_from_narrate(memory: FakeMemory):
    from system_one_dwarf.agent import LLMAuthError

    llm = ScriptedLLM(LLMAuthError("no key"))
    narrator = Narrator(llm, memory)

    with pytest.raises(LLMAuthError):
        narrator.narrate({"effort": "high"}, EVENTS, "tail", now=NOW)

    assert memory.calls == []


def test_scripted_llm_receives_the_compact_prompt(memory: FakeMemory):
    llm = ScriptedLLM(json.dumps({"narrative": "N", "record": "R"}))
    narrator = Narrator(llm, memory)

    narrator.narrate({"effort": "low", "record_type": FACT}, ["only event"], "old line", now=NOW)

    assert llm.call_count == 1
    call = llm.calls[0]
    assert call["purpose"] == "narrator"
    assert call["reasoning"] == "low"
    assert "only event" in call["user"]
    assert "old line" in call["user"]
    assert call["max_tokens"] == 1200
    assert isinstance(call["system"], str) and "memory narrator" in call["system"]


def test_result_helper_defaults():
    draft = NarrationDraft(narrative="n", record="r", record_type=FACT, usage=LLMUsage())
    assert draft.cost_usd == 0.0
    assert NarrationResult(draft=draft).errors == ()

# ------------------------------------------------- helper/parsing branch coverage


def test_read_jev_answer_accepts_objects_exposing_attributes():
    from types import SimpleNamespace

    assert read_jev_answer(SimpleNamespace(update=0.8), *UPDATE_ANSWER_KEYS) == 0.8
    assert read_jev_answer(SimpleNamespace(narrator_effort="max"), *EFFORT_ANSWER_KEYS) == "max"
    assert read_jev_answer(object(), "update", default="none") == "none"
    assert read_jev_answer({"Update_Memory": 0.77}, *UPDATE_ANSWER_KEYS) == 0.77  # case-insensitive


def test_probability_coercion_branches():
    from system_one_dwarf.agent.narrator import _as_probability

    assert [_as_probability(value) for value in (True, "true", "yes", "1", "no", "0")] == [
        None,
        1.0,
        1.0,
        1.0,
        0.0,
        0.0,
    ]
    assert [_as_probability(value) for value in ("banana", 2.0, -1, None, object(), [1])] == [
        None,
        None,
        None,
        None,
        None,
        None,
    ]


def test_normalisers_fall_back_on_junk_answers():
    from system_one_dwarf.agent.narrator import _normalise_effort, _normalise_record_type

    assert _normalise_effort(42) == "medium"
    assert _normalise_effort({"effort": "max"}, default="low") == "low"
    assert _normalise_effort("MAX") == "max"
    assert _normalise_record_type(42) == EPISODE
    assert _normalise_record_type({"a": 1}, default=None) is None
    assert _normalise_record_type("FACT") == FACT


def test_condense_keeps_the_head_when_it_alone_blows_the_budget():
    # a single sentence longer than the budget -> keep its first words
    assert condense_to_budget("one two three four five six", 3) == ("four five six", True)
    # several sentences where even the head is over budget -> truncate the head
    assert condense_to_budget("one two three four five. six seven.", 4) == (
        "one two three four",
        True,
    )
    assert condense_to_budget("anything", 0) == ("anything", False)  # 0 = no budget enforced
    assert condense_to_budget("just one word", 4) == ("just one word", False)


def test_prompt_state_without_events_says_so():
    state = build_prompt_state([], "only the chronicle", now=NOW)

    assert "RECENT EVENTS: (none)" in state
    assert "only the chronicle" in state
    assert "(empty)" in build_prompt_state([], "", now=NOW)


def test_narrator_build_prompt_state_uses_the_injected_clock():
    narrator = Narrator(ScriptedLLM(), None, clock=lambda: "1999-01-01T00:00:00.000Z")

    state = narrator.build_prompt_state(["one event"], "tail", record_type=FACT)

    assert "NOW (UTC): 1999-01-01T00:00:00.000Z" in state
    assert "RECORD TO PRODUCE: fact" in state


def test_constructor_rejects_impossible_budgets_and_thresholds():
    with pytest.raises(ValueError):
        Narrator(ScriptedLLM(), None, min_words=300, max_words=100)
    with pytest.raises(ValueError):
        Narrator(ScriptedLLM(), None, max_words=0)
    with pytest.raises(ValueError):
        Narrator(ScriptedLLM(), None, update_threshold=1.5)


def test_record_type_of_falls_back_when_the_answer_is_junk():
    narrator = Narrator(ScriptedLLM(), None, default_record_type=FACT)

    assert narrator.record_type_of({"record_type": 42}) == FACT
    assert narrator.record_type_of({}) == FACT
    assert narrator.effort_of({"effort": 42}) == "medium"


def test_narrate_with_write_false_never_touches_the_memory(memory: FakeMemory):
    transport = FakeTransport(narration_payload("Chronicle line.", "The record text."))
    narrator = make_narrator(transport, memory)

    result = narrator.narrate({"record_type": EPISODE}, EVENTS, "tail", write=False, now=NOW)

    assert result.narrative == "Chronicle line."
    assert result.memory == {}
    assert memory.calls == []


def test_memory_missing_the_record_method_is_reported():
    class _NoRecordWriter:
        def __init__(self):
            self.narrative = ""

        def set_narrative(self, text, **kwargs):
            self.narrative = text
            return {"ok": True}

    target = _NoRecordWriter()
    transport = FakeTransport(narration_payload("Chronicle line.", "The record text."))
    narrator = Narrator(DeepSeekLLM(lambda: FAKE_KEY, transport=transport), target)

    result = narrator.narrate({"record_type": FACT}, EVENTS, "tail", now=NOW)

    assert target.narrative == "Chronicle line."
    assert any("memory has no add_fact()" in error for error in result.errors)


def test_set_narrative_failure_is_reported_not_raised():
    memory = FakeMemory("set_narrative")
    transport = FakeTransport(narration_payload("Chronicle line.", "The record text."))
    narrator = make_narrator(transport, memory)

    result = narrator.narrate({"record_type": EPISODE}, EVENTS, "tail", now=NOW)

    assert result.memory.get("narrative") is None
    assert any("set_narrative failed" in error for error in result.errors)
    assert memory.contents("add_episode") == [f"{NOW} — The record text."]  # record still lands


def test_memory_without_set_narrative_uses_the_add_entry_bridge():
    class _AddEntryOnly:
        def __init__(self):
            self.calls = []

        def add_entry(self, kind, content, **kwargs):
            self.calls.append({"kind": kind, "content": content, "kwargs": kwargs})
            return {"ok": True}

        def add_episode(self, content, **kwargs):
            return {"ok": True}

    target = _AddEntryOnly()
    transport = FakeTransport(narration_payload("Chronicle line.", "The record text."))
    narrator = Narrator(DeepSeekLLM(lambda: FAKE_KEY, transport=transport), target)

    result = narrator.narrate({"record_type": EPISODE}, EVENTS, "tail", now=NOW)

    assert target.calls == [
        {
            "kind": "semantic",
            "content": "Chronicle line.",
            "kwargs": {"key": "narrative", "tags": "narrative", "origin": "agent"},
        }
    ]
    assert result.memory["narrative"] == {"ok": True}
    assert result.errors == ()


def test_memory_with_neither_narrative_writer_is_reported():
    class _NoNarrativeWriter:
        def add_episode(self, content, **kwargs):
            return {"ok": True}

    transport = FakeTransport(narration_payload("Chronicle line.", "The record text."))
    narrator = Narrator(DeepSeekLLM(lambda: FAKE_KEY, transport=transport), _NoNarrativeWriter())

    result = narrator.narrate({"record_type": EPISODE}, EVENTS, "tail", now=NOW)

    assert any("neither set_narrative() nor add_entry()" in error for error in result.errors)
    assert "narrative" not in result.memory


def test_narration_result_properties_pass_through_the_draft():
    transport = FakeTransport(narration_payload("Chronicle line.", "The record text."))
    narrator = make_narrator(transport, FakeMemory())

    result = narrator.narrate({"effort": "max", "record_type": EPISODE}, EVENTS, "tail", now=NOW)

    assert result.reasoning == "effort:max"
    assert result.model == "deepseek/deepseek-v4.1-flash"
    assert result.latency_ms >= 0
    assert result.word_count == 2


# ------------------------------------------------------- output protocol edges


def test_parse_narration_empty_answer():
    assert parse_narration("") == ("", "", ("empty model answer",))
    assert parse_narration("   \n ") == ("", "", ("empty model answer",))


def test_parse_narration_json_object_without_the_protocol_fields():
    narrative, record, issues = parse_narration('{"summary": "something else"}')

    assert (narrative, record) == ("", "")
    assert issues and "neither narrative nor record" in issues[0]


def test_parse_narration_labelled_sections_keep_multiline_bodies():
    narrative, record, issues = parse_narration(
        "NARRATIVE: first line\nsecond line\nRECORD: the record\nmore record"
    )

    assert narrative == "first line second line"
    assert record == "the record more record"
    assert issues and "labelled" in issues[0]


def test_parse_narration_with_fence_around_prose_still_reads_the_prose():
    narrative, record, _issues = parse_narration("```\nA plain chronicle line.\n```")

    assert narrative == "A plain chronicle line."
    assert record == ""


# -- defect regression: a truncated protocol answer must never be stored ------ #


def test_truncated_json_reply_is_never_stored_as_the_rolling_narrative(memory: FakeMemory):
    transport = FakeTransport(chat_payload('{"narrative": "O aventureiro'))
    narrator = make_narrator(transport, memory)

    result = narrator.narrate({"effort": "max", "record_type": EPISODE}, EVENTS, "tail", now=NOW)

    assert result.narrative == ""
    assert result.record == ""
    assert memory.calls == []  # nothing reached the memory
    assert memory.narrative == ""
    assert any("did not parse" in error for error in result.errors)
    # the model call still happened and was paid for: the failure is visible, not silent
    assert len(transport.calls) == 1
    assert result.usage.cost_usd == 0.000023


def test_truncated_json_reply_does_not_overwrite_an_existing_chronicle(memory: FakeMemory):
    memory.narrative = "The chronicle so far."
    transport = FakeTransport(chat_payload('{"narrative": "O aventureiro'))
    narrator = make_narrator(transport, memory)

    result = narrator.consider({"update": 0.95, "effort": "high", "record_type": EPISODE}, EVENTS,
                               memory.narrative, now=NOW)

    assert memory.narrative == "The chronicle so far."  # untouched
    assert memory.methods() == []
    assert result.errors


@pytest.mark.parametrize(
    "broken",
    [
        '{"narrative": "O aventureiro',
        '{"narrative": "x", "record": ',
        '{"narrative": "x",,}',
        '[{"narrative": "x"',
        'narrative": "x", "record": "y"',
    ],
)
def test_parse_narration_refuses_broken_protocol_answers(broken: str):
    narrative, record, issues = parse_narration(broken)

    assert (narrative, record) == ("", "")
    assert issues and "did not parse" in issues[0]


def test_looks_like_json_does_not_flag_plain_prose():
    from system_one_dwarf.agent.narrator import _looks_like_json

    assert _looks_like_json('{"narrative": "x') is True
    assert _looks_like_json("The goblin attacked the {caravan} at dawn.") is False
    assert _looks_like_json("NARRATIVE: the line\nRECORD: the record") is False


def test_read_jev_answer_with_an_explicit_none_value():
    from system_one_dwarf.agent.narrator import _unwrap

    assert _unwrap(None) is None
    assert read_jev_answer({"update_memory": None}, *UPDATE_ANSWER_KEYS, default=0.42) is None


def test_unknown_record_type_string_falls_back():
    from system_one_dwarf.agent.narrator import _normalise_record_type

    assert _normalise_record_type("episodic") == EPISODE
    assert _normalise_record_type("skipped", default=None) is None
    assert _normalise_record_type("procedure") == PROCEDURE


def test_add_entry_bridge_failure_is_reported():
    class _ExplodingAddEntry:
        def add_entry(self, *args, **kwargs):
            raise RuntimeError("bridge is broken")

        def add_episode(self, content, **kwargs):
            return {"ok": True}

    transport = FakeTransport(narration_payload("Chronicle line.", "The record text."))
    narrator = Narrator(DeepSeekLLM(lambda: FAKE_KEY, transport=transport), _ExplodingAddEntry())

    result = narrator.narrate({"record_type": EPISODE}, EVENTS, "tail", now=NOW)

    assert "narrative" not in result.memory
    assert any("add_entry(narrative) failed" in error for error in result.errors)
