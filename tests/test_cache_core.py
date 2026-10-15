"""``system_one_dwarf.cache``: núcleo genérico de cache + métrica por sessão.

É o mecanismo do cache de tradução extraído/generalizado (Passo 19): LRU com
espelho JSON, contadores (hits/misses/hit_rate/custo poupado) e a métrica
reportável por sessão — registada no mesmo padrão onde o projeto regista gastos
(``campaign.spend.SpendLedger``).
"""

from __future__ import annotations

import json
from pathlib import Path

from system_one_dwarf.cache import CacheSession, JsonEntryCache, money, value_key
from system_one_dwarf.campaign.spend import SpendLedger


def entry(text: str = "olá", *, cost_usd: float = 0.0) -> dict:
    return {"text": text, "cost_usd": cost_usd}


# ------------------------------------------------------------------ value_key


def test_value_key_is_stable_and_part_sensitive() -> None:
    assert value_key("a", "b") == value_key("a", "b")
    assert value_key("a", "b") != value_key("b", "a")
    assert value_key("a", "b") != value_key("a", "b", "")
    assert value_key("a", None) == value_key("a", "")  # None ≡ vazio (como cache_key)


# --------------------------------------------------------------- JsonEntryCache


def test_hit_and_miss_are_counted_and_a_hit_returns_the_entry() -> None:
    cache = JsonEntryCache(name="x")
    cache.put("k", entry("olá"))

    assert cache.get("k")["text"] == "olá"
    assert cache.get("missing") is None

    stats = cache.stats()
    assert stats["hits"] == 1 and stats["misses"] == 1
    assert stats["hit_rate"] == 0.5
    assert stats["name"] == "x"


def test_hit_rate_is_zero_before_any_lookup() -> None:
    assert JsonEntryCache().hit_rate() == 0.0


def test_a_hit_saves_the_stored_generation_cost() -> None:
    cache = JsonEntryCache()
    cache.put("k", entry(cost_usd=0.25))

    cache.get("k")
    cache.get("k")

    assert cache.stats()["saved_usd"] == 0.5


def test_money_follows_the_six_decimal_contract() -> None:
    assert money(0.123456789) == 0.123457
    assert money(None) == 0.0


def test_lru_eviction_and_recency() -> None:
    cache = JsonEntryCache(maxsize=2)
    cache.put("a", entry())
    cache.put("b", entry())
    assert cache.get("a") is not None  # "a" fica recente
    cache.put("c", entry())

    assert cache.get("b") is None and cache.get("a") is not None
    assert cache.stats()["evictions"] == 1


def test_peek_does_not_count_or_touch_recency() -> None:
    cache = JsonEntryCache(maxsize=1)
    cache.put("a", entry())

    assert cache.peek("a")["text"] == "olá"
    assert cache.peek("zz") is None

    stats = cache.stats()
    assert stats["hits"] == 0 and stats["misses"] == 0


def test_count_hit_and_count_miss_feed_the_same_counters() -> None:
    cache = JsonEntryCache()
    cache.put("k", entry(cost_usd=0.1))

    cached = cache.peek("k")
    cache.count_hit(cached)
    cache.count_miss()

    stats = cache.stats()
    assert (stats["hits"], stats["misses"], stats["saved_usd"]) == (1, 1, 0.1)


