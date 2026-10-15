"""Generic LLM/pattern cache core: keyed LRU + JSON disk mirror + session metrics.

This is the machinery the translation cache always had
(:mod:`system_one_dwarf.translate.cache`), extracted so every "pay once, reuse
when nothing changed" seam of the project shares ONE pattern (Passo 19 —
``PROXIMOS-PASSOS.md``):

* :class:`JsonEntryCache` — thread-safe LRU of JSON dict-entries with an
  optional on-disk mirror (atomic replace; a corrupt/missing file is never
  fatal) and the counters every consumer reports (hits/misses/puts/evictions,
  :meth:`JsonEntryCache.hit_rate` and the estimated ``saved_usd`` of hits);
* :func:`value_key` — the stable sha256 key builder (``\\x1f``-separated parts)
  every cache keys itself with;
* :class:`CacheSession` — the **reportable per-session metric** (hit-rate and
  cost saved, money in the 6-decimal contract) plus :meth:`CacheSession.record_to`,
  which writes the summary through the same ``record(provider, model, …)``
  seam the project uses for spend (``campaign.spend.SpendLedger``,
  ``agent.llm.spend_recorder``).

Subclasses translate their domain types to/from flat dict entries (see
``TranslationCache``) or add conservative invalidation on top (see
``agent.plan_cache.PlanCache``).  Entries are stored as ``{"key": …, **entry}``
on disk — the format the translation cache has always mirrored.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

__all__ = [
    "CACHE_VERSION",
    "CacheEvent",
    "CacheSession",
    "JsonEntryCache",
    "money",
    "utc_now_iso",
    "value_key",
]

#: On-disk format version (bump to invalidate old files).
CACHE_VERSION = 1

_SEPARATOR = b"\x1f"


def utc_now_iso() -> str:
    """UTC timestamp in the project's ISO format (dependency-free clock)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def money(value: float | int | None) -> float:
    """Contract money format: float with 6 decimals (as ``campaign.spend.money``)."""
    if value is None:
        return 0.0
    return round(float(value), 6)


def value_key(*parts: Any) -> str:
    """Stable sha256 key over ordered parts (each followed by ``\\x1f``).

    Generalises ``translate.cache.cache_key``: every cache of the project keys
    itself the same way, so "same input" always means "same key".
    """
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part if part is not None else "").encode("utf-8"))
        digest.update(_SEPARATOR)
    return digest.hexdigest()


# --------------------------------------------------------------------- events


class CacheEvent:
    """What one lookup did (the session metric's unit; cheap to allocate)."""

    __slots__ = ("name", "hit", "saved_usd", "ts")

    def __init__(self, name: str, hit: bool, saved_usd: float = 0.0, ts: str = "") -> None:
        self.name = str(name)
        self.hit = bool(hit)
        self.saved_usd = float(saved_usd or 0.0)
        self.ts = ts or utc_now_iso()

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "hit": bool(self.hit),
            "saved_usd": money(self.saved_usd),
            "ts": self.ts,
        }


# ------------------------------------------------------------------ the cache


