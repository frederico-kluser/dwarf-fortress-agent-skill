"""DeepSeek (OpenRouter) translator for the Dwarf Fortress autopilot.

Two jobs, one client:

* **Screen text EN→PT** — Dwarf Fortress screen content is translated for the UI;
  the original is never shown.  This path must be cheap and fast, so it batches a
  whole screen into **one** chat call, deduplicates unchanged lines against the
  previous screen, skips pure numbers/punctuation and serves repeats from an LRU
  cache (see :meth:`DeepSeekTranslator.translate_screen`).
* **User input PT→EN** — objective, free-form information and memory edits are
  translated into English before they enter any model prompt.

Model facts this module is built around (verified):

* ``deepseek/deepseek-v4.1-flash`` through
  ``POST https://openrouter.ai/api/v1/chat/completions`` (OpenAI-compatible),
  ``Authorization: Bearer <key>``, $0.30/Mtok input, $1.20/Mtok output, 1M context.
* Reasoning control lives in the nested ``reasoning`` object.  The cheapest form
  is ``{"reasoning": {"enabled": false}}``; the model's supported efforts are
  ``max``/``high``/``low`` and ``minimal``/``none`` may be rejected.  The client
  therefore walks a **degradation ladder** — ``{"enabled": false}`` → ``{"effort":
  "low"}`` → no parameter at all — and remembers the first mode the API accepted,
  so a rejection costs one wasted round trip *once*, never a lost translation.
* Errors: 429 is retried with backoff honouring ``Retry-After``; 401 and 402 are
  terminal (bad key / no credit); 5xx and network failures are retried; an HTTP
  200 carrying an ``error`` body is treated as the error it is.

The transport is injectable (anything with ``post(url, *, headers, json,
timeout)``, or a plain callable), which is how the whole test suite runs offline.
No API key ever reaches a log, a message or a ``repr``: the key is fetched per
call through ``get_key`` and only ever placed in the Authorization header.
"""

from __future__ import annotations

import inspect
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

try:  # pragma: no cover - import guard, exercised only without httpx installed
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

__all__ = [
    "DeepSeekTranslator",
    "Translation",
    "ScreenTranslation",
    "Usage",
    "Translator",
    "BatchTranslator",
    "TranslationError",
    "TranslationAuthError",
    "TranslationQuotaError",
    "TranslationRequestError",
    "TranslationResponseError",
    "TranslationTransportError",
    "DEFAULT_MODEL",
    "DEFAULT_ENDPOINT",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_TEMPERATURE",
    "REASONING_LADDER",
    "RETRYABLE_STATUSES",
    "PRICES_USD_PER_MTOK",
    "estimate_cost",
    "is_translatable",
    "clean_translation",
    "parse_batch_content",
]

DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
DEFAULT_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

#: USD per million tokens: (input, output).  Used when the API omits ``cost``.
PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    DEFAULT_MODEL: (0.30, 1.20),
}
FALLBACK_PRICES_USD_PER_MTOK: tuple[float, float] = (0.30, 1.20)

DEFAULT_MAX_TOKENS = 2000
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_ATTEMPTS = 3

#: Transient HTTP codes worth another attempt.
RETRYABLE_STATUSES: frozenset[int] = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})

#: Reasoning degradation ladder, cheapest first: (mode name, request parameter).
#: ``None`` means "send no ``reasoning`` field at all".
REASONING_LADDER: tuple[tuple[str, Mapping[str, Any] | None], ...] = (
    ("off", {"enabled": False}),
    ("low", {"effort": "low"}),
    ("default", None),
)

#: Translation ``via`` values (how a Translation was produced).
VIA_LLM = "llm"
VIA_CACHE = "cache"
VIA_SKIPPED = "skipped"
VIA_IDENTITY = "identity"
VIA_GLOSSARY = "glossary"

_FENCE_RE = re.compile(
    r"^\s*```[a-zA-Z0-9_+-]*\s*\n?(?P<body>.*?)\n?\s*```\s*$",
    re.DOTALL,
)
_LABEL_RE = re.compile(
    r"^(?:translation|translated|tradução|traducao|português|portuguese|english|inglês)\s*:\s*",
    re.IGNORECASE,
)
#: A numbered-list line: ``1. text``, ``1) text``, ``1 - text``, ``1: text``.
#: Bullets (``- text``) are deliberately not accepted: an unnumbered list is
#: prose that merely has N lines, which must never be read as a translation.
_NUMBERED_RE = re.compile(r"^\s*(?P<number>\d{1,3})\s*[.)\]:–—-]\s+(?P<text>\S.*)$")
_BATCH_KEYS = ("translations", "translation", "lines", "items", "results", "result", "output", "data")
_BATCH_ITEM_KEYS = ("text", "translation", "translated", "line", "value")


# --------------------------------------------------------------------- errors


