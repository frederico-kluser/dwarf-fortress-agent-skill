"""``agent.questions``: JEV-D decision, pt-BR output, contract-shaped queue entry.

Offline: the formatter talks to a ``ScriptedLLM`` (or a ``DeepSeekLLM`` over
``FakeTransport``) and never to the network.  The assertions pin the user
requirements: Portuguese question text, the option count of each format, and no
internal number ever reaching the player.
"""

from __future__ import annotations

import json
import re

import pytest

from system_one_dwarf.agent import (
    BINARY,
    MULTIPLE,
    OPEN,
    DeepSeekLLM,
    HumanQuestion,
    LLMAuthError,
    NoQuestion,
    QuestionFormatter,
    QuestionOption,
    parse_question,
    queue_entry,
    question_cost,
    strip_number_leaks,
)
from system_one_dwarf.agent.questions import ASK_ANSWER_KEYS, FORMAT_ANSWER_KEYS

from .conftest import FAKE_KEY, FakeTransport, ScriptedLLM, question_payload

NOW = "2026-10-09T00:00:00.000Z"
CONTEXT = "The adventurer stands on the river bank; the current looks strong and night is coming."
CANDIDATES = ["swim across the river", "follow the bank east", "build a raft", "wait for daylight"]


def answer_payload(question: str, options: list[str], why: str = "") -> str:
    return json.dumps({"question": question, "options": options, "why": why})


def make_formatter(llm, **kwargs) -> QuestionFormatter:
    return QuestionFormatter(llm, **kwargs)


# ---------------------------------------------------------------- NoQuestion


def test_no_question_when_jev_d_says_no_and_confidence_is_auto():
    llm = ScriptedLLM()  # any call would explode
    formatter = make_formatter(llm)

    result = formatter.format(
        {"ask": 0.12, "format": BINARY}, CONTEXT, 0.95, CANDIDATES, now=NOW
    )

    assert isinstance(result, NoQuestion)
    assert result.reason == "auto_band_no_doubt"
    assert result.band == "auto"
    assert result.confidence == 0.95
    assert result.cost_usd == 0.0
    assert llm.call_count == 0
    assert queue_entry(result) is None
    assert json.loads(json.dumps(result.as_dict()))["asked"] is False


def test_asks_when_confidence_is_below_the_auto_band_even_if_jev_d_says_no():
    llm = ScriptedLLM(answer_payload("Atravessar o rio a nado ou seguir a margem?", ["Nadar", "Seguir a margem"]))
    formatter = make_formatter(llm)

    result = formatter.format({"ask": 0.1, "format": BINARY}, CONTEXT, 0.72, CANDIDATES, now=NOW)

    assert isinstance(result, HumanQuestion)
    assert result.source_data["reason"] == "confidence_below_auto"
    assert result.source_data["band"] == "hitl"
    assert llm.call_count == 1


def test_zero_and_missing_confidence_are_not_auto():
    llm = ScriptedLLM(answer_payload("Pergunta?", ["A", "B"]), answer_payload("Pergunta?", ["A", "B"]))
    formatter = make_formatter(llm)

    zero = formatter.format({"ask": 0.0, "format": BINARY}, CONTEXT, 0.0, CANDIDATES, now=NOW)
    missing = formatter.format({"ask": 0.0, "format": BINARY}, CONTEXT, None, CANDIDATES, now=NOW)

    assert isinstance(zero, HumanQuestion) and isinstance(missing, HumanQuestion)
    assert zero.source_data["confidence_available"] is False
    assert missing.source_data["confidence_available"] is False


def test_unavailable_ask_answer_asks_anyway():
    llm = ScriptedLLM(answer_payload("O que fazer agora?", ["Avançar", "Recuar"]))
    formatter = make_formatter(llm)

    result = formatter.format({"format": BINARY}, CONTEXT, 0.99, CANDIDATES, now=NOW)

    assert isinstance(result, HumanQuestion)
    assert result.source_data["ask"] is None
    assert result.source_data["reason"] == "ask_unavailable"


def test_explicit_ask_wins_over_the_auto_band():
    llm = ScriptedLLM(answer_payload("Confirma a travessia?", ["Confirmar", "Cancelar"]))
    formatter = make_formatter(llm)

    result = formatter.format({"ask": 0.97, "format": BINARY}, CONTEXT, 0.99, CANDIDATES, now=NOW)

    assert isinstance(result, HumanQuestion)
    assert result.source_data["reason"] == "jev_d_asked"


