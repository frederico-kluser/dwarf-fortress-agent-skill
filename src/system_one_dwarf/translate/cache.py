"""Translation cache: in-memory LRU with an optional on-disk JSON mirror.

Screen translation runs at game-loop speed over a screen that mostly does not
change, so the same line is asked for over and over.  A cache hit costs zero
tokens and ~0 ms, which is what makes :meth:`DeepSeekTranslator.translate_screen`
affordable in real time.

The cache is keyed by ``(model, direction, text)`` — plus an optional ``extra``
part the caller may use (this module's callers pass the glossary signature, so a
glossary edit cannot serve stale jargon).  Only the translation *text* is stored;
a hit returns a fresh :class:`~system_one_dwarf.translate.translator.Translation`
with zero usage and ``via="cache"``, because a hit really did cost nothing.

Disk storage is a single JSON file (atomic replace, best-effort on autosave).
A corrupt or missing file is never fatal: the cache simply starts empty.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterator, Mapping

from .translator import Translation, Usage

__all__ = ["TranslationCache", "cache_key", "CACHE_VERSION"]

#: On-disk format version (bump to invalidate old files).
CACHE_VERSION = 1

_SEPARATOR = b"\x1f"


def cache_key(direction: str, text: str, *, model: str = "", extra: str = "") -> str:
    """Stable key for one (model, direction, text[, extra]) translation."""
    digest = hashlib.sha256()
    for part in (model or "", direction or "", extra or "", text or ""):
        digest.update(part.encode("utf-8"))
        digest.update(_SEPARATOR)
    return digest.hexdigest()


class TranslationCache:
    """LRU translation cache, thread-safe, optionally persisted to JSON.

    Parameters
    ----------
    maxsize:
        Maximum number of entries kept (memory *and* disk mirror).
    path:
        JSON file used as a persistent mirror; ``None`` keeps the cache purely
        in memory.
    autosave:
        Write the file after every :meth:`put`.  Enabled by default when a path
        is given; ``False`` makes persistence explicit via :meth:`save`.
    """

    def __init__(
        self,
        *,
        maxsize: int = 4096,
        path: str | Path | None = None,
        autosave: bool = True,
    ) -> None:
        self.maxsize = max(0, int(maxsize))
        self.path = Path(path) if path is not None else None
        self.autosave = bool(autosave) and self.path is not None
        self._entries: "OrderedDict[str, dict[str, str]]" = OrderedDict()
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0
        self.puts = 0
        self.evictions = 0
        self.disk_loads = 0
        if self.path is not None:
            self.load()

    # ---------------------------------------------------------------- memory API

    def get(
        self,
        direction: str,
        text: str,
        *,
        model: str = "",
        extra: str = "",
    ) -> Translation | None:
        """Cached :class:`Translation` (zero usage, ``via="cache"``) or ``None``."""
        key = cache_key(direction, text, model=model, extra=extra)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
        return Translation(
            text=entry.get("text", ""),
            source_lang=entry.get("source_lang", ""),
            target_lang=entry.get("target_lang", ""),
            usage=Usage(),
            latency_ms=0,
            model=entry.get("model", model),
            via="cache",
        )

    def put(
        self,
        direction: str,
        text: str,
        translation: Translation,
        *,
        model: str = "",
        extra: str = "",
    ) -> None:
        """Store ``translation`` for ``text``; evicts the oldest entry if full."""
        if self.maxsize == 0:
            return
        key = cache_key(direction, text, model=model, extra=extra)
        entry = {
            "text": translation.text,
            "source_lang": translation.source_lang,
            "target_lang": translation.target_lang,
            "model": translation.model,
        }
        with self._lock:
            self._entries[key] = entry
            self._entries.move_to_end(key)
            self.puts += 1
            while len(self._entries) > self.maxsize:
                self._entries.popitem(last=False)
                self.evictions += 1
        if self.autosave:
            try:
                self.save()
            except OSError:
                # Best effort: a read-only/full disk must never break a translation.
                pass

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __contains__(self, key: object) -> bool:  # pragma: no cover - convenience
        with self._lock:
            return isinstance(key, str) and key in self._entries

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            return iter(list(self._entries))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"TranslationCache(size={len(self)}, maxsize={self.maxsize}, "
            f"path={str(self.path) if self.path else None})"
        )

    def stats(self) -> dict[str, Any]:
        """Counters for the live-metrics panel (hits/misses/evictions)."""
        with self._lock:
            size = len(self._entries)
        return {
            "size": size,
            "maxsize": self.maxsize,
            "hits": self.hits,
            "misses": self.misses,
            "puts": self.puts,
            "evictions": self.evictions,
            "disk_loads": self.disk_loads,
            "path": str(self.path) if self.path else None,
        }

    # ------------------------------------------------------------------- disk IO

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
                text = item.get("text")
                if not isinstance(key, str) or not isinstance(text, str):
                    continue
                if key in self._entries:
                    continue
                self._entries[key] = {
                    "text": text,
                    "source_lang": str(item.get("source_lang", "")),
                    "target_lang": str(item.get("target_lang", "")),
                    "model": str(item.get("model", "")),
                }
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