class TranslationError(Exception):
    """Base class for every error raised by the translation client."""

    def __init__(
        self,
        message: str = "",
        *,
        status_code: int | None = None,
        detail: Any = None,
        attempts: int | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        #: Server-provided explanation (already safe to log: never a credential).
        self.detail = detail
        self.attempts = attempts
        self.model = model


class TranslationAuthError(TranslationError):
    """Missing/invalid API key (HTTP 401 or an empty ``get_key()``) — terminal."""


class TranslationQuotaError(TranslationError):
    """HTTP 402 — no credit left; terminal, retrying cannot help."""


class TranslationRequestError(TranslationError):
    """The request itself is wrong (400/404/422/…): terminal, fix the request."""


class TranslationResponseError(TranslationError):
    """HTTP 200 whose body carries no usable translation."""


class TranslationTransportError(TranslationError):
    """Network failure, timeout or retryable HTTP status exhausted retries."""


# ----------------------------------------------------------------------- value


def money(value: float | int | None) -> float:
    """Project money contract: ``float`` in USD with 6 decimals."""
    if value is None:
        return 0.0
    return round(float(value), 6)


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return None
    return None


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def price_for(model: str) -> tuple[float, float]:
    """(input, output) USD per Mtok for ``model`` (defaults for unknown models)."""
    return PRICES_USD_PER_MTOK.get(model or "", FALLBACK_PRICES_USD_PER_MTOK)


def estimate_cost(input_tokens: int, output_tokens: int, *, model: str = DEFAULT_MODEL) -> float:
    """Cost in USD from token counts, rounded to the project's 6 decimals."""
    price_in, price_out = price_for(model)
    total = (int(input_tokens or 0) * price_in + int(output_tokens or 0) * price_out) / 1_000_000
    return money(total)


@dataclass(frozen=True)
class Usage:
    """Token usage and cost of one translation call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return int(self.input_tokens) + int(self.output_tokens)

    def __add__(self, other: "Usage") -> "Usage":
        if not isinstance(other, Usage):
            return NotImplemented
        return Usage(
            input_tokens=int(self.input_tokens) + int(other.input_tokens),
            output_tokens=int(self.output_tokens) + int(other.output_tokens),
            cost_usd=money(self.cost_usd + other.cost_usd),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": int(self.input_tokens),
            "output_tokens": int(self.output_tokens),
            "cost_usd": money(self.cost_usd),
        }


@dataclass(frozen=True)
class Translation:
    """One translated text, with the accounting the UI needs.

    ``via`` says how the text was produced: ``"llm"`` (a real call), ``"cache"``
    (served from the LRU, therefore zero usage), ``"skipped"`` (nothing to
    translate: blank/punctuation-only), ``"identity"`` (fallback translator) or
    ``"glossary"`` (deterministic term-only translation).
    """

    text: str
    source_lang: str
    target_lang: str
    usage: Usage = Usage()
    latency_ms: int = 0
    model: str = ""
    via: str = VIA_LLM

    @property
    def cached(self) -> bool:
        """True when the text came from the cache (zero cost)."""
        return self.via == VIA_CACHE

    @property
    def cost_usd(self) -> float:
        return money(self.usage.cost_usd)

    @property
    def input_tokens(self) -> int:
        return int(self.usage.input_tokens)

    @property
    def output_tokens(self) -> int:
        return int(self.usage.output_tokens)

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "usage": self.usage.as_dict(),
            "latency_ms": int(self.latency_ms),
            "model": self.model,
            "via": self.via,
        }


@dataclass(frozen=True)
class ScreenTranslation:
    """A whole screen translated line by line.

    ``translated_pt`` is the name used by ``docs/api.md`` for the screen payload
    (``GET /api/campaigns/{id}/screen`` → ``{"translated_pt": …}``);
    ``display_pt`` and ``text_pt`` are aliases of the same string.
    """

    text_pt: str
    lines_pt: tuple[str, ...] = ()
    source_lines: tuple[str, ...] = ()
    usage: Usage = Usage()
    latency_ms: int = 0
    model: str = ""
    llm_calls: int = 0
    translated_lines: int = 0
    cached_lines: int = 0
    reused_lines: int = 0
    skipped_lines: int = 0

    @property
    def translated_pt(self) -> str:
        return self.text_pt

    @property
    def display_pt(self) -> str:
        return self.text_pt

    @property
    def cost_usd(self) -> float:
        return money(self.usage.cost_usd)

    def as_dict(self) -> dict[str, Any]:
        return {
            "translated_pt": self.text_pt,
            "lines_pt": list(self.lines_pt),
            "usage": self.usage.as_dict(),
            "latency_ms": int(self.latency_ms),
            "model": self.model,
            "llm_calls": int(self.llm_calls),
            "translated_lines": int(self.translated_lines),
            "cached_lines": int(self.cached_lines),
            "reused_lines": int(self.reused_lines),
            "skipped_lines": int(self.skipped_lines),
        }


# ------------------------------------------------------------------- protocols


@runtime_checkable
class Translator(Protocol):
    """Minimal pluggable translator the backend types against.

    Two methods, both returning :class:`Translation`; an implementation never
    returns ``None`` and (in the identity implementation) never raises.
    """

    def to_pt(self, text_en: str) -> Translation:
        """Translate English text into Portuguese."""

    def to_en(self, text_pt: str) -> Translation:
        """Translate Portuguese text into English."""


@runtime_checkable
class BatchTranslator(Translator, Protocol):
    """Additive extension: a translator that can batch several lines at once."""

    def to_pt_batch(self, lines: Sequence[str]) -> list[Translation]:
        """Translate several English lines, 1:1 and in order."""


@runtime_checkable
class TextTranslator(Protocol):
    """The ``str -> str`` seam used by ``campaign.memory`` and ``campaign.objective``.

    Those modules define their own runtime-checkable ``Translator`` protocol with
    exactly these two methods and call them through ``safe_translate()``, which
    keeps the input text when the call raises or returns an empty string.  Every
    implementation in this package provides them **best effort**: they unwrap the
    :class:`Translation` text and never raise, so wiring a real translator into
    ``MemoryStore``/``ObjectiveStore`` cannot break the campaign.
    """

    def to_english(self, text: str) -> str:
        """Portuguese → English (canonical form stored in memory)."""

    def to_portuguese(self, text: str) -> str:
        """English → Portuguese (the ``*_pt`` fields shown in the UI)."""


# -------------------------------------------------------------------- helpers


def is_translatable(line: str) -> bool:
    """False for lines with no letters at all (blank, numbers, box drawing)."""
    return any(char.isalpha() for char in (line or ""))


def clean_translation(raw: str, source: str = "") -> str:
    """Strip model chatter (fences, "Translation:" labels, wrapping quotes)."""
    text = (raw or "").strip()
    if not text:
        return ""
    fenced = _FENCE_RE.match(text)
    if fenced is not None:
        text = fenced.group("body").strip()
    labelled = _LABEL_RE.match(text)
    if labelled is not None and len(text) > labelled.end():
        text = text[labelled.end() :].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'`“”‘’«»":
        # Only unwrap when the source was not itself quoted.
        if not (source and len(source) >= 2 and source.strip()[0] == source.strip()[-1]):
            text = text[1:-1].strip()
    return text


def parse_batch_content(raw: str, expected: int) -> list[str] | None:
    """Parse a batched answer into exactly ``expected`` strings, or ``None``.

    Accepts a bare JSON array, a JSON object wrapping one (``{"translations":
    [...]}``), a JSON array of objects carrying the text under a known key, an
    index-annotated array (sorted by index) and, as a last resort, a **real**
    numbered plain-text list (every line marked ``1.``/``1)``/``1 -``/``1:`` and
    numbered ``1…N`` in order — plain prose with N lines is *not* accepted, see
    :func:`_parse_numbered_lines`).  Anything else — wrong length included —
    returns ``None`` so the caller can fall back to per-line calls.
    """
    if expected <= 0:
        return []
    text = (raw or "").strip()
    if not text:
        return None
    payload: Any = None
    fenced = _FENCE_RE.match(text)
    candidates = [fenced.group("body").strip() if fenced is not None else text]
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        items = _batch_items(payload)
        if items is not None:
            # 1:1 by index is the protocol: a wrong length is a failed batch.
            return items if len(items) == expected else None
    return _parse_numbered_lines(text, expected)


def _batch_items(payload: Any) -> list[str] | None:
    """Normalise a decoded JSON payload into a list of strings (or ``None``)."""
    if isinstance(payload, Mapping):
        for key in _BATCH_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                payload = value
                break
        else:
            return None
    if not isinstance(payload, list):
        return None
    if payload and all(isinstance(item, Mapping) for item in payload):
        if any(_index_of(item) is not None for item in payload):
            try:
                ordered = sorted(payload, key=lambda item: _index_of(item))
            except TypeError:  # pragma: no cover - defensive
                ordered = payload
            payload = ordered
    out: list[str] = []
    for item in payload:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, Mapping):
            for key in _BATCH_ITEM_KEYS:
                value = item.get(key)
                if isinstance(value, str):
                    out.append(value)
                    break
            else:
                return None
        elif item is None:
            out.append("")
        else:
            out.append(str(item))
    return out


def _index_of(item: Mapping[str, Any]) -> int | None:
    for key in ("index", "i", "idx", "id"):
        if key in item:
            return _as_int(item.get(key))
    return None


def _parse_numbered_lines(text: str, expected: int) -> list[str] | None:
    """A plain-text answer accepted **only** as a real, ordered numbered list.

    Every non-empty line must carry an explicit numeric marker (``1.``, ``1)``,
    ``1 -``, ``1:``) and the numbers must run ``1…N`` in order.  Plain prose that
    merely happens to have N lines — a model apology, an error message, a line
    per sentence — is **not** a numbered list: accepting it would put model
    chatter on the user's screen as if it were a translation, so it returns
    ``None`` and the caller falls back to per-line calls.
    """
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if len(lines) != expected:
        return None
    out: list[str] = []
    for position, line in enumerate(lines, start=1):
        match = _NUMBERED_RE.match(line)
        if match is None:
            return None
        if int(match.group("number")) != position:
            return None
        body = match.group("text").strip()
        if not body:
            return None
        out.append(body)
    return out


def _header(response: Any, name: str) -> str | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if callable(getter):
        for key in (name, name.lower(), name.upper(), name.title()):
            try:
                value = getter(key)
            except Exception:  # pragma: no cover - defensive
                value = None
            if value:
                return str(value)
    try:
        for key, value in dict(headers).items():
            if str(key).lower() == name.lower():
                return str(value)
    except Exception:  # pragma: no cover - defensive
        return None
    return None


def _response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    content = getattr(response, "content", None)
    if isinstance(content, (bytes, bytearray)):
        return bytes(content).decode("utf-8", "replace")
    parser = getattr(response, "json", None)
    if callable(parser):
        try:
            return json.dumps(parser())
        except Exception:  # pragma: no cover - defensive
            return ""
    return ""


def _json_payload(response: Any) -> Any:
    parser = getattr(response, "json", None)
    if callable(parser):
        try:
            return parser()
        except Exception:
            pass
    text = _response_text(response)
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _error_detail(response: Any) -> str | None:
    payload = _json_payload(response)
    if isinstance(payload, Mapping):
        detail = payload.get("error") or payload.get("detail") or payload.get("message")
        if isinstance(detail, Mapping):
            detail = detail.get("message") or detail.get("detail") or detail.get("code")
        if detail is not None:
            return str(detail)[:300]
    text = _response_text(response)
    return text[:300] if text else None


def _error_status_from_body(payload: Any) -> int | None:
    """HTTP-equivalent status carried by a 200 response with an ``error`` body."""
    if not isinstance(payload, Mapping):
        return None
    error = payload.get("error")
    if not error:
        return None
    if isinstance(error, Mapping):
        code = error.get("code")
        status = _as_int(code)
        if status is None:
            text = f"{error.get('type', '')} {error.get('message', '')}".lower()
            if "rate" in text or "limit" in text:
                return 429
            if "quota" in text or "credit" in text or "insufficient" in text:
                return 402
            if "auth" in text or "api key" in text or "unauthor" in text:
                return 401
        return status
    return None


def _parse_retry_after(value: Any) -> float | None:
    """Parse ``Retry-After`` (seconds or HTTP-date) into non-negative seconds."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, (moment - datetime.now(timezone.utc)).total_seconds())


