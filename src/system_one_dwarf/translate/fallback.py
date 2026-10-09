"""Fallbacks for the translator slot: identity, and glossary → LLM → identity.

The backend treats translation as a **pluggable slot**: it must never be the
reason the game loop stops.  Two implementations carry that guarantee:

* :class:`IdentityTranslator` — returns the input unchanged, zero cost, never
  raises.  It is what the system uses when no OpenRouter key is configured, so
  the loop still runs (English text reaches the model, untranslated text reaches
  the UI) instead of crashing.
* :class:`ChainTranslator` — wraps a real translator with the deterministic
  glossary and the identity fallback::

      glossary terms → DeepSeek → identity (on any error)

  The glossary is applied to *both* ends: PT→EN before the call for user input
  (the model receives canonical English jargon) and EN→PT after the call for
  screen text (the UI receives the project's Portuguese jargon).  A short text
  made only of glossary terms is translated without any call at all
  (``via="glossary"``).  Any error from the real translator degrades to the
  identity translation of the glossary-processed text and is recorded in
  ``last_error``/``error_count`` — it is never re-raised unless ``raise_errors``
  is set on purpose.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Sequence

from .glossary import Glossary
from .translator import (
    DeepSeekTranslator,
    ScreenTranslation,
    Translation,
    Translator,
    Usage,
    VIA_GLOSSARY,
    VIA_IDENTITY,
    VIA_SKIPPED,
    is_translatable,
)

__all__ = [
    "IdentityTranslator",
    "ChainTranslator",
    "build_translator",
    "IDENTITY_MODEL",
    "GLOSSARY_MODEL",
]

#: ``Translation.model`` of the identity fallback.
IDENTITY_MODEL = "identity"

#: ``Translation.model`` of a deterministic glossary-only translation.
GLOSSARY_MODEL = "glossary"


class IdentityTranslator:
    """No-op translator: input in, input out, zero cost, never raises.

    Implements the same protocol as :class:`DeepSeekTranslator`
    (``to_pt``/``to_en``/``to_pt_batch`` plus ``translate_screen``), so it can be
    dropped into the backend when no API key is configured.
    """

    #: Kept for interface parity with the real translator.
    model = IDENTITY_MODEL
    supports_screens = True

    def __init__(self, *, name: str = "identity") -> None:
        self.name = name
        self.calls = 0

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"IdentityTranslator(model={IDENTITY_MODEL!r})"

    def is_configured(self) -> bool:
        """Always ``False``: this translator is the "no key" fallback."""
        return False

    def close(self) -> None:
        """Nothing to close (protocol parity)."""
        return None

    def __enter__(self) -> "IdentityTranslator":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def _identity(self, text: str, source_lang: str, target_lang: str) -> Translation:
        return Translation(
            text=text or "",
            source_lang=source_lang,
            target_lang=target_lang,
            usage=Usage(),
            latency_ms=0,
            model=IDENTITY_MODEL,
            via=VIA_IDENTITY,
        )

    def to_pt(self, text_en: str) -> Translation:
        """Return the English text unchanged (zero usage)."""
        return self._identity(text_en, "en", "pt")

    def to_en(self, text_pt: str) -> Translation:
        """Return the Portuguese text unchanged (zero usage)."""
        return self._identity(text_pt, "pt", "en")

    def translate(self, text: str, direction: str) -> Translation:
        if direction == "en->pt":
            return self.to_pt(text)
        if direction == "pt->en":
            return self.to_en(text)
        raise ValueError(f"unknown direction {direction!r}; expected 'en->pt' or 'pt->en'")

    def to_pt_batch(self, lines: Sequence[str]) -> list[Translation]:
        """Identity for every line, in order."""
        return [self._identity(line, "en", "pt") for line in lines]

    def to_english(self, text: str) -> str:
        """``str -> str`` seam for ``campaign.memory``: identity, never raises."""
        return text

    def to_portuguese(self, text: str) -> str:
        """``str -> str`` seam for ``campaign.memory``: identity, never raises."""
        return text

    def translate_screen(
        self,
        screen_text: str,
        previous_translation: ScreenTranslation | str | None = None,
    ) -> ScreenTranslation:
        """The screen, unchanged, presented as a :class:`ScreenTranslation`."""
        lines = (screen_text or "").split("\n")
        return ScreenTranslation(
            text_pt="\n".join(lines),
            lines_pt=tuple(lines),
            source_lines=tuple(lines),
            usage=Usage(),
            latency_ms=0,
            model=IDENTITY_MODEL,
            llm_calls=0,
            translated_lines=0,
            cached_lines=0,
            reused_lines=0,
            skipped_lines=len(lines),
        )


class ChainTranslator:
    """Glossary → real translator → identity fallback, with a final cache.

    Parameters
    ----------
    primary:
        The real translator (normally a :class:`DeepSeekTranslator`).
    fallback:
        Used on any error (default :class:`IdentityTranslator`).
    glossary:
        Term table applied before (PT→EN) and after (EN→PT) the call; ``None``
        disables deterministic substitution.
    cache:
        Optional cache for **final** results (glossary already applied).  The
        primary translator keeps its own cache; pass one here only when the
        glossary post-processing must also be skipped on repeats.
    fast_path:
        Translate short texts made only of glossary terms without any call.
    raise_errors:
        ``True`` re-raises instead of degrading to the fallback (tests/debug).
    """

    def __init__(
        self,
        primary: Translator,
        *,
        fallback: Translator | None = None,
        glossary: Glossary | None = None,
        cache: Any = None,
        fast_path: bool = True,
        fast_path_max_tokens: int = 3,
        raise_errors: bool = False,
        name: str = "chain",
    ) -> None:
        self.primary = primary
        self.fallback: Translator = fallback if fallback is not None else IdentityTranslator()
        self.glossary = glossary if glossary is not None else Glossary.empty()
        self.cache = cache
        self.fast_path = bool(fast_path)
        self.fast_path_max_tokens = int(fast_path_max_tokens)
        self.raise_errors = bool(raise_errors)
        self.name = name
        #: Last error swallowed by the fallback (telemetry; never re-raised here).
        self.last_error: BaseException | None = None
        #: How many failed calls degraded to the fallback (a batch counts once).
        self.error_count = 0
        #: How many calls were served by the deterministic glossary path.
        self.glossary_hits = 0

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"ChainTranslator(primary={self.primary!r}, fallback={self.fallback!r}, "
            f"glossary={len(self.glossary)}, errors={self.error_count})"
        )

    def is_configured(self) -> bool:
        """True when the wrapped translator reports a usable configuration."""
        probe = getattr(self.primary, "is_configured", None)
        if callable(probe):
            try:
                return bool(probe())
            except Exception:  # pragma: no cover - defensive
                return False
        return True

    def close(self) -> None:
        for target in (self.primary, self.fallback):
            closer = getattr(target, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # pragma: no cover - defensive
                    pass

    def __enter__(self) -> "ChainTranslator":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ glossary

    def _glossary_signature(self) -> str:
        return getattr(self.glossary, "signature", "")

    def _deterministic(self, text: str, direction: str) -> str | None:
        """Glossary-only translation of a fully-known short text (or ``None``)."""
        if not self.fast_path or not text or not self.glossary:
            return None
        translated = self.glossary.translate_complete(
            text,
            direction,
            max_tokens=self.fast_path_max_tokens,
        )
        if translated is None or translated == text:
            return None
        self.glossary_hits += 1
        return translated

    def _post(self, text: str, direction: str) -> str:
        """Deterministic term substitution applied to a translated text."""
        if not text or not self.glossary:
            return text
        return self.glossary.to_pt(text) if direction == "en->pt" else self.glossary.to_en(text)

    def _pre(self, text: str, direction: str) -> str:
        """Deterministic term substitution applied before the call (PT→EN)."""
        if not text or not self.glossary or direction != "pt->en":
            return text
        return self.glossary.to_en(text)

    # ------------------------------------------------------------------ translate

    def translate(self, text: str, direction: str) -> Translation:
        """Translate in ``direction``, degrading instead of failing."""
        source_lang, target_lang = _langs(direction)
        if not text:
            return _translation("", source_lang, target_lang, via=VIA_IDENTITY, model=IDENTITY_MODEL)
        if not is_translatable(text):
            # Nothing to translate (blank/numbers/punctuation): never spend a call.
            return _translation(
                text,
                source_lang,
                target_lang,
                via=VIA_SKIPPED,
                model=getattr(self.primary, "model", "") or IDENTITY_MODEL,
            )

        deterministic = self._deterministic(text, direction)
        if deterministic is not None:
            return _translation(
                deterministic,
                source_lang,
                target_lang,
                via=VIA_GLOSSARY,
                model=GLOSSARY_MODEL,
            )

        cached = self._cache_get(direction, text)
        if cached is not None:
            return cached

        prepared = self._pre(text, direction)
        try:
            result = self._call_primary(prepared, direction)
        except Exception as exc:
            self._record_error(exc)
            return self._degrade(exc, text, direction)
        translated = self._post(result.text, direction)
        if translated != result.text:
            result = replace(result, text=translated)
        self._cache_put(direction, text, result)
        return result

    def to_pt(self, text_en: str) -> Translation:
        """English → Portuguese through the chain."""
        return self.translate(text_en, "en->pt")

    def to_en(self, text_pt: str) -> Translation:
        """Portuguese → English through the chain."""
        return self.translate(text_pt, "pt->en")

    def to_english(self, text: str) -> str:
        """PT→EN as a plain string for ``campaign.memory``; never raises."""
        return self._seam_text(text, "pt->en")

    def to_portuguese(self, text: str) -> str:
        """EN→PT as a plain string for ``campaign.memory``; never raises."""
        return self._seam_text(text, "en->pt")

    def _seam_text(self, text: str, direction: str) -> str:
        """The ``safe_translate`` contract: text in, text out, no exception.

        Even with ``raise_errors=True`` (a debug switch for :meth:`translate`)
        this seam stays total: a campaign must never fail because the translator
        is unavailable.
        """
        if not isinstance(text, str) or not text:
            return text
        try:
            return self.translate(text, direction).text or text
        except Exception:
            return text

    def to_pt_batch(self, lines: Sequence[str]) -> list[Translation]:
        """Batch EN→PT through the chain, keeping 1:1 order.

        Uses the primary's own ``to_pt_batch`` when it has one (one call for the
        screen); otherwise falls back to one call per line.  Each line is still
        glossary-processed and degrades to identity on error.
        """
        items = list(lines)
        batch_method = getattr(self.primary, "to_pt_batch", None)
        if not callable(batch_method):
            return [self.to_pt(line) for line in items]
        try:
            results = list(batch_method(items))
        except Exception as exc:
            self._record_error(exc)
            if self.raise_errors:
                raise
            return [self._degrade(exc, line, "en->pt") for line in items]
        output: list[Translation] = []
        for line, result in zip(items, results):
            translated = self._post(result.text, "en->pt")
            output.append(replace(result, text=translated) if translated != result.text else result)
        return output

    def translate_screen(
        self,
        screen_text: str,
        previous_translation: ScreenTranslation | str | None = None,
    ) -> ScreenTranslation:
        """Screen EN→PT: delegate to the primary, then normalise terminology."""
        screen_method = getattr(self.primary, "translate_screen", None)
        if callable(screen_method):
            try:
                screen = screen_method(screen_text, previous_translation)
            except Exception as exc:
                self._record_error(exc)
                if self.raise_errors:
                    raise
                return self._fallback_screen(screen_text, exc)
        else:  # pragma: no cover - only for exotic primaries
            screen = self._screen_from_batch(screen_text, previous_translation)
        if not self.glossary:
            return screen
        lines = tuple(self._post(line, "en->pt") for line in screen.lines_pt)
        return replace(screen, text_pt="\n".join(lines), lines_pt=lines)

    def _screen_from_batch(
        self,
        screen_text: str,
        previous_translation: ScreenTranslation | str | None,
    ) -> ScreenTranslation:
        """Assemble a screen from ``to_pt_batch`` (primaries without screens)."""
        source_lines = (screen_text or "").split("\n")
        reuse: dict[str, str] = {}
        if isinstance(previous_translation, ScreenTranslation):
            if len(previous_translation.source_lines) == len(previous_translation.lines_pt):
                reuse = dict(zip(previous_translation.source_lines, previous_translation.lines_pt))
        elif isinstance(previous_translation, str):
            previous_lines = previous_translation.split("\n")
            if len(previous_lines) == len(source_lines):
                reuse = dict(zip(source_lines, previous_lines))
        unresolved = [line for line in source_lines if is_translatable(line) and line not in reuse]
        unique = _unique(unresolved)
        results = self.to_pt_batch(unique)
        by_line = dict(zip(unique, results))
        lines = [reuse.get(line) or by_line.get(line, self.to_pt(line)).text for line in source_lines]
        usage = Usage()
        for translation in by_line.values():
            usage = usage + translation.usage
        return ScreenTranslation(
            text_pt="\n".join(lines),
            lines_pt=tuple(lines),
            source_lines=tuple(source_lines),
            usage=usage,
            latency_ms=sum(t.latency_ms for t in by_line.values()),
            model=getattr(self.primary, "model", ""),
            llm_calls=sum(1 for t in by_line.values() if t.via == "llm"),
            translated_lines=sum(1 for t in by_line.values() if t.via == "llm"),
        )

    # --------------------------------------------------------------------- internals

    def _call_primary(self, text: str, direction: str) -> Translation:
        method = getattr(self.primary, "translate", None)
        if callable(method):
            return method(text, direction)
        if direction == "en->pt":
            return self.primary.to_pt(text)
        return self.primary.to_en(text)

    def _record_error(self, exc: BaseException) -> None:
        """Count one failed call (a batch counts once, not once per line)."""
        self.error_count += 1
        self.last_error = exc

    def _degrade(self, exc: BaseException, text: str, direction: str) -> Translation:
        """Answer with the identity translation of the glossary-processed text."""
        if self.raise_errors:
            raise exc
        source_lang, target_lang = _langs(direction)
        fallback_text = self._post(text, direction)
        fallback_method = getattr(self.fallback, "translate", None)
        if callable(fallback_method):
            try:
                result = fallback_method(fallback_text, direction)
            except Exception:  # pragma: no cover - the fallback must not fail
                result = None
        else:  # pragma: no cover - exotic fallbacks
            result = None
        if result is None:
            return _translation(
                fallback_text,
                source_lang,
                target_lang,
                via=VIA_IDENTITY,
                model=getattr(self.fallback, "model", IDENTITY_MODEL),
            )
        return replace(result, text=fallback_text)

    def _fallback_screen(self, screen_text: str, exc: BaseException) -> ScreenTranslation:
        screen_method = getattr(self.fallback, "translate_screen", None)
        if callable(screen_method):
            try:
                screen = screen_method(screen_text, None)
            except Exception:  # pragma: no cover - defensive
                screen = None
        else:  # pragma: no cover - exotic fallbacks
            screen = None
        if screen is None:  # pragma: no cover - defensive
            lines = (screen_text or "").split("\n")
            return ScreenTranslation(text_pt="\n".join(lines), lines_pt=tuple(lines))
        lines = tuple(self._post(line, "en->pt") for line in screen.lines_pt)
        return replace(screen, text_pt="\n".join(lines), lines_pt=lines)

    def _cache_get(self, direction: str, text: str) -> Translation | None:
        if self.cache is None:
            return None
        return self.cache.get(
            direction,
            text,
            model=getattr(self.primary, "model", ""),
            extra=self._glossary_signature(),
        )

    def _cache_put(self, direction: str, text: str, translation: Translation) -> None:
        if self.cache is None:
            return
        self.cache.put(
            direction,
            text,
            translation,
            model=getattr(self.primary, "model", ""),
            extra=self._glossary_signature(),
        )


def build_translator(
    get_key: Callable[[], str] | None = None,
    *,
    model: str = "deepseek/deepseek-v4.1-flash",
    transport: Any = None,
    glossary: Glossary | None = None,
    cache: Any = None,
    **kwargs: Any,
) -> Translator:
    """Translator for the pluggable slot: identity without a key, chain with one.

    ``get_key`` may be ``None``, may raise, or may return an empty string: in all
    three cases the result is an :class:`IdentityTranslator`, so a missing key
    disables translation instead of breaking the game loop.  With a usable key
    the result is a :class:`ChainTranslator` (glossary → DeepSeek → identity)
    that never raises on a translation error.
    """
    if get_key is None or not _key_available(get_key):
        return IdentityTranslator()
    primary = DeepSeekTranslator(get_key, model=model, transport=transport, **kwargs)
    return ChainTranslator(
        primary,
        fallback=IdentityTranslator(),
        glossary=glossary if glossary is not None else Glossary.load(),
        cache=cache,
    )


# --------------------------------------------------------------------- helpers


def _key_available(get_key: Callable[[], str]) -> bool:
    try:
        key = get_key()
    except Exception:
        return False
    return bool(isinstance(key, str) and key.strip())


def _langs(direction: str) -> tuple[str, str]:
    if direction == "en->pt":
        return "en", "pt"
    if direction == "pt->en":
        return "pt", "en"
    raise ValueError(f"unknown direction {direction!r}; expected 'en->pt' or 'pt->en'")


def _translation(
    text: str,
    source_lang: str,
    target_lang: str,
    *,
    via: str,
    model: str,
) -> Translation:
    return Translation(
        text=text,
        source_lang=source_lang,
        target_lang=target_lang,
        usage=Usage(),
        latency_ms=0,
        model=model,
        via=via,
    )


def _unique(lines: Sequence[str]) -> list[str]:
    seen: dict[str, None] = {}
    for line in lines:
        seen.setdefault(line, None)
    return list(seen)
