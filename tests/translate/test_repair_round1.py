"""Repair-round regressions: numbered-list strictness, memory seam, static lines.

Three defects the independent verifier found:

1. ``_parse_numbered_lines`` accepted *any* plain-text answer with exactly N
   non-empty lines as if it were a numbered list, so a model apology could be
   shown on the user's screen as a translation with no fallback.
2. ``campaign.memory`` / ``campaign.objective`` need ``to_english`` /
   ``to_portuguese`` (``str -> str``, never raising); without them
   ``safe_translate`` silently kept the untranslated text.
3. The pure-numbers/punctuation guard existed only on the batch/screen path, so
   ``to_pt("12345")`` paid for a chat call.
"""

from __future__ import annotations

import pytest

from system_one_dwarf.campaign import memory  # the other piece's seam (read-only)

from system_one_dwarf.translate import (
    ChainTranslator,
    build_translator,
    DeepSeekTranslator,
    Glossary,
    IdentityTranslator,
    TextTranslator,
    Usage,
    parse_batch_content,
)

from dwarf_translate_fakes import (
    FAKE_KEY,
    FakeTransport,
    chat_response,
    error_response,
    lines_payload,
    make_translator,
    prefixed_reply,
)

APOLOGY = "I cannot help.\nPlease try again.\nSorry."


class ExplodingTranslator:
    """A primary whose every call fails, to prove the seam stays total."""

    model = "exploding"

    def to_pt(self, text_en):  # noqa: ANN001, ANN201 - test double
        raise RuntimeError("primary exploded")

    def to_en(self, text_pt):  # noqa: ANN001, ANN201 - test double
        raise RuntimeError("primary exploded")


# ------------------------------------------- 1. plain text is not a numbered list


def test_plain_prose_with_n_lines_is_rejected_as_a_batch_answer() -> None:
    """Repro from the verifier: an apology must never be read as 3 translations."""
    assert parse_batch_content(APOLOGY, 3) is None
    assert parse_batch_content("one\ntwo\nthree", 3) is None
    assert parse_batch_content("Sure!\nHere you go.\nDone.", 3) is None
    assert parse_batch_content("- one\n- two\n- three", 3) is None  # bullets are prose


def test_real_numbered_lists_are_still_accepted() -> None:
    assert parse_batch_content("1. um\n2. dois\n3. três", 3) == ["um", "dois", "três"]
    assert parse_batch_content("1) um\n2) dois", 2) == ["um", "dois"]
    assert parse_batch_content("1 - um\n2 - dois", 2) == ["um", "dois"]
    assert parse_batch_content("1: um\n2: dois", 2) == ["um", "dois"]


def test_numbered_lists_out_of_order_or_incomplete_are_rejected() -> None:
    assert parse_batch_content("2. dois\n1. um", 2) is None
    assert parse_batch_content("1. um\n3. três", 2) is None
    assert parse_batch_content("1. um\nnot numbered", 2) is None
    assert parse_batch_content("1. \n2. dois", 2) is None


def test_apology_answer_falls_back_to_per_line_calls() -> None:
    """End to end: the apology must not reach the screen; per-line calls do."""
    transport = FakeTransport(
        [
            chat_response(APOLOGY),
            chat_response("[pt] You are standing in a clearing."),
            chat_response("[pt] A deer grazes nearby."),
            chat_response("[pt] The sun sets."),
        ]
    )
    translator = make_translator(transport)

    screen = translator.translate_screen(
        "You are standing in a clearing.\nA deer grazes nearby.\nThe sun sets."
    )

    assert translator.batch_parse_failures == 1
    assert transport.count == 4  # 1 rejected batch + 3 per-line calls
    assert screen.lines_pt == (
        "[pt] You are standing in a clearing.",
        "[pt] A deer grazes nearby.",
        "[pt] The sun sets.",
    )
    assert "cannot help" not in screen.text_pt
    assert screen.translated_lines == 3


def test_to_pt_batch_rejects_the_apology_too() -> None:
    transport = FakeTransport(
        [
            chat_response(APOLOGY),
            chat_response("[pt] a"),
            chat_response("[pt] b"),
            chat_response("[pt] c"),
        ]
    )
    translator = make_translator(transport)

    results = translator.to_pt_batch(["a", "b", "c"])

    assert [item.text for item in results] == ["[pt] a", "[pt] b", "[pt] c"]
    assert translator.batch_parse_failures == 1


# --------------------------------------------------- 2. campaign.memory seam


def _mem_chain(transport: FakeTransport) -> ChainTranslator:
    return ChainTranslator(make_translator(transport), glossary=Glossary.load())


def test_translators_satisfy_the_memory_translator_protocol() -> None:
    identity = IdentityTranslator()
    deepseek = DeepSeekTranslator(lambda: FAKE_KEY, transport=FakeTransport())
    chain = ChainTranslator(deepseek, glossary=Glossary.load())

    for translator in (identity, deepseek, chain):
        assert isinstance(translator, memory.Translator)
        assert isinstance(translator, TextTranslator)
        assert callable(getattr(translator, "to_english", None))
        assert callable(getattr(translator, "to_portuguese", None))