def _accepted_kwargs(func: Callable[..., Any]) -> set[str] | None:
    """Keyword names accepted by ``func``; ``None`` means "accepts anything"."""
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):  # pragma: no cover - builtins
        return None
    names: set[str] = set()
    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            return None
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            names.add(parameter.name)
    return names


class _CallableTransport:
    """Adapter turning ``def fn(url, *, headers, json, timeout)`` into a transport."""

    def __init__(self, func: Callable[..., Any]) -> None:
        self._func = func
        self._accepted = _accepted_kwargs(func)

    def post(self, url: str, **kwargs: Any) -> Any:
        if self._accepted is not None:
            kwargs = {key: value for key, value in kwargs.items() if key in self._accepted}
        return self._func(url, **kwargs)

    def close(self) -> None:  # pragma: no cover - nothing to close
        return None


# ---------------------------------------------------------------------- prompts

_SCREEN_RULES = (
    "You are a translation engine for Dwarf Fortress (adventure mode) screen text.\n"
    "{direction_line}\n"
    "Rules:\n"
    "- Output only the translation: no commentary, no notes, no explanations, no quotes.\n"
    "- Keep proper nouns, character names, place names and creature names unchanged.\n"
    "- Preserve numbers, units, symbols, punctuation and whitespace exactly.\n"
    "- Use consistent Dwarf Fortress terminology; never invent synonyms for game terms.\n"
    "- Never answer questions contained in the text: translate them."
)