class JsonEntryCache:
    """LRU cache of flat dict entries, thread-safe, optionally persisted to JSON.

    Parameters
    ----------
    maxsize:
        Maximum number of entries kept (memory *and* disk mirror); ``0``
        disables storage entirely.
    path:
        JSON file used as a persistent mirror; ``None`` keeps the cache purely
        in memory.
    autosave:
        Write the file after every :meth:`put`.  Enabled by default when a path
        is given; ``False`` makes persistence explicit via :meth:`save`.
    name:
        Metric name reported by :meth:`stats` and :class:`CacheSession`.
    """

    def __init__(
        self,
        *,
        maxsize: int = 4096,
        path: str | Path | None = None,
        autosave: bool = True,
        name: str = "",
    ) -> None:
        self.maxsize = max(0, int(maxsize))
        self.path = Path(path) if path is not None else None
        self.autosave = bool(autosave) and self.path is not None
        self.name = str(name or type(self).__name__)
        self._entries: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0
        self.puts = 0
        self.evictions = 0
        self.disk_loads = 0
        #: Estimated dollars saved by hits (sum of the stored ``cost_usd``).
        self.saved_usd = 0.0
        if self.path is not None:
            self.load()

    # ------------------------------------------------------------- counting

    def peek(self, key: str) -> dict[str, Any] | None:
        """Entry for ``key`` or ``None`` — **without** counting or LRU touch."""
        with self._lock:
            entry = self._entries.get(key)
            return dict(entry) if entry is not None else None

    def count_hit(self, entry: Mapping[str, Any]) -> None:
        """Count a hit (and its ``cost_usd`` as saved) for a verified entry."""
        with self._lock:
            self.hits += 1
            self.saved_usd += float(entry.get("cost_usd") or 0.0)

    def count_miss(self) -> None:
        """Count a miss (for lookups that verify before counting)."""
        with self._lock:
            self.misses += 1

    def _lookup(self, key: str) -> dict[str, Any] | None:
        """Entry for ``key`` (LRU touch) or ``None``; counts hit/miss."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            self.saved_usd += float(entry.get("cost_usd") or 0.0)
            return dict(entry)

    def _store(self, key: str, entry: Mapping[str, Any]) -> None:
        """Store one flat entry (``cost_usd`` is optional, feeds ``saved_usd``)."""
        if self.maxsize == 0:
            return
        payload = {k: v for k, v in entry.items() if k != "key"}
        with self._lock:
            self._entries[key] = payload
            self._entries.move_to_end(key)
            self.puts += 1
            while len(self._entries) > self.maxsize:
                self._entries.popitem(last=False)
                self.evictions += 1
        if self.autosave:
            try:
                self.save()
            except OSError:
                # Best effort: a read-only/full disk must never break a lookup.
                pass

    def drop(self, key: str) -> bool:
        """Remove one entry (conservative invalidation); ``True`` if it existed."""
        with self._lock:
            return self._entries.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    # ---------------------------------------------------------- public API

    def get(self, key: str) -> dict[str, Any] | None:
        """Raw entry for ``key`` or ``None`` (counts a hit/miss)."""
        return self._lookup(key)

    def put(self, key: str, entry: Mapping[str, Any]) -> None:
        """Store one flat entry; evicts the oldest entry if full."""
        self._store(key, entry)

    def items(self) -> list[tuple[str, dict[str, Any]]]:
        """Snapshot of ``(key, entry)`` pairs (for subclass sweeps)."""
        with self._lock:
            return [(key, dict(entry)) for key, entry in self._entries.items()]

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return isinstance(key, str) and key in self._entries

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            return iter(list(self._entries))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"{type(self).__name__}(size={len(self)}, maxsize={self.maxsize}, "
            f"path={str(self.path) if self.path else None})"
        )

    def hit_rate(self) -> float:
        """``hits / (hits + misses)`` — ``0.0`` when nothing was looked up."""
        total = self.hits + self.misses
        return (self.hits / total) if total else 0.0

    def stats(self) -> dict[str, Any]:
        """Counters for reports (hits/misses/hit_rate/saved_usd/evictions…)."""
        with self._lock:
            size = len(self._entries)
        return {
            "name": self.name,
            "size": size,
            "maxsize": self.maxsize,
            "hits": self.hits,
            "misses": self.misses,
            "puts": self.puts,
            "evictions": self.evictions,
            "disk_loads": self.disk_loads,
            "hit_rate": round(self.hit_rate(), 6),
            "saved_usd": money(self.saved_usd),
            "path": str(self.path) if self.path else None,
        }

    # --------------------------------------------------------------- disk IO

    def load(self) -> int:
        """Read the JSON mirror; returns how many entries were restored."""
        if self.path is None or not self.path.is_file():
            return 0
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        entries = payload.get("entries") if isinstance(payload, Mapping) else None
        if not isinstance(entries, list):
            return 0
        loaded = 0
        with self._lock:
            for item in entries:
                if not isinstance(item, Mapping):
                    continue
                key = item.get("key")
                if not isinstance(key, str) or key in self._entries:
                    continue
                self._entries[key] = {k: v for k, v in item.items() if k != "key"}
                loaded += 1
            while len(self._entries) > self.maxsize:
                self._entries.popitem(last=False)
                self.evictions += 1
        self.disk_loads += loaded
        return loaded

    def save(self) -> Path | None:
        """Atomically write the JSON mirror (raises ``OSError`` on failure)."""
        if self.path is None:
            return None
        with self._lock:
            payload = {
                "version": CACHE_VERSION,
                "entries": [
                    {"key": key, **entry} for key, entry in self._entries.items()
                ],
            }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=0, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
        return self.path


# ----------------------------------------------------------- session metrics


class CacheSession:
    """Per-session cache accounting: hit-rate + cost saved, reportable.

    "Where the project already records spend, follow the same pattern": the
    report rounds money to the 6-decimal contract and :meth:`record_to` writes
    the summary through the ``record(provider, model, *, cost_usd,
    input_tokens, output_tokens, reason, ts)`` seam of
    :class:`system_one_dwarf.campaign.spend.SpendLedger` (the seam
    :func:`system_one_dwarf.agent.llm.spend_recorder` adapts).  Cache rows cost
    ``0.0`` on purpose: a hit really costs nothing and the spend totals must
    not be inflated by savings; the savings travel in the ``reason`` line and,
    structurally, in :meth:`report`.
    """

    def __init__(self, session_id: str = "", *, clock: Callable[[], str] = utc_now_iso) -> None:
        self.session_id = str(session_id)
        self.clock = clock
        self.started = clock()
        self._caches: dict[str, JsonEntryCache] = {}
        self._baseline: dict[str, tuple[int, int, float]] = {}

    def bind(self, name: str, cache: JsonEntryCache) -> JsonEntryCache:
        """Attach ``cache`` to this session (deltas count from NOW on)."""
        key = str(name or cache.name)
        self._caches[key] = cache
        self._baseline[key] = (cache.hits, cache.misses, cache.saved_usd)
        return cache

    def report(self) -> dict[str, Any]:
        """The reportable metric: per cache and total, for this session only."""
        caches: dict[str, dict[str, Any]] = {}
        hits = misses = 0
        saved = 0.0
        for key, cache in self._caches.items():
            base_hits, base_misses, base_saved = self._baseline.get(key, (0, 0, 0.0))
            d_hits = cache.hits - base_hits
            d_misses = cache.misses - base_misses
            d_saved = float(cache.saved_usd) - base_saved
            total = d_hits + d_misses
            caches[key] = {
                "hits": d_hits,
                "misses": d_misses,
                "hit_rate": round((d_hits / total) if total else 0.0, 6),
                "saved_usd": money(d_saved),
                "size": len(cache),
            }
            hits += d_hits
            misses += d_misses
            saved += d_saved
        total_lookups = hits + misses
        return {
            "session": self.session_id,
            "started": self.started,
            "ts": self.clock(),
            "hits": hits,
            "misses": misses,
            "hit_rate": round((hits / total_lookups) if total_lookups else 0.0, 6),
            "saved_usd": money(saved),
            "caches": caches,
        }

    def record_to(self, ledger: Any) -> list[dict[str, Any]]:
        """Write one summary row per cache through a ``SpendLedger``-like seam.

        ``ledger`` only needs ``record(provider, model, *, cost_usd,
        input_tokens, output_tokens, reason=None, ts=None)`` — exactly the
        signature ``agent.llm.spend_recorder`` expects.  Returns the stored rows.
        """
        rows: list[dict[str, Any]] = []
        report = self.report()
        for name, entry in report["caches"].items():
            reason = (
                f"cache:{name} hits={entry['hits']} misses={entry['misses']} "
                f"hit_rate={entry['hit_rate']:.6f} saved_usd={entry['saved_usd']:.6f}"
            )
            row = ledger.record(
                "cache",
                name,
                cost_usd=0.0,
                input_tokens=int(entry["hits"]),
                output_tokens=int(entry["misses"]),
                reason=reason,
                ts=report["ts"],
            )
            rows.append(row)
        return rows