def test_safe_translate_pt_to_en_actually_translates() -> None:
    transport = FakeTransport(reply=prefixed_reply())
    translator = _mem_chain(transport)

    # "Preciso de aço" -> "Preciso de steel" (glossary, PT->EN) -> English model answer
    result = memory.safe_translate(translator, "Preciso de aço para a forja", "en")

    assert result != "Preciso de aço para a forja"
    assert "steel" in result


def test_safe_translate_en_to_pt_actually_translates() -> None:
    transport = FakeTransport(reply=prefixed_reply())
    translator = _mem_chain(transport)

    result = memory.safe_translate(translator, "The dwarf has steel.", "pt")

    assert result != "The dwarf has steel."
    assert result == "[pt] The anão has aço."


def test_seam_unwraps_the_translation_text() -> None:
    transport = FakeTransport(reply=prefixed_reply())
    translator = _mem_chain(transport)

    assert isinstance(translator.to_english("Preciso de ferro"), str)
    assert isinstance(translator.to_portuguese("Iron ore."), str)
    # deterministic glossary fast path: no call at all, still a real translation
    assert translator.to_english("aço") == "steel"
    assert translator.to_portuguese("Steel") == "Aço"


def test_build_translator_output_wires_into_safe_translate_both_ways() -> None:
    """Exactly how the backend will wire it: factory -> MemoryStore seam."""
    transport = FakeTransport(reply=prefixed_reply())
    translator = build_translator(lambda: FAKE_KEY, transport=transport, glossary=Glossary.load())

    assert isinstance(translator, memory.Translator)
    canonical = memory.safe_translate(translator, "Preciso de aço para a forja", "en")
    displayed = memory.safe_translate(translator, "The dwarf has steel.", "pt")

    assert canonical != "Preciso de aço para a forja" and "steel" in canonical
    assert displayed == "[pt] The anão has aço."
    assert transport.count == 2


def test_identity_seam_is_the_identity_in_both_directions() -> None:
    identity = IdentityTranslator()

    assert identity.to_english("Preciso de aço") == "Preciso de aço"
    assert identity.to_portuguese("The dwarf") == "The dwarf"
    assert memory.safe_translate(identity, "Preciso de aço", "en") == "Preciso de aço"


def test_seam_never_raises_when_the_model_fails() -> None:
    transport = FakeTransport([error_response(402, "no credit")])
    translator = ChainTranslator(
        make_translator(transport, max_attempts=1),
        glossary=Glossary.load(),
    )

    assert memory.safe_translate(translator, "Preciso de aço", "en") == "Preciso de steel"
    assert translator.error_count == 1

    exploding = ChainTranslator(ExplodingTranslator())
    assert exploding.to_english("Olá") == "Olá"  # no exception escapes
    assert exploding.to_portuguese("Hello") == "Hello"


def test_deepseek_seam_never_raises_without_a_key() -> None:
    translator = DeepSeekTranslator(lambda: "", transport=FakeTransport())

    assert translator.to_english("Olá") == "Olá"
    assert translator.to_portuguese("Hello") == "Hello"


def test_seam_handles_empty_and_non_string_input() -> None:
    translator = _mem_chain(FakeTransport(reply=prefixed_reply()))

    assert translator.to_english("") == ""
    assert translator.to_portuguese(None) is None  # type: ignore[arg-type]


# --------------------------------------------- 3. static lines on the single path


@pytest.mark.parametrize("static_text", ["12345", "---+---", "   ", "42.5", "±§$%&"])
def test_single_call_skips_static_text(static_text: str) -> None:
    transport = FakeTransport()
    translator = make_translator(transport)

    result = translator.to_pt(static_text)

    assert transport.count == 0
    assert result.text == static_text
    assert result.via == "skipped"
    assert result.usage == Usage()


def test_single_call_skips_static_text_in_both_directions() -> None:
    transport = FakeTransport()
    translator = make_translator(transport)

    assert translator.to_en("42").via == "skipped"
    assert translator.translate("----", "en->pt").via == "skipped"
    assert transport.count == 0


def test_chain_single_call_skips_static_text() -> None:
    transport = FakeTransport()
    chain = ChainTranslator(make_translator(transport), glossary=Glossary.load())

    result = chain.to_pt("12345")

    assert transport.count == 0
    assert result.text == "12345"
    assert result.via == "skipped"
    assert result.usage == Usage()


def test_real_text_still_calls_the_model() -> None:
    transport = FakeTransport([chat_response("Olá")])
    translator = make_translator(transport)

    assert translator.to_pt("Hello").text == "Olá"
    assert transport.count == 1


def test_batch_path_keeps_skipping_static_lines() -> None:
    transport = FakeTransport([chat_response(lines_payload(["[pt] hello", "[pt] world"]))])
    translator = make_translator(transport)

    results = translator.to_pt_batch(["hello", "42", "world"])

    assert transport.count == 1
    assert [item.via for item in results] == ["llm", "skipped", "llm"]