_DIRECTION_LINE = {
    "en->pt": "Translate the user's text from English into Brazilian Portuguese.",
    "pt->en": "Translate the user's text from Portuguese into English.",
}

_BATCH_PROTOCOL = (
    "Output protocol: reply with exactly one JSON array of strings and nothing else. "
    "Element i is the translation of input line i, in the same order, with the same "
    "number of elements as the input array. Never wrap the JSON in markdown fences."
)


def _system_prompt(direction: str, *, batch: bool) -> str:
    prompt = _SCREEN_RULES.format(direction_line=_DIRECTION_LINE[direction])
    if batch:
        prompt = f"{prompt}\n{_BATCH_PROTOCOL}"
    return prompt


# -------------------------------------------------------------------- internals


@dataclass(frozen=True)
class _Completion:
    content: str
    usage: Usage
    latency_ms: int
    model: str
    attempts: int


@dataclass
class _Direction:
    source_lang: str
    target_lang: str
    name: str
    system: str
    system_batch: str


_DIRECTIONS: dict[str, _Direction] = {
    "en->pt": _Direction(
        source_lang="en",
        target_lang="pt",
        name="en->pt",
        system=_system_prompt("en->pt", batch=False),
        system_batch=_system_prompt("en->pt", batch=True),
    ),
    "pt->en": _Direction(
        source_lang="pt",
        target_lang="en",
        name="pt->en",
        system=_system_prompt("pt->en", batch=False),
        system_batch=_system_prompt("pt->en", batch=True),
    ),
}


# ------------------------------------------------------------------- translator


