"""Tradução PT↔EN do System One Dwarf (DeepSeek v4.1-flash via OpenRouter).

Two consumers, one slot:

* **Screen text EN→PT** — ``deepseek/deepseek-v4.1-flash`` translates the game
  screen for the UI in real time (the original is never shown):
  :meth:`DeepSeekTranslator.translate_screen` deduplicates unchanged lines,
  skips pure numbers/punctuation, caches repeats and batches the rest into a
  single call.
* **User input PT→EN** — objective, information and memory edits are translated
  into English before they enter any model prompt (``to_en``).

The backend depends on the :class:`Translator` protocol only (``to_pt``,
``to_en``); :class:`BatchTranslator` adds ``to_pt_batch``, and
``translate_screen`` exists on both implementations.  When no OpenRouter key is
configured, :class:`IdentityTranslator` (zero cost, never raises) keeps the game
loop alive; :func:`build_translator` picks between them, and
:class:`ChainTranslator` adds the deterministic glossary plus error degradation.

Quick start::

    from system_one_dwarf.translate import build_translator

    translator = build_translator(get_key)              # identity when no key
    screen = translator.translate_screen(screen_text, previous)
    payload = {"translated_pt": screen.translated_pt}   # docs/api.md field

Nothing in this package holds an API key: the key arrives per call through
``get_key()`` and is only ever placed in the Authorization header.
"""

from __future__ import annotations

from .cache import TranslationCache, cache_key
from .fallback import (
    GLOSSARY_MODEL,
    IDENTITY_MODEL,
    ChainTranslator,
    IdentityTranslator,
    build_translator,
)
from .glossary import (
    DEFAULT_GLOSSARY_RELPATH,
    Glossary,
    GlossaryEntry,
    default_glossary_path,
)
from .translator import (
    DEFAULT_ENDPOINT,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_TEMPERATURE,
    PRICES_USD_PER_MTOK,
    REASONING_LADDER,
    RETRYABLE_STATUSES,
    BatchTranslator,
    DeepSeekTranslator,
    ScreenTranslation,
    TextTranslator,
    Translation,
    TranslationAuthError,
    TranslationError,
    TranslationQuotaError,
    TranslationRequestError,
    TranslationResponseError,
    TranslationTransportError,
    Translator,
    Usage,
    clean_translation,
    estimate_cost,
    is_translatable,
    parse_batch_content,
)

__all__ = [
    # core interface
    "Translator",
    "BatchTranslator",
    "TextTranslator",
    "Translation",
    "ScreenTranslation",
    "Usage",
    # implementations
    "DeepSeekTranslator",
    "IdentityTranslator",
    "ChainTranslator",
    "build_translator",
    # supporting pieces
    "Glossary",
    "GlossaryEntry",
    "default_glossary_path",
    "DEFAULT_GLOSSARY_RELPATH",
    "TranslationCache",
    "cache_key",
    # errors
    "TranslationError",
    "TranslationAuthError",
    "TranslationQuotaError",
    "TranslationRequestError",
    "TranslationResponseError",
    "TranslationTransportError",
    # constants / helpers
    "DEFAULT_MODEL",
    "DEFAULT_ENDPOINT",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_TEMPERATURE",
    "PRICES_USD_PER_MTOK",
    "REASONING_LADDER",
    "RETRYABLE_STATUSES",
    "IDENTITY_MODEL",
    "GLOSSARY_MODEL",
    "estimate_cost",
    "is_translatable",
    "clean_translation",
    "parse_batch_content",
]