def test_disk_mirror_round_trip_and_corruption_is_never_fatal(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    first = JsonEntryCache(path=path, name="disk")
    first.put("k", entry("guardado", cost_usd=0.02))

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1 and payload["entries"][0]["key"] == "k"

    second = JsonEntryCache(path=path, name="disk")
    assert second.get("k")["text"] == "guardado"
    assert second.stats()["disk_loads"] == 1

    path.write_text("{corrupto", encoding="utf-8")
    assert len(JsonEntryCache(path=path)) == 0  # nunca rebenta


def test_drop_and_items_support_conservative_invalidation() -> None:
    cache = JsonEntryCache()
    cache.put("a", entry())
    cache.put("b", entry())

    assert {k for k, _ in cache.items()} == {"a", "b"}
    assert cache.drop("a") is True and cache.drop("a") is False
    assert list(cache) == ["b"] and len(cache) == 1


# ------------------------------------------------------------- CacheSession


def test_session_report_is_per_session_and_carries_cost_and_hit_rate() -> None:
    cache = JsonEntryCache(name="hunt_plan")
    cache.get("warmup")  # atividade ANTES da sessão: não conta

    session = CacheSession("sessão-1", clock=lambda: "2026-10-09T00:00:00+00:00")
    session.bind("hunt_plan", cache)
    cache.put("k", entry(cost_usd=0.001))
    cache.get("k")          # hit (poupa 0.001)
    cache.get("outro")      # miss

    report = session.report()
    assert report["session"] == "sessão-1"
    assert report["hits"] == 1 and report["misses"] == 1
    assert report["hit_rate"] == 0.5
    assert report["saved_usd"] == 0.001          # custo poupado (contrato 6 decimais)
    assert report["caches"]["hunt_plan"]["hit_rate"] == 0.5

    again = session.report()
    assert again["hits"] == 1  # deltas estáveis: a sessão não dupla-conta


def test_two_sessions_split_the_same_cache_activity() -> None:
    cache = JsonEntryCache(name="t")
    cache.put("k", entry())

    first = CacheSession("s1")
    first.bind("t", cache)
    cache.get("k")
    snapshot = first.report()
    assert snapshot["hits"] == 1 and snapshot["misses"] == 0

    second = CacheSession("s2")
    second.bind("t", cache)          # passado fica fora da sessão s2
    cache.get("k")
    cache.get("missing")

    # Cada sessão = a atividade desde o seu início (life-time da sessão).
    assert second.report()["hits"] == 1 and second.report()["misses"] == 1
    assert second.report()["hit_rate"] == 0.5


def test_record_to_follows_the_spend_ledger_pattern_without_inflating_spend(tmp_path: Path) -> None:
    ledger = SpendLedger(tmp_path / "gastos.sqlite")
    cache = JsonEntryCache(name="hunt_plan")
    session = CacheSession("sessão-1")
    session.bind("hunt_plan", cache)
    cache.put("k", entry(cost_usd=0.002))
    cache.get("k")
    cache.get("outro")

    rows = session.record_to(ledger)

    assert len(rows) == 1
    row = rows[0]
    assert row["provider"] == "cache" and row["model"] == "hunt_plan"
    assert row["cost_usd"] == 0.0                      # hits não são gasto
    assert row["input_tokens"] == 1 and row["output_tokens"] == 1  # hits/misses
    assert "hit_rate=0.500000" in row["reason"]
    assert "saved_usd=0.002000" in row["reason"]
    assert ledger.total_usd() == 0.0                  # o spend real não inflaciona
    assert ledger.calls() == 1                        # padrão: uma linha por registo


def test_the_translation_cache_shares_the_core_and_the_session_metric() -> None:
    """O cache de tradução é o mesmo núcleo — generalização, não reimplementação."""
    from system_one_dwarf.translate import Translation, TranslationCache, Usage

    cache = TranslationCache()
    assert isinstance(cache, JsonEntryCache)
    session = CacheSession("sessão-tradução")
    session.bind("translation", cache)

    sample = Translation(text="olá", source_lang="en", target_lang="pt",
                         usage=Usage(input_tokens=100, output_tokens=50, cost_usd=0.001),
                         latency_ms=1, model="deepseek/deepseek-v4.1-flash", via="llm")
    cache.put("en->pt", "hello", sample, model="deepseek/deepseek-v4.1-flash")

    assert cache.get("en->pt", "hello", model="deepseek/deepseek-v4.1-flash") is not None
    assert cache.get("en->pt", "bye", model="deepseek/deepseek-v4.1-flash") is None

    report = session.report()
    assert report["hits"] == 1 and report["misses"] == 1
    assert report["hit_rate"] == 0.5
    assert report["saved_usd"] == 0.001   # uma tradução poupada