# ------------------------------------------------------------------- formats


@pytest.mark.parametrize(
    ("fmt", "scripted", "expected_count"),
    [
        (BINARY, answer_payload("Nadar ou contornar?", ["Nadar", "Contornar pela margem"]), 2),
        (
            MULTIPLE,
            answer_payload(
                "Qual caminho seguir?",
                ["Nadar direto", "Seguir a margem leste", "Construir uma jangada", "Esperar o amanhecer"],
            ),
            4,
        ),
        (OPEN, answer_payload("O que o aventureiro deve fazer agora?", []), 0),
    ],
)
def test_option_count_follows_the_jev_d_format(fmt: str, scripted: str, expected_count: int):
    llm = ScriptedLLM(scripted)
    formatter = make_formatter(llm)

    result = formatter.format({"ask": 0.9, "format": fmt}, CONTEXT, 0.6, CANDIDATES, now=NOW)

    assert isinstance(result, HumanQuestion)
    assert result.format == fmt
    assert len(result.options) == expected_count
    assert result.question_pt.strip()
    assert result.free_text_allowed is True
    if expected_count:
        assert [option.id for option in result.options] == [f"opt-{i}" for i in range(1, expected_count + 1)]
        assert all(option.label_pt.strip() for option in result.options)
        assert all(option.text == option.label_pt for option in result.options)


def test_multiple_format_is_padded_to_at_least_three_options():
    llm = ScriptedLLM(answer_payload("Qual caminho?", ["Seguir a margem"]))
    formatter = make_formatter(llm)

    result = formatter.format({"ask": 0.9, "format": MULTIPLE}, CONTEXT, 0.55, CANDIDATES, now=NOW)

    assert isinstance(result, HumanQuestion)
    assert 3 <= len(result.options) <= 4
    labels = [option.label_pt for option in result.options]
    assert len(set(labels)) == len(labels)
    assert labels[0] == "Seguir a margem"


def test_binary_format_is_padded_to_two_options():
    llm = ScriptedLLM(answer_payload("Nadar?", []))
    formatter = make_formatter(llm)

    result = formatter.format({"ask": 0.9, "format": BINARY}, CONTEXT, 0.6, CANDIDATES, now=NOW)

    assert isinstance(result, HumanQuestion)
    assert len(result.options) == 2


def test_binary_format_keeps_only_the_first_two_options():
    llm = ScriptedLLM(answer_payload("Nadar?", ["Nadar", "Contornar", "Esperar", "Voltar"]))
    formatter = make_formatter(llm)

    result = formatter.format({"ask": 0.9, "format": BINARY}, CONTEXT, 0.6, CANDIDATES, now=NOW)

    assert isinstance(result, HumanQuestion)
    assert result.option_labels == ("Nadar", "Contornar")


def test_duplicate_and_empty_options_are_dropped():
    llm = ScriptedLLM(answer_payload("Escolher?", ["Nadar", "nadar", "  ", "Contornar"]))
    formatter = make_formatter(llm)

    result = formatter.format({"ask": 0.9, "format": MULTIPLE}, CONTEXT, 0.6, CANDIDATES, now=NOW)

    assert [option.label_pt for option in result.options[:2]] == ["Nadar", "Contornar"]


def test_format_falls_back_by_candidate_count_when_jev_d_is_unreadable():
    two = make_formatter(ScriptedLLM(answer_payload("P?", ["A", "B"])))
    many = make_formatter(ScriptedLLM(answer_payload("P?", ["A", "B", "C"])))

    assert two.format({"ask": 0.9}, CONTEXT, 0.6, ["only one", "two"], now=NOW).format == BINARY
    assert many.format({"ask": 0.9}, CONTEXT, 0.6, CANDIDATES, now=NOW).format == MULTIPLE


def test_unknown_format_answer_falls_back_to_the_default():
    llm = ScriptedLLM(answer_payload("P?", ["A", "B"]))
    formatter = make_formatter(llm)

    result = formatter.format({"ask": 0.9, "format": "essay"}, CONTEXT, 0.6, ["one", "two"], now=NOW)

    assert isinstance(result, HumanQuestion)
    assert result.format == BINARY


# --------------------------------------------------------- Portuguese & leaks


