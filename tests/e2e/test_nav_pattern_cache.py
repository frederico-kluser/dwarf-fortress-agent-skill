"""Cache de padrões de menu (``scripts/e2e/nav_jev.PatternCache``) — Passo 19.

Os padrões de navegação/menu são re-derivados por ecrã (focus + texto → padrões
da biblioteca); o cache guarda a derivação com a impressão digital da biblioteca
e é conservador: qualquer edição da biblioteca invalida as entradas e uma entrada
divergente nunca é servida — zero padrões stale.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NAV_PATH = ROOT / "scripts" / "e2e" / "nav_jev.py"
LIBRARY_PATH = ROOT / "docs" / "padroes-navegacao.json"


def load(name, path=NAV_PATH):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def nav():
    return load("nav_jev_cache_under_test")


@pytest.fixture(scope="module")
def library(nav):
    return nav.load_library(LIBRARY_PATH)


IN_GAME = ("dungeonmode/Default", "Some screen with [a stray mule] and Okay button")
LAUNCH = ("new_region", "Dwarf Fortress title — Create new world")


def test_hit_serves_the_same_ids_and_counts_the_saved_work(nav, library):
    cache = nav.PatternCache()
    first = cache.match_ids(library, *IN_GAME)
    second = cache.match_ids(library, *IN_GAME)

    assert first == second and first  # mesma resposta
    stats = cache.stats()
    assert stats["hits"] == 1 and stats["misses"] == 1


def test_miss_when_the_screen_changes(nav, library):
    cache = nav.PatternCache()
    cache.match_ids(library, *IN_GAME)

    other = cache.match_ids(library, "dungeonmode/Default", "completely different screen")

    assert cache.stats()["misses"] == 2
    assert isinstance(other, tuple)


def test_the_cache_never_changes_the_derived_answer(nav, library):
    """Paridade total: cache e derivação pura veem exatamente os mesmos padrões."""
    cache = nav.PatternCache()
    for focus, text in (IN_GAME, LAUNCH, ("", ""), ("new_region", "Okay")):
        pure = [p["id"] for p in nav.candidates(library, focus, text)]
        cached = [p["id"] for p in cache.candidates_for(library, focus, text)]
        assert cached == pure


def test_a_library_edit_invalidates_every_entry(nav, library):
    cache = nav.PatternCache()
    cache.match_ids(library, *IN_GAME)
    assert cache.stats()["size"] == 1

    edited = copy.deepcopy(library)
    edited["patterns"][0]["text_markers"] = ["totally new marker"]
    assert nav.library_fingerprint(edited) != nav.library_fingerprint(library)

    # Nova biblioteca → chave nova (miss) e a entrada antiga nunca é servida.
    cache.match_ids(edited, *IN_GAME)
    assert cache.stats()["misses"] == 2

    # Invalidação conservadora explícita: drop de tudo o que não é a biblioteca atual.
    dropped = cache.invalidate_library(edited)
    assert dropped == 1 and cache.stats()["size"] == 1


def test_a_corrupt_mirror_entry_is_never_served(nav, library, tmp_path):
    path = tmp_path / "nav-cache.json"
    cache = nav.PatternCache(path=path)
    cache.match_ids(library, *IN_GAME)

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["entries"][0]["fingerprint"] = "outra-biblioteca"
    path.write_text(json.dumps(payload), encoding="utf-8")

    reloaded = nav.PatternCache(path=path)
    assert reloaded.match_ids(library, *IN_GAME)  # re-derivado, não servido em stale
    assert reloaded.stats()["hits"] == 0
    assert reloaded.stats()["misses"] == 1


def test_only_current_library_ids_are_resolved(nav, library):
    """Ids guardados resolvem-se contra a biblioteca atual — nunca contra a antiga."""
    cache = nav.PatternCache()
    cache.match_ids(library, *LAUNCH)

    shrunk = copy.deepcopy(library)
    shrunk["patterns"] = shrunk["patterns"][:1]
    resolved = cache.candidates_for(shrunk, *LAUNCH)
    ids = {p["id"] for p in resolved}

    assert ids <= {p["id"] for p in shrunk["patterns"]}
