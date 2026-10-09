"""Per-campaign translator wiring: the real DeepSeek translator when a key exists.

The contract (``docs/api.md``) says the canonical memory is **EN** and every
``*_pt`` field is a *display* translation.  Until this module existed the app
always used :class:`~system_one_dwarf.campaign.memory.IdentityTranslator`, so
``PATCH .../memory``/``PUT .../objective`` stored Portuguese verbatim and the
``*_pt`` fields mirrored English.

The seam is per **campaign** because the key is: ``POST .../keys`` stores the
OpenRouter key in the campaign's own ``chaves.sqlite``
(:class:`~system_one_dwarf.campaign.keys.KeyStore`).  :class:`CampaignTranslator`
binds a ``KeyStore`` to ``system_one_dwarf.translate.build_translator`` and
resolves lazily, so:

* **no key** → :class:`~system_one_dwarf.campaign.memory.IdentityTranslator`
  (documented behaviour: text is stored as written, no model is called);
* **key present** → the real ``ChainTranslator`` built by
  :func:`system_one_dwarf.translate.build_translator` (glossary → DeepSeek →
  identity), which is best-effort and never raises;
* a key added *after* the app started is picked up on the next call, and a key
  removed later falls back to identity again.

``create_app(translator=...)`` still wins: an explicitly injected translator is
used for every campaign (tests and embedders), which keeps the pre-existing
behaviour byte for byte.

The factory is injectable (``create_app(translator_factory=...)``) so tests can
prove the wiring offline: the factory receives the campaign's ``get_key``
callable and returns a marker translator — zero network.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from ..campaign import IdentityTranslator, KeyStore, Translator

log = logging.getLogger("system_one_dwarf.app.translator")

#: Provider whose key feeds the translator (the same one the LLM uses).
TRANSLATOR_PROVIDER = "openrouter"

TranslatorFactory = Callable[..., Translator]


def default_translator_factory(get_key: Callable[[], str | None], **kwargs: Any) -> Translator:
    """The production factory: ``system_one_dwarf.translate.build_translator``.

    Imported inside the function so that ``create_app()`` (and the whole app
    package) never depends on the translate piece at import time.
    """
    from ..translate import build_translator

    return build_translator(get_key, **kwargs)


class CampaignTranslator:
    """``Translator`` bound to one campaign's :class:`KeyStore`.

    Implements the campaign-layer protocol (``to_english`` / ``to_portuguese``)
    **and** the screen-translation surface the agent loop uses
    (``translate_screen``), always best-effort: a translator problem degrades to
    identity, never to an exception.
    """

    name = "campaign"

    def __init__(
        self,
        key_store: KeyStore,
        *,
        factory: TranslatorFactory | None = None,
        provider: str = TRANSLATOR_PROVIDER,
        **factory_kwargs: Any,
    ) -> None:
        self.key_store = key_store
        self.provider = provider
        self.factory = factory or default_translator_factory
        self.factory_kwargs = dict(factory_kwargs)
        self._delegate: Translator | None = None
        self._configured: bool | None = None
        #: How many real (keyed) translators were built — observability for tests.
        self.builds = 0

    # ------------------------------------------------------------------- keys
    def key(self) -> str | None:
        """The campaign's key, or ``None`` — never raises, never logs material."""
        getter = getattr(self.key_store, "get_key", None)
        if not callable(getter):
            return None
        try:
            value = getter(self.provider)
        except Exception:  # unknown provider / broken sqlite: degrade to identity
            log.warning("key store failed for provider %s; using identity", self.provider,
                        exc_info=True)
            return None
        return value if isinstance(value, str) and value.strip() else None

    @property
    def configured(self) -> bool:
        """True when the campaign currently has a usable key."""
        return self.key() is not None

    # -------------------------------------------------------------- delegate
    def _current(self) -> Translator:
        configured = self.configured
        if self._delegate is None or configured != self._configured:
            self._delegate = self._build(configured)
            self._configured = configured
        return self._delegate

    def _build(self, configured: bool) -> Translator:
        if not configured:
            return IdentityTranslator()
        try:
            built = self.factory(self.key, **self.factory_kwargs)
        except Exception:  # the translate piece is optional: never break the app
            log.warning("translator factory failed; using identity", exc_info=True)
            return IdentityTranslator()
        if built is None:  # a factory may decline (no key by the time it looked)
            return IdentityTranslator()
        self.builds += 1
        return built

    # ------------------------------------------------------------------- API
    def to_english(self, text: str) -> str:
        """PT → EN (best effort; the original text on any failure)."""
        return self._call("to_english", text)

    def to_portuguese(self, text: str) -> str:
        """EN → PT (best effort; the original text on any failure)."""
        return self._call("to_portuguese", text)

    def translate_screen(self, screen_text: str, previous: Any = None) -> Any:
        """EN → PT of the whole screen (the loop's ``screen_translation`` step)."""
        delegate = self._current()
        fn = getattr(delegate, "translate_screen", None)
        if not callable(fn):
            return _identity_screen(screen_text)
        try:
            if previous is None:
                return fn(screen_text)
            try:
                return fn(screen_text, previous)
            except TypeError:  # a translator that takes only the text
                return fn(screen_text)
        except Exception:
            log.warning("screen translation failed; nothing is published", exc_info=True)
            return _identity_screen("")

    def _call(self, method: str, text: str) -> str:
        fn = getattr(self._current(), method, None)
        if not callable(fn):
            return text
        try:
            out = fn(text)
        except Exception:
            log.warning("translator.%s failed; keeping the original text", method, exc_info=True)
            return text
        return out if isinstance(out, str) and out else text

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"CampaignTranslator(provider={self.provider!r}, "
            f"configured={self.configured}, builds={self.builds})"
        )


def _identity_screen(text: str) -> Any:
    """A ``ScreenTranslation``-shaped empty/echo result for the no-key case."""
    try:
        from ..translate import ScreenTranslation

        return ScreenTranslation(text_pt=text or "", lines_pt=tuple((text or "").split("\n")))
    except Exception:  # pragma: no cover - translate piece unavailable
        from types import SimpleNamespace

        return SimpleNamespace(text_pt=text or "", translated_pt=text or "",
                               lines_pt=tuple((text or "").split("\n")))


__all__ = [
    "CampaignTranslator",
    "TRANSLATOR_PROVIDER",
    "TranslatorFactory",
    "default_translator_factory",
]