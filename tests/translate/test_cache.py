"""TranslationCache: LRU behaviour, key composition, disk mirror, tolerance."""

from __future__ import annotations

import json
from pathlib import Path

from system_one_dwarf.translate import Translation, TranslationCache, Usage, cache_key

MODEL = "deepseek/deepseek-v4.1-flash"


def sample(text: str = "olá", *, model: str = MODEL) -> Translation:
    return Translation(
        text=text,
        source_lang="en",
        target_lang="pt",
        usage=Usage(input_tokens=100, output_tokens=50, cost_usd=0.00009),
        latency_ms=12,
        model=model,
        via="llm",
    )


# --------------------------------------------------------------------- keys


def test_cache_key_is_stable_and_direction_sensitive() -> None:
    assert cache_key("en->pt", "hello") == cache_key("en->pt", "hello")
    assert cache_key("en->pt", "hello") != cache_key("pt->en", "hello")
    assert cache_key("en->pt", "hello") != cache_key("en->pt", "hello!")
    assert cache_key("en->pt", "hello", model="a") != cache_key("en->pt", "hello", model="b")
    assert cache_key("en->pt", "hello", extra="g1") != cache_key("en->pt", "hello", extra="g2")


def test_lookup_is_scoped_by_direction_and_model() -> None:
    cache = TranslationCache()
    cache.put("en->pt", "hello", sample(), model=MODEL)

    assert cache.get("en->pt", "hello", model=MODEL) is not None
    assert cache.get("pt->en", "hello", model=MODEL) is None
    assert cache.get("en->pt", "hello", model="another-model") is None
    assert cache.get("en->pt", "hello") is None  # unscoped lookup is a different key


# ---------------------------------------------------------------------- hits


def test_hit_is_zero_cost_and_flagged_as_cached() -> None:
    cache = TranslationCache()
    cache.put("en->pt", "hello", sample("olá"), model=MODEL)

    hit = cache.get("en->pt", "hello", model=MODEL)

    assert hit is not None
    assert hit.text == "olá"
    assert hit.source_lang == "en"
    assert hit.target_lang == "pt"
    assert hit.model == MODEL
    assert hit.via == "cache"
    assert hit.cached is True
    assert hit.usage == Usage()
    assert hit.cost_usd == 0.0
    assert hit.latency_ms == 0
    assert cache.stats()["hits"] == 1
    assert cache.stats()["misses"] == 0


def test_miss_is_counted() -> None:
    cache = TranslationCache()

    assert cache.get("en->pt", "unknown") is None
    assert cache.stats()["misses"] == 1


def test_stats_report_size_and_puts() -> None:
    cache = TranslationCache()
    cache.put("en->pt", "a", sample("a"))
    cache.put("en->pt", "b", sample("b"))

    stats = cache.stats()

    assert stats["size"] == 2
    assert stats["puts"] == 2
    assert stats["maxsize"] == 4096
    assert stats["path"] is None


# ----------------------------------------------------------------------- LRU


def test_lru_evicts_the_oldest_entry() -> None:
    cache = TranslationCache(maxsize=2)
    cache.put("en->pt", "a", sample("a"))
    cache.put("en->pt", "b", sample("b"))
    cache.put("en->pt", "c", sample("c"))

    assert len(cache) == 2
    assert cache.get("en->pt", "a") is None
    assert cache.get("en->pt", "b") is not None
    assert cache.get("en->pt", "c") is not None
    assert cache.stats()["evictions"] == 1


def test_get_refreshes_recency() -> None:
    cache = TranslationCache(maxsize=2)
    cache.put("en->pt", "a", sample("a"))
    cache.put("en->pt", "b", sample("b"))
    cache.get("en->pt", "a")  # "a" becomes the most recent
    cache.put("en->pt", "c", sample("c"))

    assert cache.get("en->pt", "a") is not None
    assert cache.get("en->pt", "b") is None


def test_maxsize_zero_disables_storage() -> None:
    cache = TranslationCache(maxsize=0)
    cache.put("en->pt", "a", sample("a"))

    assert len(cache) == 0
    assert cache.get("en->pt", "a") is None


def test_clear_empties_the_cache() -> None:
    cache = TranslationCache()
    cache.put("en->pt", "a", sample("a"))
    cache.clear()

    assert len(cache) == 0
    assert cache.get("en->pt", "a") is None


# ------------------------------------------------------------------ disk mirror


def test_disk_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "cache" / "translations.json"
    first = TranslationCache(path=path)

    first.put("en->pt", "hello", sample("olá"), model=MODEL)
    assert path.is_file()

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert len(payload["entries"]) == 1

    second = TranslationCache(path=path)
    hit = second.get("en->pt", "hello", model=MODEL)

    assert hit is not None
    assert hit.text == "olá"
    assert hit.via == "cache"
    assert second.stats()["disk_loads"] == 1


def test_save_creates_missing_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "nested" / "cache.json"
    cache = TranslationCache(path=path)

    cache.put("en->pt", "hello", sample())

    assert path.is_file()


def test_autosave_can_be_disabled(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    cache = TranslationCache(path=path, autosave=False)

    cache.put("en->pt", "hello", sample())
    assert not path.exists()

    cache.save()
    assert path.exists()


def test_corrupt_disk_file_is_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    path.write_text("{not json at all", encoding="utf-8")

    cache = TranslationCache(path=path)

    assert len(cache) == 0
    assert cache.get("en->pt", "hello") is None
    cache.put("en->pt", "hello", sample())  # rewriting repairs the file
    assert json.loads(path.read_text(encoding="utf-8"))["entries"]


def test_unexpected_disk_payload_is_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({"version": 1, "entries": "nope"}), encoding="utf-8")

    assert len(TranslationCache(path=path)) == 0


def test_disk_mirror_is_capped_by_maxsize(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    cache = TranslationCache(path=path, maxsize=1)
    cache.put("en->pt", "a", sample("a"))
    cache.put("en->pt", "b", sample("b"))

    assert len(json.loads(path.read_text(encoding="utf-8"))["entries"]) == 1
    assert TranslationCache(path=path).get("en->pt", "b") is not None


def test_save_without_a_path_is_a_noop() -> None:
    assert TranslationCache().save() is None
