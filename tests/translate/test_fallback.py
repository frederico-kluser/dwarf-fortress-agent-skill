"""Identity fallback, glossary→LLM→identity chain, and translator factory."""

from __future__ import annotations

import pytest

from system_one_dwarf.translate import (
    BatchTranslator,
    ChainTranslator,
    DeepSeekTranslator,
    Glossary,
    IdentityTranslator,
    ScreenTranslation,
    Translation,
    TranslationQuotaError,
    Translator,
    TranslationCache,
    Usage,
    build_translator,
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


class ExplodingTranslator:
    """Primary that always fails, to exercise the fallback path."""

    model = "exploding"

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error or RuntimeError("primary exploded")
        self.calls = 0

    def to_pt(self, text_en: str) -> Translation:
        self.calls += 1
        raise self.error

    def to_en(self, text_pt: str) -> Translation:
        self.calls += 1
        raise self.error

    def translate_screen(
        self,
        screen_text: str,
        previous_translation: ScreenTranslation | str | None = None,
    ) -> ScreenTranslation:
        self.calls += 1
        raise self.error


class SingleOnlyTranslator:
    """Primary without ``to_pt_batch``/``translate_screen`` (minimal protocol)."""

    model = "single-only"

    def __init__(self) -> None:
        self.calls = 0

    def to_pt(self, text_en: str) -> Translation:
        self.calls += 1
        return Translation(text=f"<{text_en}>", source_lang="en", target_lang="pt", model=self.model)

    def to_en(self, text_pt: str) -> Translation:
        self.calls += 1
        return Translation(text=f"<{text_pt}>", source_lang="pt", target_lang="en", model=self.model)


# ------------------------------------------------------------------- identity


def test_identity_returns_input_unchanged_at_zero_cost() -> None:
    identity = IdentityTranslator()

    result = identity.to_pt("Hello, adventurer.")

    assert result.text == "Hello, adventurer."
    assert result.source_lang == "en"
    assert result.target_lang == "pt"
    assert result.model == "identity"
    assert result.via == "identity"
    assert result.usage == Usage()
    assert result.cost_usd == 0.0
    assert result.latency_ms == 0
    assert result.cached is False
    assert identity.is_configured() is False


def test_identity_to_en_translate_and_batch() -> None:
    identity = IdentityTranslator()

    assert identity.to_en("Olá").text == "Olá"
    assert identity.translate("Olá", "pt->en").target_lang == "en"
    assert [item.text for item in identity.to_pt_batch(["a", "b"])] == ["a", "b"]
    assert all(item.usage == Usage() for item in identity.to_pt_batch(["a", "b"]))
    with pytest.raises(ValueError):
        identity.translate("a", "en->es")


def test_identity_screen_is_the_source_screen() -> None:
    identity = IdentityTranslator()

    screen = identity.translate_screen("A\nB", previous_translation="ignored")

    assert screen.text_pt == "A\nB"
    assert screen.lines_pt == ("A", "B")
    assert screen.usage == Usage()
    assert screen.llm_calls == 0
    assert screen.skipped_lines == 2
    assert screen.translated_pt == "A\nB"


def test_identity_satisfies_the_translator_protocols() -> None:
    identity = IdentityTranslator()

    assert isinstance(identity, Translator)
    assert isinstance(identity, BatchTranslator)
    identity.close()
    with identity as entered:
        assert entered is identity


# ---------------------------------------------------------------------- chain


def test_chain_pre_substitutes_glossary_for_user_input() -> None:
    transport = FakeTransport(reply=prefixed_reply())
    chain = ChainTranslator(make_translator(transport), glossary=Glossary.load())

    result = chain.to_en("Preciso de aço e ferro")

    assert transport.user_contents() == ["Preciso de steel e iron"]  # PT→EN before the call
    assert result.text == "[en] Preciso de steel e iron"
    assert result.via == "llm"
    assert chain.error_count == 0


def test_chain_post_substitutes_glossary_for_screens() -> None:
    transport = FakeTransport([chat_response("O anão tem steel e iron.")])
    chain = ChainTranslator(make_translator(transport), glossary=Glossary.load())

    result = chain.to_pt("The dwarf has steel and iron.")

    assert result.text == "O anão tem aço e ferro."  # EN→PT after the call
    assert result.via == "llm"


def test_chain_fast_path_translates_jargon_without_any_call() -> None:
    transport = FakeTransport()
    chain = ChainTranslator(make_translator(transport), glossary=Glossary.load())

    english = chain.to_pt("Steel axe")
    portuguese = chain.to_en("aço")

    assert transport.count == 0
    assert english.text == "Aço machado"
    assert english.via == "glossary"
    assert english.model == "glossary"
    assert english.usage == Usage()
    assert portuguese.text == "steel"
    assert chain.glossary_hits == 2


def test_chain_fast_path_can_be_disabled() -> None:
    transport = FakeTransport([chat_response("Aço machado")])
    chain = ChainTranslator(make_translator(transport), glossary=Glossary.load(), fast_path=False)

    result = chain.to_pt("Steel axe")

    assert transport.count == 1
    assert result.via == "llm"
    assert chain.glossary_hits == 0


def test_chain_falls_back_to_identity_on_terminal_api_error() -> None:
    transport = FakeTransport([error_response(402, "no credit")])
    chain = ChainTranslator(make_translator(transport, max_attempts=1), glossary=Glossary.load())

    result = chain.to_en("Preciso de aço")

    assert isinstance(chain.last_error, TranslationQuotaError)
    assert chain.error_count == 1
    assert result.via == "identity"
    assert result.model == "identity"
    assert result.usage == Usage()
    assert result.cost_usd == 0.0
    assert result.text == "Preciso de steel"  # glossary still applied


def test_chain_falls_back_on_unexpected_primary_error() -> None:
    chain = ChainTranslator(ExplodingTranslator(), glossary=Glossary.load())

    result = chain.to_pt("The dwarf has steel.")

    assert chain.error_count == 1
    assert isinstance(chain.last_error, RuntimeError)
    assert result.text == "The anão has aço."
    assert result.via == "identity"


def test_chain_can_re_raise_when_asked() -> None:
    boom = ValueError("nope")
    chain = ChainTranslator(ExplodingTranslator(boom), raise_errors=True)

    with pytest.raises(ValueError):
        chain.to_pt("hello")

    assert chain.last_error is boom


def test_chain_batch_uses_the_primary_batch_and_normalises_terms() -> None:
    transport = FakeTransport(
        [chat_response(lines_payload(["O anão tem steel.", "Ferro minério."]))]
    )
    chain = ChainTranslator(make_translator(transport), glossary=Glossary.load())

    results = chain.to_pt_batch(["The dwarf has steel.", "Iron ore."])

    assert transport.count == 1
    assert [item.text for item in results] == ["O anão tem aço.", "Ferro minério."]


def test_chain_batch_degrades_to_identity_on_error() -> None:
    transport = FakeTransport([error_response(402, "no credit")])
    chain = ChainTranslator(make_translator(transport, max_attempts=1), glossary=Glossary.load())

    results = chain.to_pt_batch(["The dwarf has steel.", "Iron ore."])

    assert chain.error_count == 1
    assert [item.via for item in results] == ["identity", "identity"]
    assert [item.text for item in results] == ["The anão has aço.", "Ferro minério."]
    assert all(item.usage == Usage() for item in results)


def test_chain_batch_without_a_primary_batch_loops_per_line() -> None:
    primary = SingleOnlyTranslator()
    chain = ChainTranslator(primary, glossary=None)

    results = chain.to_pt_batch(["a", "b"])

    assert primary.calls == 2
    assert [item.text for item in results] == ["<a>", "<b>"]


def test_chain_screen_delegates_and_normalises_terminology() -> None:
    transport = FakeTransport(reply=prefixed_reply())
    chain = ChainTranslator(make_translator(transport), glossary=Glossary.load())

    screen = chain.translate_screen("The dwarf has steel.\n\n42")

    assert transport.count == 1
    assert screen.lines_pt == ("[pt] The anão has aço.", "", "42")
    assert screen.skipped_lines == 2
    assert isinstance(screen, ScreenTranslation)


def test_chain_screen_falls_back_to_identity_on_error() -> None:
    transport = FakeTransport([error_response(500, "down")])
    chain = ChainTranslator(make_translator(transport, max_attempts=1), glossary=Glossary.load())

    screen = chain.translate_screen("The dwarf has steel.")

    assert chain.error_count == 1
    assert screen.lines_pt == ("The anão has aço.",)
    assert screen.usage == Usage()
    assert screen.llm_calls == 0


def test_chain_screen_without_a_primary_screen_method() -> None:
    primary = SingleOnlyTranslator()
    chain = ChainTranslator(primary, glossary=None)

    screen = chain.translate_screen("a\nb", previous_translation=None)

    assert screen.lines_pt == ("<a>", "<b>")
    assert screen.source_lines == ("a", "b")


def test_chain_cache_short_circuits_the_glossary_and_the_llm() -> None:
    transport = FakeTransport([chat_response("O anão tem steel.")])
    cache = TranslationCache()
    chain = ChainTranslator(make_translator(transport), glossary=Glossary.load(), cache=cache)

    first = chain.to_pt("The dwarf has steel.")
    second = chain.to_pt("The dwarf has steel.")

    assert transport.count == 1
    assert first.text == "O anão tem aço."
    assert second.via == "cache"
    assert second.text == "O anão tem aço."
    assert second.cost_usd == 0.0


def test_chain_reports_configuration_and_closes_both_sides() -> None:
    transport = FakeTransport()
    chain = ChainTranslator(make_translator(transport))

    assert chain.is_configured() is True
    chain.close()
    assert transport.closed is True

    unconfigured = ChainTranslator(DeepSeekTranslator(lambda: "", transport=FakeTransport()))
    assert unconfigured.is_configured() is False


def test_chain_satisfies_the_protocols() -> None:
    chain = ChainTranslator(IdentityTranslator())

    assert isinstance(chain, Translator)
    assert isinstance(chain, BatchTranslator)


# -------------------------------------------------------------------- factory


def test_build_translator_without_a_usable_key_is_identity() -> None:
    def broken() -> str:
        raise RuntimeError("key store offline")

    assert isinstance(build_translator(None), IdentityTranslator)
    assert isinstance(build_translator(lambda: ""), IdentityTranslator)
    assert isinstance(build_translator(lambda: "   "), IdentityTranslator)
    assert isinstance(build_translator(broken), IdentityTranslator)


def test_build_translator_with_a_key_is_a_chain_over_deepseek() -> None:
    transport = FakeTransport(reply=prefixed_reply())

    translator = build_translator(lambda: FAKE_KEY, transport=transport)

    assert isinstance(translator, ChainTranslator)
    assert isinstance(translator.primary, DeepSeekTranslator)
    assert translator.is_configured() is True
    assert translator.to_pt("Hello").text == "[pt] Hello"


def test_build_translator_screen_payload_matches_the_api_field() -> None:
    transport = FakeTransport(reply=prefixed_reply())
    translator = build_translator(lambda: FAKE_KEY, transport=transport, glossary=Glossary.load())

    screen = translator.translate_screen("You are standing in a clearing.\n\n42")

    assert screen.as_dict()["translated_pt"] == "[pt] You are standing in a clearing.\n\n42"
    assert screen.display_pt == screen.translated_pt