def test_question_and_options_are_portuguese_and_free_of_internal_numbers():
    scripted = answer_payload(
        "Atravessar o rio a nado (62% de chance de sucesso) ou procurar uma ponte?",
        ["Nadar agora (probabilidade de 0,62)", "Procurar uma ponte a leste"],
        "Há 62% de chance de a travessia dar certo.",
    )
    llm = ScriptedLLM(scripted)
    formatter = make_formatter(llm)

    result = formatter.format({"ask": 0.8, "format": BINARY}, CONTEXT, 0.63, CANDIDATES, now=NOW)

    assert isinstance(result, HumanQuestion)
    assert not re.search(r"\d", result.question_pt), result.question_pt
    for option in result.options:
        assert not re.search(r"\d", option.label_pt), option.label_pt
    assert not re.search(r"\d", result.why_pt), result.why_pt
    assert "%" not in result.as_dict()["question_pt"]
    # the number is data, kept out of the display text but logged with the decision
    assert result.source_data["action_confidence"] == 0.63
    assert "0.63" not in result.question_pt and "0,63" not in result.question_pt


def test_strip_number_leaks_keeps_in_game_numbers():
    cleaned = strip_number_leaks("Leva 2 dias de viagem, mas há 62% de chance de chuva.")
    assert "2 dias" in cleaned
    assert "%" not in cleaned


def test_question_prompt_never_carries_the_confidence_number():
    llm = ScriptedLLM(answer_payload("P?", ["A", "B"]))
    formatter = make_formatter(llm)

    formatter.format({"ask": 0.9, "format": BINARY}, CONTEXT, 0.63, CANDIDATES, now=NOW)

    user = llm.calls[0]["user"]
    assert "0.63" not in user and "63" not in user
    assert "SITUATION" in user and "FORMAT: binary" in user
    assert "swim across the river" in user
    assert "DECISION CLARITY" in user


def test_why_falls_back_to_a_deterministic_portuguese_line():
    llm = ScriptedLLM(answer_payload("O que fazer?", ["Avançar", "Recuar"], why=""))
    formatter = make_formatter(llm)

    result = formatter.format({"ask": 0.9, "format": BINARY}, CONTEXT, 0.2, CANDIDATES, now=NOW)

    assert isinstance(result, HumanQuestion)
    assert result.why_pt
    assert not re.search(r"\d", result.why_pt)
    assert "informação" in result.why_pt  # abstain band wording
    assert result.source_data["band"] == "abstain"


def test_near_tie_probabilities_shape_the_fallback_why():
    llm = ScriptedLLM(answer_payload("O que fazer?", ["Avançar", "Recuar"], why=""))
    formatter = make_formatter(llm)

    result = formatter.format(
        {"ask": 0.9, "format": BINARY, "probabilities": {"binary": 0.52, "open": 0.48}},
        CONTEXT,
        0.52,
        CANDIDATES,
        now=NOW,
    )

    assert isinstance(result, HumanQuestion)
    assert "empatadas" in result.why_pt
    assert result.source_data["near_tie"] is True
    assert result.source_data["probabilities"] == {"binary": 0.52, "open": 0.48}


def test_long_question_is_truncated():
    long_question = "O que o aventureiro deve fazer agora " + "considerando tudo o que viu " * 20 + "?"
    llm = ScriptedLLM(answer_payload(long_question, ["Avançar", "Recuar"]))
    formatter = make_formatter(llm, max_question_chars=120)

    result = formatter.format({"ask": 0.9, "format": BINARY}, CONTEXT, 0.6, CANDIDATES, now=NOW)

    assert isinstance(result, HumanQuestion)
    assert len(result.question_pt) <= 120
    assert result.question_pt.endswith("…")


def test_bare_text_answer_still_produces_a_question():
    llm = ScriptedLLM("Atravessar o rio agora ou esperar?")
    formatter = make_formatter(llm)

    result = formatter.format({"ask": 0.9, "format": OPEN}, CONTEXT, 0.6, CANDIDATES, now=NOW)

    assert isinstance(result, HumanQuestion)
    assert result.question_pt == "Atravessar o rio agora ou esperar?"
    assert result.options == ()


def test_empty_answer_raises_instead_of_showing_an_empty_question():
    llm = ScriptedLLM(json.dumps({"question": "", "options": []}))
    formatter = make_formatter(llm)

    with pytest.raises(ValueError):
        formatter.format({"ask": 0.9, "format": BINARY}, CONTEXT, 0.6, CANDIDATES, now=NOW)


def test_llm_error_propagates_to_the_loop():
    llm = ScriptedLLM(LLMAuthError("no key"))
    formatter = make_formatter(llm)

    with pytest.raises(LLMAuthError):
        formatter.format({"ask": 0.9, "format": BINARY}, CONTEXT, 0.6, CANDIDATES, now=NOW)


