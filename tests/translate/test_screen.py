"""``translate_screen``: real-time discipline (dedup, cache, skips, batching)."""

from __future__ import annotations

import json

import pytest

from system_one_dwarf.translate import (
    ScreenTranslation,
    TranslationAuthError,
    TranslationCache,
    Usage,
)

from dwarf_translate_fakes import (
    FakeTransport,
    chat_response,
    error_response,
    lines_payload,
    make_translator,
    prefixed_reply,
)

SCREEN_A = "\n".join(
    [
        "You are standing in a clearing.",
        "",
        "42",
        "---+---",
    ]
)


# ------------------------------------------------------------------- happy path


def test_screen_translates_translatable_lines_in_one_call() -> None:
    transport = FakeTransport(reply=prefixed_reply())
    translator = make_translator(transport)

    screen = translator.translate_screen(SCREEN_A)

    assert transport.count == 1
    assert screen.lines_pt == ("[pt] You are standing in a clearing.", "", "42", "---+---")
    assert screen.text_pt == "\n".join(screen.lines_pt)
    assert screen.translated_pt == screen.text_pt
    assert screen.display_pt == screen.text_pt
    assert screen.source_lines == tuple(SCREEN_A.split("\n"))
    assert screen.llm_calls == 1
    assert screen.translated_lines == 1
    assert screen.skipped_lines == 3
    assert screen.cached_lines == 0
    assert screen.reused_lines == 0
    assert screen.usage.input_tokens == 1000
    assert screen.usage.output_tokens == 500
    assert screen.latency_ms >= 0
    assert screen.as_dict()["translated_pt"] == screen.text_pt
    assert translator.last_screen is screen


def test_screen_line_count_and_order_are_preserved() -> None:
    source_lines = [f"Event number {index} happens." for index in range(12)]
    transport = FakeTransport(reply=prefixed_reply())
    translator = make_translator(transport)

    screen = translator.translate_screen("\n".join(source_lines))

    assert len(screen.lines_pt) == 12
    assert [line.replace("[pt] ", "") for line in screen.lines_pt] == source_lines


def test_single_line_screen_uses_a_single_call_protocol() -> None:
    transport = FakeTransport(reply=prefixed_reply())
    translator = make_translator(transport)

    screen = translator.translate_screen("The dwarf is here.")

    assert transport.count == 1
    assert transport.user_contents() == ["The dwarf is here."]  # not a JSON array
    assert screen.lines_pt == ("[pt] The dwarf is here.",)


def test_all_static_screen_makes_no_call_at_all() -> None:
    transport = FakeTransport()
    translator = make_translator(transport)

    screen = translator.translate_screen("42\n---\n\n")

    assert transport.count == 0
    assert screen.llm_calls == 0
    assert screen.text_pt == "42\n---\n\n"
    assert screen.skipped_lines == 4
    assert screen.usage == Usage()


# ------------------------------------------------------------------- dedup/cache


def test_second_identical_screen_costs_zero_calls() -> None:
    transport = FakeTransport(reply=prefixed_reply())
    translator = make_translator(transport)

    first = translator.translate_screen(SCREEN_A)
    second = translator.translate_screen(SCREEN_A)

    assert transport.count == 1  # 1 call for two screens
    assert second.llm_calls == 0
    assert second.usage == Usage()
    assert second.reused_lines == 1
    assert second.text_pt == first.text_pt


def test_repeated_line_across_screens_is_served_from_the_shared_cache() -> None:
    transport = FakeTransport(reply=prefixed_reply())
    cache = TranslationCache()
    first = make_translator(transport, cache=cache)
    second = make_translator(transport, cache=cache)

    first.translate_screen("The dwarf is here.")
    screen = second.translate_screen("The dwarf is here.")

    assert transport.count == 1
    assert screen.llm_calls == 0
    assert screen.cached_lines == 1
    assert screen.usage == Usage()
    assert cache.stats()["hits"] == 1


