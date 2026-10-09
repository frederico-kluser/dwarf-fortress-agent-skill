"""Glossary: loading, tolerance, deterministic substitution, coverage fast path."""

from __future__ import annotations

from pathlib import Path

import pytest

from system_one_dwarf.translate import (
    DEFAULT_GLOSSARY_RELPATH,
    Glossary,
    GlossaryEntry,
    default_glossary_path,
)


# --------------------------------------------------------------------- loading


def test_project_glossary_is_found_and_loaded(glossary: Glossary) -> None:
    path = default_glossary_path()
    assert path is not None
    assert path.as_posix().endswith(DEFAULT_GLOSSARY_RELPATH.as_posix())
    assert len(glossary) >= 250
    assert "aço" in glossary.pt_terms
    assert "steel" in glossary.en_terms


def test_loading_never_modifies_the_shared_file(glossary: Glossary) -> None:
    path = default_glossary_path()
    assert path is not None
    before = (path.stat().st_size, path.stat().st_mtime_ns)

    loaded = Glossary.load()
    loaded.to_en("aço e ferro")
    loaded.to_pt("steel and iron")

    after = (path.stat().st_size, path.stat().st_mtime_ns)
    assert before == after


def test_missing_file_is_tolerated(tmp_path: Path) -> None:
    missing = tmp_path / "nope.tsv"

    assert len(Glossary.load(missing)) == 0
    assert not Glossary.load(missing)
    assert Glossary.load(missing).to_en("aço") == "aço"

    with pytest.raises(FileNotFoundError):
        Glossary.load(missing, required=True)


def test_malformed_lines_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "glossary.tsv"
    path.write_text(
        "# comment\n"
        "\n"
        "no-tab-here\n"
        "\tsteel\n"
        "aco\t\n"
        "aço\tsteel\n"
        "ferro\tiron iron-alloy\n",
        encoding="utf-8",
    )

    loaded = Glossary.load(path)

    assert loaded.entries == (
        GlossaryEntry("aço", ("steel",)),
        GlossaryEntry("ferro", ("iron", "iron-alloy")),
    )
    assert loaded.to_en("aço") == "steel"
    assert loaded.to_pt("iron-alloy") == "ferro"


def test_empty_glossary_is_identity() -> None:
    empty = Glossary.empty()

    assert len(empty) == 0
    assert empty.to_pt("Steel") == "Steel"
    assert empty.to_en("aço") == "aço"
    assert empty.translate_complete("Steel", "en->pt") is None


# --------------------------------------------------------------- substitution


def test_pt_to_en_replaces_game_terms(glossary: Glossary) -> None:
    result = glossary.to_en("Preciso de aço e ferro para a forja.")

    assert result == "Preciso de steel e iron para a forge."
    assert glossary.matched_terms("aço e ferro", "pt->en") == ["aço", "ferro"]


def test_en_to_pt_replaces_game_terms(glossary: Glossary) -> None:
    result = glossary.to_pt("The dwarf mined adamantine ore.")

    assert result == "The anão mined adamantina minério."


def test_accented_portuguese_variant_is_preferred(glossary: Glossary) -> None:
    assert glossary.to_pt("steel") == "aço"  # not the ASCII twin "aco"
    assert glossary.to_pt("dwarf") == "anão"
    assert glossary.to_pt("ore") == "minério"


def test_case_of_a_leading_capital_is_preserved(glossary: Glossary) -> None:
    assert glossary.to_pt("Steel") == "Aço"
    assert glossary.to_en("Aço") == "Steel"


def test_substitution_is_idempotent(glossary: Glossary) -> None:
    once = glossary.to_pt("Steel armor and iron ore")
    twice = glossary.to_pt(once)

    assert once == twice


def test_word_boundaries_prevent_partial_matches() -> None:
    loaded = Glossary.from_text("aço\tsteel\nferro\tiron\n")

    assert loaded.to_en("açougue") == "açougue"
    assert loaded.to_en("aço") == "steel"
    assert loaded.to_en("ferroada") == "ferroada"
    assert loaded.to_en("(aço)") == "(steel)"


def test_longest_term_wins() -> None:
    loaded = Glossary.from_text("ferro\tiron\nferro fundido\tpigiron\n")

    assert loaded.to_en("ferro fundido") == "pigiron"
    assert loaded.to_en("ferro") == "iron"


def test_unknown_direction_is_rejected(glossary: Glossary) -> None:
    with pytest.raises(ValueError):
        glossary.translate("Steel", "en->es")


# ------------------------------------------------------------- coverage fast path


def test_translate_complete_covers_short_jargon_text(glossary: Glossary) -> None:
    assert glossary.translate_complete("Steel", "en->pt") == "Aço"
    assert glossary.translate_complete("Steel axe", "en->pt") == "Aço machado"
    assert glossary.translate_complete("aço ferro", "pt->en") == "steel iron"
    assert glossary.translate_complete("", "en->pt") == ""


def test_translate_complete_rejects_unknown_words(glossary: Glossary) -> None:
    assert glossary.translate_complete("Steel mystery", "en->pt") is None
    assert glossary.translate_complete("The dwarf", "en->pt") is None
    assert glossary.translate_complete("12345", "en->pt") is None


def test_translate_complete_respects_the_token_budget(glossary: Glossary) -> None:
    assert glossary.translate_complete("Steel axe iron ore", "en->pt") is None
    assert (
        glossary.translate_complete("Steel axe iron ore", "en->pt", max_tokens=4)
        == "Aço machado ferro minério"
    )


# -------------------------------------------------------------------- signature


def test_signature_is_stable_and_content_sensitive() -> None:
    first = Glossary.from_text("aço\tsteel\n")
    same = Glossary.from_text("aço\tsteel\n")
    different = Glossary.from_text("aço\tsteel\nferro\tiron\n")

    assert first.signature == same.signature
    assert first.signature != different.signature
    assert len(first.signature) == 16


def test_mapping_view_is_a_copy(glossary: Glossary) -> None:
    view = glossary.mapping("pt->en")
    view["aço"] = "tampered"

    assert glossary.to_en("aço") == "steel"