# -------------------------------------------------------------- API contract


def test_queue_entry_matches_the_api_contract_and_the_runtime_store():
    from system_one_dwarf.app.runtime import CampaignRuntime
    from system_one_dwarf.app.schemas import QuestionOut

    llm = ScriptedLLM(
        answer_payload("Atravessar o rio a nadar ou procurar uma ponte?", ["Nadar", "Procurar uma ponte"])
    )
    formatter = make_formatter(llm)
    question = formatter.format({"ask": 0.9, "format": BINARY}, CONTEXT, 0.6, CANDIDATES, now=NOW)
    assert isinstance(question, HumanQuestion)

    entry = question.to_queue_entry()
    assert set(entry) <= {"question", "options", "free_text_allowed", "ts"}
    assert entry["question"] == question.question_pt
    assert entry["options"] == [
        {"id": "opt-1", "text": "Nadar"},
        {"id": "opt-2", "text": "Procurar uma ponte"},
    ]
    assert entry["free_text_allowed"] is True

    stored = CampaignRuntime("c-1").add_question(entry)
    by_id = {option["id"]: option["text"] for option in stored["options"]}
    assert by_id == {"opt-1": "Nadar", "opt-2": "Procurar uma ponte"}
    assert stored["status"] == "pending"
    assert stored["free_text_allowed"] is True

    model = QuestionOut(**stored)  # the contract model accepts the entry verbatim
    assert model.question == question.question_pt
    assert [option.id for option in model.options] == ["opt-1", "opt-2"]


def test_human_question_serialisation_and_aliases():
    question = HumanQuestion(
        question_pt="Nadar ou contornar?",
        options=(QuestionOption("opt-1", "Nadar"),),
        format=BINARY,
        why_pt="As alternativas estão próximas.",
        source_data={"band": "hitl"},
        ts=NOW,
    )
    payload = question.as_dict()

    assert payload["question"] == payload["question_pt"] == "Nadar ou contornar?"
    assert payload["options"][0] == {"id": "opt-1", "label_pt": "Nadar", "text": "Nadar"}
    assert question.option_labels == ("Nadar",)
    assert json.loads(json.dumps(payload))["source_data"] == {"band": "hitl"}


# ------------------------------------------------------------------- wiring


def test_formatter_uses_the_cheapest_reasoning_mode():
    transport = FakeTransport(question_payload("Pergunta curta?", ["Sim", "Não"], "Dúvida real."))
    llm = DeepSeekLLM(lambda: FAKE_KEY, transport=transport)
    formatter = QuestionFormatter(llm)

    question = formatter.format({"ask": 0.9, "format": BINARY}, CONTEXT, 0.6, CANDIDATES, now=NOW)

    assert isinstance(question, HumanQuestion)
    assert transport.last_body["reasoning"] == {"enabled": False}
    assert transport.last_body["max_tokens"] == 500
    assert question.source_data["reasoning"] == "off"
    assert question_cost(question) == 0.000023
    assert formatter.calls == 1


def test_formatter_accepts_a_real_decision_result():
    from system_one_dwarf.jev.client import Answer, DecisionResult
    from system_one_dwarf.jev.client import Usage as JevUsage

    result = DecisionResult(
        answers={
            "ask_human": Answer(
                name="ask_human",
                type="noul",
                value=0.88,
                confidence=None,
                probabilities=None,
                legend=None,
                band="auto",
                chosen_probability=None,
            ),
            "question_format": Answer(
                name="question_format",
                type="choice",
                value=MULTIPLE,
                confidence=0.7,
                probabilities={MULTIPLE: 0.7, BINARY: 0.3},
                legend=None,
                band="hitl",
                chosen_probability=0.7,
            ),
        },
        usage=JevUsage(),
        latency_ms=9,
        model="typesafe/jev-1.13",
        raw={},
    )
    llm = ScriptedLLM(
        answer_payload("Qual caminho?", ["Nadar direto", "Seguir a margem", "Esperar o amanhecer"])
    )
    formatter = make_formatter(llm)

    question = formatter.format(result, CONTEXT, 0.58, CANDIDATES, now=NOW)

    assert isinstance(question, HumanQuestion)
    assert question.format == MULTIPLE
    assert question.source_data["ask"] is True
    assert question.source_data["near_tie"] is False  # 0.70 vs 0.30 is a clear margin
    assert question.source_data["probabilities"] == {"multiple": 0.7, "binary": 0.3}
    assert len(question.options) == 3


