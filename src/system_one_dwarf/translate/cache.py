"""Translation cache: the project's shared cache core, specialised for text.

Screen translation runs at game-loop speed over a screen that mostly does not
change, so the same line is asked for over and over.  A cache hit costs zero
tokens and ~0 ms, which is what makes :meth:`DeepSeekTranslator.translate_screen`
affordable in real time.

The cache is keyed by ``(model, direction, text)`` — plus an optional ``extra``
part the caller may use (this module's callers pass the glossary signature, so a
glossary edit cannot serve stale jargon).  Only the translation *text* is stored;
a hit returns a fresh :class:`~system_one_dwarf.translate.translator.Translation`
with zero usage and ``via="cache"``, because a hit really did cost nothing.

Everything else — LRU behaviour, counters, the JSON disk mirror (atomic replace,
best-effort on autosave; a corrupt or missing file is never fatal) and the
per-session metrics of ``system_one_dwarf.cache.CacheSession`` — comes from
:class:`system_one_dwarf.cache.JsonEntryCache`, the generic core extracted from
this module (Passo 19).  :func:`cache_key` keeps its historical signature and
delegates to :func:`system_one_dwarf.cache.value_key`.
"""

from __future__ import annotations

from pathlib import Path

from ..cache import CACHE_VERSION, JsonEntryCache, value_key
from .translator import Translation, Usage

__all__ = ["TranslationCache", "cache_key", "CACHE_VERSION"]


def cache_key(direction: str, text: str, *, model: str = "", extra: str = "") -> str:
    """Stable key for one (model, direction, text[, extra]) translation."""
    return value_key(model or "", direction or "", extra or "", text or "")


class TranslationCache(JsonEntryCache):
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
        super().__init__(maxsize=maxsize, path=path, autosave=autosave, name="translation")

    # ---------------------------------------------------------- domain API

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
        entry = self._lookup(key)
        if entry is None:
            return None
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
        self._store(key, {
            "text": translation.text,
            "source_lang": translation.source_lang,
            "target_lang": translation.target_lang,
            "model": translation.model,
            # O custo da geração alimenta o "saved_usd" dos hits (métrica de sessão).
            "cost_usd": float(getattr(translation.usage, "cost_usd", 0.0) or 0.0),
        })