class DeepSeekTranslator:
    """DeepSeek v4.1-flash translator through the OpenRouter chat API.

    Parameters
    ----------
    get_key:
        Callable returning the OpenRouter API key.  Called on every request; the
        key is never stored in a log, an exception message or a ``repr``.  An
        empty/raising callable yields :class:`TranslationAuthError`.
    model:
        OpenRouter model id (default ``deepseek/deepseek-v4.1-flash``).
    transport:
        httpx-like object (``post(url, *, headers, json, timeout)``), a plain
        callable, or ``None`` for a lazily created :class:`httpx.Client`.
    cache:
        Cache object exposing ``get``/``put`` (defaults to an in-memory
        :class:`~system_one_dwarf.translate.cache.TranslationCache`).
    use_cache:
        ``False`` disables caching entirely (``cache`` wins when both are given).
    reasoning:
        ``True`` starts the degradation ladder at ``{"enabled": false}``;
        ``"low"`` starts at ``{"effort": "low"}``; ``False``/``"none"`` sends no
        ``reasoning`` field at all.
    sleep:
        Injectable sleep used for backoff (tests monkeypatch it; default
        :func:`time.sleep` resolved at call time).
    """

    def __init__(
        self,
        get_key: Callable[[], str],
        model: str = DEFAULT_MODEL,
        transport: Any = None,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout: float = 30.0,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_base: float = 0.5,
        backoff_factor: float = 2.0,
        max_backoff: float = 8.0,
        max_retry_after: float = 30.0,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        cache: Any = None,
        use_cache: bool = True,
        cache_extra: str = "",
        reasoning: bool | str = True,
        sleep: Callable[[float], None] | None = None,
        name: str = "deepseek-translator",
    ) -> None:
        self.get_key = get_key
        self.model = model
        self.endpoint = endpoint
        self.timeout = float(timeout)
        self.max_attempts = max(1, int(max_attempts))
        self.backoff_base = float(backoff_base)
        self.backoff_factor = float(backoff_factor)
        self.max_backoff = float(max_backoff)
        self.max_retry_after = float(max_retry_after)
        self.temperature = float(temperature)
        self.max_tokens = int(max_tokens)
        self.cache_extra = str(cache_extra or "")
        self.name = name
        self._sleep_fn = sleep
        self._transport = transport
        self._owns_transport = transport is None
        if cache is not None:
            self.cache = cache
        elif use_cache:
            from .cache import TranslationCache

            self.cache = TranslationCache()
        else:
            self.cache = None
        self._reasoning_index = _reasoning_index(reasoning)
        #: Name of the first reasoning mode the API accepted (telemetry).
        self.reasoning_mode: str = REASONING_LADDER[self._reasoning_index][0]
        #: How many times a reasoning parameter had to be dropped.
        self.degradations = 0
        #: Batched answers that could not be parsed (they fell back to per line).
        self.batch_parse_failures = 0
        #: Total successful chat calls made by this instance.
        self.calls = 0
        #: Sum of the usage of every successful call.
        self.usage_total = Usage()
        self._last_screen: ScreenTranslation | None = None

    # ------------------------------------------------------------------ dunder

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"DeepSeekTranslator(model={self.model!r}, endpoint={self.endpoint!r}, "
            f"reasoning={self.reasoning_mode!r}, calls={self.calls}, cache={self.cache!r})"
        )

    def __enter__(self) -> "DeepSeekTranslator":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the lazily created transport, if this instance owns one."""
        transport = self._transport
        self._transport = None
        if transport is None:
            return
        closer = getattr(transport, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:  # pragma: no cover - defensive
                pass

    # ------------------------------------------------------------------- public

    def is_configured(self) -> bool:
        """True when ``get_key()`` currently yields a usable (non-empty) key."""
        try:
            key = self.get_key()
        except Exception:
            return False
        return bool(isinstance(key, str) and key.strip())

    def to_pt(self, text_en: str) -> Translation:
        """Translate one English text into Portuguese (single chat call)."""
        return self.translate(text_en, "en->pt")

    def to_english(self, text: str) -> str:
        """PT→EN as a plain string — the ``campaign.memory`` seam; never raises."""
        return self._best_effort_text(text, "pt->en")

    def to_portuguese(self, text: str) -> str:
        """EN→PT as a plain string — the ``campaign.memory`` seam; never raises."""
        return self._best_effort_text(text, "en->pt")

    def _best_effort_text(self, text: str, direction: str) -> str:
        """Translate, and keep the input text whenever anything goes wrong.

        ``campaign.memory.safe_translate`` already guards its call, but the
        contract is the translator's: a missing key, a quota error or a bad
        response must degrade to the original text, never to an exception.
        """
        if not isinstance(text, str) or not text:
            return text
        try:
            return self.translate(text, direction).text or text
        except Exception:
            return text

    def to_en(self, text_pt: str) -> Translation:
        """Translate one Portuguese text into English (single chat call)."""
        return self.translate(text_pt, "pt->en")

    def translate(self, text: str, direction: str) -> Translation:
        """Translate ``text`` in ``direction`` (``"en->pt"`` or ``"pt->en"``)."""
        spec = self._direction(direction)
        if not text or not is_translatable(text):
            # Nothing to translate (blank, pure numbers, box drawing, symbols):
            # asking the model about it would burn tokens for zero information.
            return self._passthrough(text or "", spec, VIA_SKIPPED)
        cached = self._cache_get(spec, text)
        if cached is not None:
            return cached
        completion = self._chat(
            self._messages(spec.system, text),
            max_tokens=self.max_tokens,
            purpose="single",
        )
        result = Translation(
            text=clean_translation(completion.content, text),
            source_lang=spec.source_lang,
            target_lang=spec.target_lang,
            usage=completion.usage,
            latency_ms=completion.latency_ms,
            model=completion.model,
            via=VIA_LLM,
        )
        if not result.text and text.strip():
            raise TranslationResponseError(
                "empty translation in a 200 response",
                model=completion.model,
                attempts=completion.attempts,
            )
        self._cache_put(spec, text, result)
        return result

    def to_pt_batch(self, lines: Sequence[str]) -> list[Translation]:
        """Translate screen lines EN→PT in **one** call, 1:1 by index.

        Blank/punctuation-only lines and cache hits make no call at all.  When
        the batched answer is unusable (malformed JSON, wrong length) the method
        falls back to one call per line rather than losing the screen.  A batch
        that reduces to a single line uses the plain single-line protocol (no
        JSON wrapping), which is both cheaper and immune to quoting problems.

        The batch shares a single API call, so its usage is reported in full on
        the **first** line produced by that call and is zero on the others:
        ``sum(item.usage for item in result)`` is the true total.
        """
        return self._translate_batch_lines(lines, "en->pt")

    def translate_screen(
        self,
        screen_text: str,
        previous_translation: ScreenTranslation | str | None = None,
    ) -> ScreenTranslation:
        """Translate a whole screen EN→PT, cheaply and in real time.

        Savings, in order: lines identical to the previous screen are reused,
        lines already in the cache cost nothing, lines without letters (blank,
        pure numbers, box-drawing punctuation) are passed through untouched, and
        whatever is left is sent as **one** batched call.  Line count and order
        are always preserved, so the UI can align the translation with the game
        screen.
        """
        source_lines = (screen_text or "").split("\n")
        spec = self._direction("en->pt")
        reuse = self._reuse_map(source_lines, previous_translation)
        started = time.perf_counter()
        calls_before = self.calls

        lines_pt: list[str | None] = [None] * len(source_lines)
        pending: list[str] = []
        seen: set[str] = set()
        skipped = reused = 0
        for index, line in enumerate(source_lines):
            if not is_translatable(line):
                lines_pt[index] = line
                skipped += 1
                continue
            if line in reuse:
                lines_pt[index] = reuse[line]
                reused += 1
                continue
            if line not in seen:
                seen.add(line)
                pending.append(line)

        cached_lines = 0
        translated_lines = 0
        usage = Usage()
        model = self.model
        if pending:
            results = self.to_pt_batch(pending)
            by_line: dict[str, Translation] = {}
            for line, translation in zip(pending, results):
                by_line[line] = translation
                usage = usage + translation.usage
                if translation.via == VIA_CACHE:
                    cached_lines += 1
                elif translation.via == VIA_LLM:
                    translated_lines += 1
                if translation.model:
                    model = translation.model
            for index, line in enumerate(source_lines):
                if lines_pt[index] is None:
                    lines_pt[index] = by_line.get(line, self._passthrough(line, spec, VIA_SKIPPED)).text
        resolved = tuple(line if line is not None else "" for line in lines_pt)
        screen = ScreenTranslation(
            text_pt="\n".join(resolved),
            lines_pt=resolved,
            source_lines=tuple(source_lines),
            usage=usage,
            latency_ms=int(round((time.perf_counter() - started) * 1000)),
            model=model,
            llm_calls=self.calls - calls_before,
            translated_lines=translated_lines,
            cached_lines=cached_lines,
            reused_lines=reused,
            skipped_lines=skipped,
        )
        self._last_screen = screen
        return screen

    @property
    def last_screen(self) -> ScreenTranslation | None:
        """Most recent :meth:`translate_screen` result (implicit reuse source)."""
        return self._last_screen

    # -------------------------------------------------------------- batch plumbing

    def _translate_batch_lines(self, lines: Sequence[str], direction: str) -> list[Translation]:
        items = list(lines)
        spec = self._direction(direction)
        results: list[Translation | None] = [None] * len(items)
        pending: list[int] = []
        for index, line in enumerate(items):
            if not is_translatable(line):
                results[index] = self._passthrough(line, spec, VIA_SKIPPED)
                continue
            cached = self._cache_get(spec, line)
            if cached is not None:
                results[index] = cached
                continue
            pending.append(index)

        if len(pending) == 1:
            index = pending[0]
            results[index] = self.translate(items[index], direction)
            return [item if item is not None else self._passthrough("", spec, VIA_SKIPPED) for item in results]
        if pending:
            batch = [items[index] for index in pending]
            parsed = self._batched_call(batch, spec)
            if parsed is None:
                for index in pending:
                    results[index] = self.translate(items[index], direction)
            else:
                texts, usage, latency_ms, model = parsed
                for position, index in enumerate(pending):
                    translation = Translation(
                        text=texts[position],
                        source_lang=spec.source_lang,
                        target_lang=spec.target_lang,
                        usage=usage if position == 0 else Usage(),
                        latency_ms=latency_ms if position == 0 else 0,
                        model=model,
                        via=VIA_LLM,
                    )
                    results[index] = translation
                    self._cache_put(spec, items[index], translation)
        return [
            item if item is not None else self._passthrough("", spec, VIA_SKIPPED) for item in results
        ]

    def _batched_call(
        self,
        batch: Sequence[str],
        spec: _Direction,
    ) -> tuple[list[str], Usage, int, str] | None:
        """One chat call for many lines; ``None`` when the answer is unusable."""
        user = json.dumps(list(batch), ensure_ascii=False)
        completion = self._chat(
            self._messages(spec.system_batch, user),
            max_tokens=self.max_tokens,
            purpose="batch",
        )
        parsed = parse_batch_content(completion.content, len(batch))
        if parsed is None:
            self.batch_parse_failures += 1
            return None
        texts = [
            clean_translation(text, source)
            for text, source in zip(parsed, batch)
        ]
        return texts, completion.usage, completion.latency_ms, completion.model

    # ------------------------------------------------------------------- helpers

    def _direction(self, direction: str) -> _Direction:
        try:
            return _DIRECTIONS[direction]
        except KeyError:
            raise ValueError(
                f"unknown direction {direction!r}; expected 'en->pt' or 'pt->en'"
            ) from None

    @staticmethod
    def _messages(system: str, user: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def _passthrough(self, text: str, spec: _Direction, via: str) -> Translation:
        return Translation(
            text=text,
            source_lang=spec.source_lang,
            target_lang=spec.target_lang,
            usage=Usage(),
            latency_ms=0,
            model=self.model,
            via=via,
        )

    def _reuse_map(
        self,
        source_lines: Sequence[str],
        previous_translation: ScreenTranslation | str | None,
    ) -> dict[str, str]:
        """Source line -> already-known Portuguese line."""
        reuse: dict[str, str] = {}
        candidates: list[ScreenTranslation | str | None] = [previous_translation]
        if previous_translation is None:
            candidates.append(self._last_screen)
        for candidate in candidates:
            if candidate is None:
                continue
            if isinstance(candidate, ScreenTranslation):
                if len(candidate.source_lines) == len(candidate.lines_pt):
                    for source, translated in zip(candidate.source_lines, candidate.lines_pt):
                        reuse.setdefault(source, translated)
                continue
            if isinstance(candidate, str):
                previous_lines = candidate.split("\n")
                if len(previous_lines) == len(source_lines):
                    for source, translated in zip(source_lines, previous_lines):
                        reuse.setdefault(source, translated)
        return reuse

    def _cache_get(self, spec: _Direction, text: str) -> Translation | None:
        if self.cache is None:
            return None
        return self.cache.get(spec.name, text, model=self.model, extra=self.cache_extra)

    def _cache_put(self, spec: _Direction, text: str, translation: Translation) -> None:
        if self.cache is None:
            return
        self.cache.put(
            spec.name,
            text,
            translation,
            model=self.model,
            extra=self.cache_extra,
        )

    # ------------------------------------------------------------------ transport

    def _get_transport(self) -> Any:
        transport = self._transport
        if transport is None:
            if httpx is None:  # pragma: no cover - only without httpx installed
                raise TranslationTransportError(
                    "httpx is required for the default transport; install httpx or "
                    "inject a transport"
                )
            transport = httpx.Client(timeout=self.timeout)
            self._transport = transport
        return transport

    def _api_key(self) -> str:
        try:
            key = self.get_key()
        except Exception as exc:
            raise TranslationAuthError(
                f"no OpenRouter API key available for {self.model}",
                model=self.model,
            ) from exc
        if not isinstance(key, str) or not key.strip():
            raise TranslationAuthError(
                f"no OpenRouter API key configured for {self.model}",
                model=self.model,
            )
        return key.strip()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _post(self, body: Mapping[str, Any]) -> Any:
        transport = self._get_transport()
        post = getattr(transport, "post", None)
        if not callable(post):
            if isinstance(transport, _CallableTransport):  # pragma: no cover - defensive
                post = transport.post
            elif callable(transport):
                transport = _CallableTransport(transport)
                post = transport.post
                self._transport = transport
            else:
                raise TranslationTransportError(
                    f"transport {transport!r} has no post() method"
                )
        kwargs: dict[str, Any] = {"headers": self._headers(), "json": body}
        accepted = _accepted_kwargs(post)
        if accepted is None or "timeout" in accepted:
            kwargs["timeout"] = self.timeout
        return post(self.endpoint, **kwargs)

    def _sleep(self, delay: float) -> None:
        if self._sleep_fn is not None:
            self._sleep_fn(delay)
        else:
            # Resolved at call time on purpose: tests monkeypatch time.sleep.
            time.sleep(delay)

    def _backoff_delay(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(float(retry_after), self.max_retry_after)
        delay = self.backoff_base * (self.backoff_factor ** max(0, attempt - 1))
        return min(float(delay), self.max_backoff)

    # ---------------------------------------------------------------- reasoning

    def reasoning_param(self) -> Mapping[str, Any] | None:
        """Current ``reasoning`` parameter (``None`` = send none at all)."""
        return REASONING_LADDER[self._reasoning_index][1]

    def _can_degrade_reasoning(self) -> bool:
        return (
            self._reasoning_index < len(REASONING_LADDER) - 1
            and self.reasoning_param() is not None
        )

    def _degrade_reasoning(self) -> None:
        self._reasoning_index += 1
        self.reasoning_mode = REASONING_LADDER[self._reasoning_index][0]
        self.degradations += 1

    # --------------------------------------------------------------------- chat

    def _body(self, messages: Sequence[Mapping[str, str]], *, max_tokens: int) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": self.temperature,
            "max_tokens": int(max_tokens),
        }
        reasoning = self.reasoning_param()
        if reasoning is not None:
            body["reasoning"] = dict(reasoning)
        return body

    def _chat(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        max_tokens: int,
        purpose: str,
    ) -> _Completion:
        """One logical completion, with retries and reasoning degradation."""
        started = time.perf_counter()
        failures = 0
        while True:
            body = self._body(messages, max_tokens=max_tokens)
            try:
                response = self._post(body)
            except TranslationError:
                raise
            except Exception as exc:
                failures += 1
                if failures >= self.max_attempts:
                    raise TranslationTransportError(
                        f"translation {purpose} transport error after {failures} "
                        f"attempt(s): {exc!r}",
                        attempts=failures,
                        model=self.model,
                    ) from exc
                self._sleep(self._backoff_delay(failures, None))
                continue

            status = getattr(response, "status_code", None)
            if not isinstance(status, int):
                raise TranslationTransportError(
                    f"transport returned a response without a status code: {response!r}",
                    model=self.model,
                )
            payload = _json_payload(response)
            detail = _error_detail(response)

            if status == 200:
                body_status = _error_status_from_body(payload)
                if body_status is not None:
                    status, detail = body_status, detail or "error body in a 200 response"
                else:
                    return self._parse_completion(response, payload, started)

            if status in (400, 422) and self._can_degrade_reasoning():
                # The API rejected the reasoning parameter: drop it and retry.
                # A reasoning rejection must never cost a translation.
                self._degrade_reasoning()
                continue

            if status == 401:
                raise TranslationAuthError(
                    "translation request rejected with HTTP 401 "
                    "(missing or invalid OpenRouter API key)"
                    + (f": {detail}" if detail else ""),
                    status_code=status,
                    detail=detail,
                    attempts=failures + 1,
                    model=self.model,
                )
            if status == 402:
                raise TranslationQuotaError(
                    "translation request rejected with HTTP 402 (no credit on the account); "
                    "terminal - retrying cannot help" + (f": {detail}" if detail else ""),
                    status_code=status,
                    detail=detail,
                    attempts=failures + 1,
                    model=self.model,
                )
            if status in RETRYABLE_STATUSES or 500 <= status < 600:
                failures += 1
                if failures >= self.max_attempts:
                    raise TranslationTransportError(
                        f"translation {purpose} request failed with HTTP {status} after "
                        f"{failures} attempt(s)" + (f": {detail}" if detail else ""),
                        status_code=status,
                        detail=detail,
                        attempts=failures,
                        model=self.model,
                    )
                retry_after = _parse_retry_after(_header(response, "Retry-After"))
                self._sleep(self._backoff_delay(failures, retry_after))
                continue
            raise TranslationRequestError(
                f"translation request rejected with HTTP {status}"
                + (f": {detail}" if detail else ""),
                status_code=status,
                detail=detail,
                attempts=failures + 1,
                model=self.model,
            )

    def _parse_completion(self, response: Any, payload: Any, started: float) -> _Completion:
        content = _completion_content(payload)
        if content is None:
            raise TranslationResponseError(
                "translation response carries no completion content"
                + (f": {_error_detail(response) or ''}" if _response_text(response) else ""),
                model=self.model,
            )
        usage = parse_usage(payload, self.model)
        model = self.model
        if isinstance(payload, Mapping):
            reported = payload.get("model")
            if isinstance(reported, str) and reported:
                model = reported
        latency_ms = int(round((time.perf_counter() - started) * 1000))
        self.calls += 1
        self.usage_total = self.usage_total + usage
        return _Completion(
            content=content,
            usage=usage,
            latency_ms=latency_ms,
            model=model,
            attempts=1,
        )


# ---------------------------------------------------------------- usage parsing


def _completion_content(payload: Any) -> str | None:
    """Text of the first choice, tolerating content parts and stray wrappers."""
    if not isinstance(payload, Mapping):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, Mapping):
        return None
    message = first.get("message")
    if isinstance(message, Mapping):
        content = message.get("content")
    else:
        content = first.get("text")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, Mapping) and isinstance(part.get("text"), str):
                parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts) if parts else None
    return None


def parse_usage(payload: Any, model: str = DEFAULT_MODEL) -> Usage:
    """Usage/cost from a chat completion payload (provider cost wins)."""
    if not isinstance(payload, Mapping):
        return Usage()
    raw = payload.get("usage")
    if not isinstance(raw, Mapping):
        return Usage()
    input_tokens = _as_int(raw.get("prompt_tokens"))
    if input_tokens is None:
        input_tokens = _as_int(raw.get("input_tokens"))
    output_tokens = _as_int(raw.get("completion_tokens"))
    if output_tokens is None:
        output_tokens = _as_int(raw.get("output_tokens"))
    input_tokens = int(input_tokens or 0)
    output_tokens = int(output_tokens or 0)
    provider_cost = _as_float(raw.get("cost"))
    cost = money(provider_cost) if provider_cost is not None else estimate_cost(
        input_tokens, output_tokens, model=model
    )
    return Usage(input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost)


def _reasoning_index(reasoning: bool | str) -> int:
    if isinstance(reasoning, bool):
        return 0 if reasoning else len(REASONING_LADDER) - 1
    value = str(reasoning or "").strip().lower()
    for index, (name, _param) in enumerate(REASONING_LADDER):
        if name == value:
            return index
    raise ValueError(
        f"unknown reasoning mode {reasoning!r}; expected True/False or one of "
        + ", ".join(name for name, _ in REASONING_LADDER)
    )