def test_parse_question_handles_fences_and_missing_fields():
    assert parse_question('```json\n{"pergunta": "P?", "opções": ["A"], "motivo": "W"}\n```') == (
        "P?",
        ["A"],
        "W",
    )
    assert parse_question("Só uma pergunta?") == ("Só uma pergunta?", [], "")
    assert parse_question("") == ("", [], "")


def test_system_prompt_states_the_language_and_the_protocol():
    formatter = make_formatter(ScriptedLLM())
    prompt = formatter.system_prompt(MULTIPLE)

    assert "Portuguese" in prompt
    assert "NEVER mention models, probabilities" in prompt
    assert "EXACTLY 2 options" not in prompt
    assert "3 or 4 options" in prompt
    assert '"question"' in prompt and '"options"' in prompt
    assert "NO options" in formatter.system_prompt(OPEN)

# ------------------------------------------------- helper/parsing branch coverage


def test_aliases_and_no_question_flags():
    question = HumanQuestion(question_pt="Pergunta?", ts=NOW)
    assert question.question == "Pergunta?" == question.question_pt
    assert question.options == () and question.option_labels == ()

    no_question = NoQuestion(reason="auto_band_no_doubt", ts=NOW)
    assert no_question.asks is False
    assert no_question.cost_usd == 0.0
    assert no_question.as_dict()["asked"] is False
    assert NoQuestion(reason="x", confidence=0.99, band="auto", format=BINARY).as_dict()["format"] == BINARY


def test_as_bool_reads_bools_strings_and_numbers():
    from system_one_dwarf.agent.questions import _as_bool

    assert [_as_bool(value, threshold=0.5) for value in (True, False, 0.9, 0.1)] == [
        True,
        False,
        True,
        False,
    ]
    assert [_as_bool(value, threshold=0.5) for value in ("true", "yes", "y", "1")] == [True] * 4
    assert [_as_bool(value, threshold=0.5) for value in ("false", "no", "n", "0")] == [False] * 4
    assert _as_bool(None, threshold=0.5) is None
    assert _as_bool("banana", threshold=0.5) is None
    assert _as_bool(object(), threshold=0.5) is None


def test_format_normalisation_falls_back_on_junk():
    from system_one_dwarf.agent.questions import _normalise_format

    assert _normalise_format(42, BINARY) == BINARY
    assert _normalise_format("MULTIPLE", BINARY) == MULTIPLE
    assert _normalise_format("essay", None) is None
    assert _normalise_format(None, BINARY) == BINARY


def test_probabilities_are_read_from_both_answer_shapes():
    from system_one_dwarf.agent.questions import _read_probabilities

    from system_one_dwarf.jev.client import Answer

    answer_object = Answer(
        name="question_format",
        type="choice",
        value=BINARY,
        confidence=0.55,
        probabilities={BINARY: 0.55, OPEN: 0.45},
        legend=None,
        band="hitl",
        chosen_probability=0.55,
    )
    result_like = type("_R", (), {"answers": {"question_format": answer_object}})()

    assert _read_probabilities({"probabilities": {"a": 0.6, "b": "x"}}) == {"a": 0.6}
    assert _read_probabilities(result_like) == {BINARY: 0.55, OPEN: 0.45}
    assert _read_probabilities({"format": BINARY}) == {}
    assert _read_probabilities("nonsense") == {}


def test_constructor_rejects_impossible_configuration():
    with pytest.raises(ValueError):
        QuestionFormatter(ScriptedLLM(), max_question_chars=0)
    with pytest.raises(ValueError):
        QuestionFormatter(ScriptedLLM(), ask_threshold=2.0)


def test_prompt_without_candidate_actions():
    llm = ScriptedLLM(answer_payload("P?", ["A", "B"]))
    formatter = make_formatter(llm)

    formatter.format({"ask": 0.9, "format": BINARY}, CONTEXT, 0.6, [], now=NOW)

    assert "ACTIONS THE AGENT WAS WEIGHING: (none listed)" in llm.calls[0]["user"]


def test_invalid_confidence_is_treated_as_hitl_not_as_auto():
    llm = ScriptedLLM(answer_payload("Seguir em frente?", ["Seguir", "Parar"]))
    formatter = make_formatter(llm)

    question = formatter.format({"ask": 0.1, "format": BINARY}, CONTEXT, float("nan"), CANDIDATES, now=NOW)

    assert isinstance(question, HumanQuestion)
    assert question.source_data["band"] == "hitl"  # a NaN never reaches "auto"
    assert not re.search(r"\d", question.question_pt)