def test_only_the_changed_line_is_sent_in_the_next_screen() -> None:
    transport = FakeTransport(reply=prefixed_reply())
    translator = make_translator(transport, use_cache=False)

    first = translator.translate_screen("Line one.\nLine two.")
    second = translator.translate_screen("Line one.\nLine two changed.", previous_translation=first)

    assert transport.count == 2
    # one line left after reuse -> the cheaper single-line protocol is used
    assert transport.user_contents()[1] == "Line two changed."
    assert second.reused_lines == 1
    assert second.translated_lines == 1
    assert second.lines_pt[0] == "[pt] Line one."
    assert second.lines_pt[1] == "[pt] Line two changed."
    assert second.llm_calls == 1


def test_duplicate_lines_within_one_screen_are_translated_once() -> None:
    transport = FakeTransport(reply=prefixed_reply())
    translator = make_translator(transport)

    screen = translator.translate_screen("Dwarf here.\nDwarf here.\nNight falls.\nDwarf here.")

    assert transport.count == 1
    # duplicates are collapsed before the call: two unique lines, one call
    assert json.loads(transport.user_contents()[0]) == ["Dwarf here.", "Night falls."]
    assert screen.lines_pt == ("[pt] Dwarf here.", "[pt] Dwarf here.", "[pt] Night falls.", "[pt] Dwarf here.")


def test_previous_translation_may_be_a_plain_string() -> None:
    transport = FakeTransport(reply=prefixed_reply())
    translator = make_translator(transport, use_cache=False)

    screen = translator.translate_screen("A\nB", previous_translation="[pt] A\n[pt] B")

    assert transport.count == 0
    assert screen.lines_pt == ("[pt] A", "[pt] B")
    assert screen.reused_lines == 2


def test_mismatched_previous_string_is_ignored() -> None:
    transport = FakeTransport(reply=prefixed_reply())
    translator = make_translator(transport, use_cache=False)

    screen = translator.translate_screen("A\nB", previous_translation="only one line")

    assert transport.count == 1
    assert screen.lines_pt == ("[pt] A", "[pt] B")


def test_cache_disabled_forces_a_new_call_for_a_repeated_line() -> None:
    transport = FakeTransport(reply=prefixed_reply())
    translator = make_translator(transport, use_cache=False)

    translator.translate_screen("The dwarf is here.")
    translator.translate_screen("Something else entirely.")  # breaks the reuse window
    screen = translator.translate_screen("The dwarf is here.")

    assert transport.count == 3
    assert screen.cached_lines == 0
    assert screen.reused_lines == 0


# ---------------------------------------------------------------- error handling


def test_screen_translation_raises_when_the_primary_fails() -> None:
    transport = FakeTransport([error_response(401, "bad key")])
    translator = make_translator(transport)

    with pytest.raises(TranslationAuthError):
        translator.translate_screen("The dwarf is here.")


def test_screen_batch_parse_failure_still_produces_the_screen() -> None:
    transport = FakeTransport(
        [
            chat_response("I cannot do that."),
            chat_response("[pt] one"),
            chat_response("[pt] two"),
        ]
    )
    translator = make_translator(transport)

    screen = translator.translate_screen("one\ntwo")

    assert transport.count == 3  # failed batch + two single calls
    assert screen.lines_pt == ("[pt] one", "[pt] two")
    assert screen.llm_calls == 3


def test_screen_usage_is_summed_from_the_lines() -> None:
    transport = FakeTransport(
        [chat_response(lines_payload(["[pt] a", "[pt] b"]), input_tokens=700, output_tokens=300)]
    )
    translator = make_translator(transport)

    screen = translator.translate_screen("a\nb")

    assert screen.usage.input_tokens == 700
    assert screen.usage.output_tokens == 300
    assert screen.usage.cost_usd > 0


def test_empty_screen_is_safe() -> None:
    transport = FakeTransport()
    translator = make_translator(transport)

    screen = translator.translate_screen("")

    assert transport.count == 0
    assert isinstance(screen, ScreenTranslation)
    assert screen.text_pt == ""