def test_parse_question_falls_back_when_the_json_is_broken():
    assert parse_question('{"question": "P?", "options": ') == ('{"question": "P?", "options":', [], "")
    # a parsable object with no usable string question yields nothing (the
    # formatter then raises instead of showing an empty question)
    assert parse_question('{"question": 42}') == ("", [], "")
    assert parse_question('```json\n{"question": "P?"}\n```') == ("P?", [], "")


def test_queue_entry_and_cost_for_a_no_question():
    assert queue_entry(NoQuestion(reason="auto_band_no_doubt")) is None
    assert question_cost(NoQuestion(reason="auto_band_no_doubt")) == 0.0
    question = HumanQuestion(
        question_pt="P?",
        source_data={"usage": {"cost_usd": 0.000123}},
    )
    assert queue_entry(question)["question"] == "P?"
    assert question_cost(question) == 0.000123


# -- defect regression: no dangling brackets after the leak strip ------------ #


@pytest.mark.parametrize(
    ("leaky", "expected"),
    [
        ("Nadar agora (probabilidade de 0,62)", "Nadar agora"),
        ("Atravessar (62% de chance) ou esperar?", "Atravessar ou esperar?"),
        ("Escolher (  ) entre A e B", "Escolher entre A e B"),
        ("Seguir (confiança 0.9) e esperar", "Seguir e esperar"),
        (
            "Atravessar o rio a nado (62% de chance de sucesso) ou procurar uma ponte?",
            "Atravessar o rio a nado ou procurar uma ponte?",
        ),
        ("Subir (score 3) até ao cume (2 horas)", "Subir até ao cume (2 horas)"),
        ("Nadar (62%)", "Nadar"),
        ("Nadar (62%) e esperar (probabilidade de 0,9)", "Nadar e esperar"),
    ],
)
def test_strip_number_leaks_leaves_no_bracket_debris(leaky: str, expected: str):
    cleaned = strip_number_leaks(leaky)

    assert cleaned == expected
    assert "()" not in cleaned
    assert "( )" not in cleaned
    assert not re.search(r"\(\s*[,.;:!?]*\s*\)", cleaned)
    assert "%" not in cleaned


def test_strip_number_leaks_keeps_legitimate_parentheses_and_game_numbers():
    assert strip_number_leaks("Travessia (perigosa) mas possível") == "Travessia (perigosa) mas possível"
    assert strip_number_leaks("Leva 2 dias de viagem (2 noites)") == "Leva 2 dias de viagem (2 noites)"
    cleaned = strip_number_leaks("Leva 2 dias de viagem, mas há 62% de chance de chuva.")
    assert "2 dias" in cleaned and "%" not in cleaned


def test_question_with_leaky_parenthetical_keeps_clean_portuguese():
    llm = ScriptedLLM(
        answer_payload(
            "Atravessar o rio a nado (62% de chance de sucesso) ou procurar uma ponte?",
            ["Nadar (probabilidade de 0,62)", "Procurar uma ponte"],
            "Há 62% de chance de a travessia dar certo.",
        )
    )
    formatter = make_formatter(llm)

    question = formatter.format({"ask": 0.8, "format": BINARY}, CONTEXT, 0.63, CANDIDATES, now=NOW)

    assert isinstance(question, HumanQuestion)
    assert "(" not in question.question_pt and ")" not in question.question_pt
    assert question.option_labels[0] == "Nadar"
    assert "(" not in question.option_labels[0]
    assert not re.search(r"\d", question.as_dict()["question_pt"])
    for label in question.option_labels:
        assert not re.search(r"\d", label)


def test_probabilities_are_read_when_the_answer_entry_is_a_plain_mapping():
    from system_one_dwarf.agent.questions import _read_probabilities

    result_like = type(
        "_R", (), {"answers": {"question_format": {"probabilities": {BINARY: 0.51, OPEN: 0.49}}}}
    )()

    assert _read_probabilities(result_like) == {BINARY: 0.51, OPEN: 0.49}
    nested = type("_R", (), {"answers": {"question_format": {"choice": BINARY}}})()
    assert _read_probabilities(nested) == {}


def test_parse_question_with_braces_that_are_not_valid_json():
    broken = '{"question": "P?",,}'

    question, options, why = parse_question(broken)

    assert question == broken  # falls back to the cleaned text, never crashes
    assert options == [] and why == ""
