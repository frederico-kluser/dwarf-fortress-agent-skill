"""DeepSeek chat client used by the narrator and the question formatter.

One client, one endpoint: ``deepseek/deepseek-v4.1-flash`` through the
OpenAI-compatible OpenRouter chat API (``POST /api/v1/chat/completions`` with
``Authorization: Bearer <key>``).  The key is **never** in the code: it arrives
through the ``get_key`` callable, is fetched per request, and never reaches a
log line, an exception message or a ``repr``.

Dynamic reasoning effort (the core of the narrator's cost control)
------------------------------------------------------------------
Reasoning is controlled by the **nested** ``reasoning`` object — the top-level
``reasoning_effort`` field is not used here, and this model's object lists only
``max``/``high``/``low`` as supported efforts, while the JEV-M answer may name
any level of the gateway enum (``max``/``xhigh``/``high``/``medium``/``low``/
``minimal``/``none``).  Every requested level therefore resolves to a
**degradation ladder**: the first rung is what we ask for, each following rung is
one level down, and the ladder always ends at *no* ``reasoning`` parameter.  When
the API rejects a rung (HTTP 400/422, or a 200 carrying an ``error`` body) the
client steps down and retries; **a reasoning parameter can never cost a call**.

============  ==========================  =======================================
JEV-M effort  first request               ladder (one step down per rejection)
============  ==========================  =======================================
``max``       ``{"effort": "max"}``       ``high`` → ``low`` → *(none)*
``xhigh``     ``{"effort": "high"}``      ``low`` → *(none)*
``high``      ``{"effort": "high"}``      ``low`` → *(none)*
``medium``    ``{"effort": "low"}``       *(none)*
``low``       ``{"effort": "low"}``       *(none)*
``minimal``   ``{"enabled": false}``      *(none)*
``none``      ``{"enabled": false}``      *(none)*
``auto``      *(none)*                    *(none)* — leave the API default alone
============  ==========================  =======================================

An explicit ``{"effort": …}`` mapping passed by a caller climbs the same ladder;
any other mapping is sent as-is with the "no parameter" rung as its only
fallback.  ``reasoning=None`` means "send no ``reasoning`` field, no ladder".

Errors (typed, all deriving from :class:`LLMError`)
---------------------------------------------------
======  ==========================  ===============================================
Status  Exception                   Retried?
======  ==========================  ===============================================
401     ``LLMAuthError``            never — terminal (bad or missing API key)
402     ``LLMQuotaError``           never — terminal (no credit left)
400/422 ``LLMRequestError``         never with the same rung; the ladder steps down
429     ``LLMTransportError``       yes (max ``max_attempts``, honouring Retry-After)
5xx     ``LLMTransportError``       yes
network ``LLMTransportError``       yes
200     ``LLMResponseError``        never — the answer carries no usable text
======  ==========================  ===============================================

Spend ledger seam
-----------------
Every successful call feeds ``usage_callback(event)`` with an
:class:`LLMUsageEvent` (provider, model, tokens, cost, latency, purpose,
reasoning, timestamp).  :func:`spend_recorder` adapts
:class:`~system_one_dwarf.campaign.spend.SpendLedger` to that hook; a callback
that raises is logged and swallowed, because accounting must never break the
game loop.
"""

from __future__ import annotations

import inspect
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from ..campaign.clock import utc_now_iso

log = logging.getLogger("system_one_dwarf.agent.llm")

__all__ = [
    "DeepSeekLLM",
    "LLMClient",
    "LLMResult",
    "LLMUsage",
    "LLMUsageEvent",
    "UsageCallback",
    "LLMError",
    "LLMAuthError",
    "LLMQuotaError",
    "LLMRequestError",
    "LLMResponseError",
    "LLMTransportError",
    "reasoning_ladder",
    "reasoning_mode_name",
    "estimate_cost",
    "money",
    "parse_usage",
    "spend_recorder",
    "DEFAULT_MODEL",
    "DEFAULT_ENDPOINT",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_TEMPERATURE",
    "DEFAULT_MAX_ATTEMPTS",
    "REASONING_LEVELS",
    "MODEL_EFFORTS",
    "REASONING_OFF",
    "RETRYABLE_STATUSES",
    "PRICES_USD_PER_MTOK",
]

DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
DEFAULT_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

#: USD per million tokens: (input, output), the contract's model card.
PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    DEFAULT_MODEL: (0.30, 1.20),
}
FALLBACK_PRICES_USD_PER_MTOK: tuple[float, float] = (0.30, 1.20)

DEFAULT_MAX_TOKENS = 1200
DEFAULT_TEMPERATURE = 0.4
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_BASE = 0.5
DEFAULT_BACKOFF_FACTOR = 2.0
DEFAULT_MAX_BACKOFF = 8.0
DEFAULT_MAX_RETRY_AFTER = 30.0
DEFAULT_TIMEOUT = 60.0

#: Transient HTTP statuses worth another attempt.
RETRYABLE_STATUSES: frozenset[int] = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})

#: The closed effort enum the JEV-M ``choice`` can answer with.
REASONING_LEVELS: tuple[str, ...] = ("max", "xhigh", "high", "medium", "low", "minimal", "none")

#: Effort levels this model's ``reasoning`` object actually documents.
MODEL_EFFORTS: tuple[str, ...] = ("max", "high", "low")

#: Cheapest mode: reasoning disabled entirely (used by the translator).
REASONING_OFF: Mapping[str, Any] = {"enabled": False}

#: Statuses whose rejection means "the request itself is wrong": the only ones
#: where stepping the reasoning ladder down can help.
_REASONING_REJECT_STATUSES: frozenset[int] = frozenset({400, 404, 422})

_EFFORT_LADDERS: dict[str, tuple[Mapping[str, Any] | None, ...]] = {
    "max": ({"effort": "max"}, {"effort": "high"}, {"effort": "low"}, None),
    "xhigh": ({"effort": "high"}, {"effort": "low"}, None),
    "high": ({"effort": "high"}, {"effort": "low"}, None),
    "medium": ({"effort": "low"}, None),
    "low": ({"effort": "low"}, None),
    "minimal": (REASONING_OFF, None),
    "none": (REASONING_OFF, None),
}

#: Value of the ``reasoning`` keyword that leaves the API default untouched.
AUTO_REASONING = "auto"


# --------------------------------------------------------------------------- #
# value objects
# --------------------------------------------------------------------------- #
def money(value: float | int | None) -> float:
    """Contract money format: ``float`` in USD with 6 decimals."""
    if value is None:
        return 0.0
    return round(float(value), 6)


def price_for(model: str) -> tuple[float, float]:
    """``(input, output)`` USD per Mtok for ``model`` (fallback when unknown)."""
    return PRICES_USD_PER_MTOK.get(model or "", FALLBACK_PRICES_USD_PER_MTOK)


def estimate_cost(input_tokens: int, output_tokens: int, *, model: str = DEFAULT_MODEL) -> float:
    """Cost in USD from token counts, in the contract's 6-decimal money format."""
    price_in, price_out = price_for(model)
    total = (int(input_tokens or 0) * price_in + int(output_tokens or 0) * price_out) / 1_000_000
    return money(total)


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


@dataclass(frozen=True)
class LLMUsage:
    """Token usage and cost of one completion (provider cost wins)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return int(self.input_tokens) + int(self.output_tokens)

    def __add__(self, other: "LLMUsage") -> "LLMUsage":
        if not isinstance(other, LLMUsage):
            return NotImplemented
        return LLMUsage(
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
class LLMUsageEvent:
    """Payload of the ``usage_callback`` hook (one per successful call)."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    provider: str = "openrouter"
    purpose: str | None = None
    reasoning: str | None = None
    attempts: int = 1
    degraded: int = 0
    ts: str = ""

    @property
    def usage(self) -> LLMUsage:
        return LLMUsage(
            input_tokens=int(self.input_tokens),
            output_tokens=int(self.output_tokens),
            cost_usd=money(self.cost_usd),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "input_tokens": int(self.input_tokens),
            "output_tokens": int(self.output_tokens),
            "cost_usd": money(self.cost_usd),
            "latency_ms": int(self.latency_ms),
            "purpose": self.purpose,
            "reasoning": self.reasoning,
            "attempts": int(self.attempts),
            "degraded": int(self.degraded),
            "ts": self.ts,
        }

    def spend_record_kwargs(self) -> dict[str, Any]:
        """Keyword arguments of ``SpendLedger.record`` (minus provider/model)."""
        return {
            "cost_usd": money(self.cost_usd),
            "input_tokens": int(self.input_tokens),
            "output_tokens": int(self.output_tokens),
            "reason": self.purpose,
            "ts": self.ts,
        }


UsageCallback = Callable[[LLMUsageEvent], Any]


@dataclass(frozen=True)
class LLMResult:
    """One completed chat call: text plus the accounting the UI needs."""

    text: str
    usage: LLMUsage = LLMUsage()
    latency_ms: int = 0
    model: str = ""
    #: Reasoning mode actually used (``"effort:max"``, ``"off"``, ``"default"``…).
    reasoning: str = "default"
    #: The exact ``reasoning`` object sent (``None`` = the field was omitted).
    reasoning_param: Mapping[str, Any] | None = None
    attempts: int = 1
    #: How many times a reasoning rung had to be dropped.
    degraded: int = 0
    #: Raw provider payload (kept for the decision log).
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def cost_usd(self) -> float:
        return money(self.usage.cost_usd)

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "usage": self.usage.as_dict(),
            "latency_ms": int(self.latency_ms),
            "model": self.model,
            "reasoning": self.reasoning,
            "reasoning_param": dict(self.reasoning_param) if self.reasoning_param else None,
            "attempts": int(self.attempts),
            "degraded": int(self.degraded),
        }


# --------------------------------------------------------------------------- #
# errors
# --------------------------------------------------------------------------- #
class LLMError(Exception):
    """Base class for every error raised by the DeepSeek client."""

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
        #: Server-provided explanation (safe to log: never a credential).
        self.detail = detail
        self.attempts = attempts
        self.model = model

    @property
    def terminal(self) -> bool:
        """True when retrying the same call cannot possibly help."""
        return isinstance(self, (LLMAuthError, LLMQuotaError, LLMRequestError, LLMResponseError))


class LLMAuthError(LLMError):
    """Missing/invalid API key (HTTP 401 or an empty ``get_key()``) — terminal."""


class LLMQuotaError(LLMError):
    """HTTP 402 — no credit left; terminal, retrying cannot help."""


class LLMRequestError(LLMError):
    """The request itself is wrong (400/404/422/…): terminal, fix the request."""


class LLMResponseError(LLMError):
    """HTTP 200 whose body carries no usable completion text."""


class LLMTransportError(LLMError):
    """Network failure, timeout, or a retryable status whose retries ran out."""


# --------------------------------------------------------------------------- #
# reasoning ladders
# --------------------------------------------------------------------------- #
def reasoning_ladder(
    reasoning: str | Mapping[str, Any] | None,
    *,
    default: str | Mapping[str, Any] | None = None,
) -> tuple[Mapping[str, Any] | None, ...]:
    """Resolve ``reasoning`` into the descending chain of parameters to try.

    Args:
        reasoning: ``"auto"`` (the API default: no field at all), one of
            :data:`REASONING_LEVELS`, an explicit ``{"effort": …}`` /
            ``{"enabled": …}`` mapping, or ``None`` (no field, no ladder).
        default: what ``"auto"`` resolves to (the client's ``default_reasoning``).

    Returns:
        A non-empty tuple; ``None`` means "send no ``reasoning`` field". The
        first entry is the requested mode and the last is always ``None``
        (except for an explicit ``None`` request, which is a single-rung chain).
    """
    if reasoning is None:
        return (None,)
    if isinstance(reasoning, str):
        value = reasoning.strip().lower()
        if value in ("", AUTO_REASONING):
            return reasoning_ladder(default) if default is not None else (None,)
        if value in _EFFORT_LADDERS:
            return _EFFORT_LADDERS[value]
        # Unknown level: never lose a call over it — send nothing and warn.
        log.warning(
            "unknown reasoning effort %r; sending no reasoning parameter "
            "(expected one of %s)",
            reasoning,
            ", ".join(REASONING_LEVELS),
        )
        return (None,)
    if isinstance(reasoning, Mapping):
        param = dict(reasoning)
        effort = param.get("effort")
        if isinstance(effort, str) and effort.strip().lower() in _EFFORT_LADDERS:
            return _EFFORT_LADDERS[effort.strip().lower()]
        return (param, None)
    raise LLMRequestError(
        f"reasoning must be 'auto', an effort level, a mapping or None, "
        f"got {type(reasoning).__name__}"
    )


def reasoning_mode_name(param: Mapping[str, Any] | None) -> str:
    """Short, loggable name of a ``reasoning`` parameter."""
    if param is None:
        return "default"
    if "effort" in param:
        return f"effort:{param['effort']}"
    if param.get("enabled") is False:
        return "off"
    if param.get("enabled") is True:
        return "on"
    return "custom"


# --------------------------------------------------------------------------- #
# transport plumbing
# --------------------------------------------------------------------------- #
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
    """HTTP-equivalent status of a 200 response carrying an ``error`` body."""
    if not isinstance(payload, Mapping):
        return None
    error = payload.get("error")
    if not error:
        return None
    if isinstance(error, Mapping):
        status = _as_int(error.get("code"))
        if status is not None:
            return status
        text = f"{error.get('type', '')} {error.get('message', '')}".lower()
        if "reasoning" in text or "effort" in text:
            return 400
        if "rate" in text or "limit" in text:
            return 429
        if "quota" in text or "credit" in text or "insufficient" in text:
            return 402
        if "auth" in text or "api key" in text or "unauthor" in text:
            return 401
        return 400
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
    content = message.get("content") if isinstance(message, Mapping) else first.get("text")
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


def parse_usage(payload: Any, model: str = DEFAULT_MODEL) -> LLMUsage:
    """Usage/cost of a chat payload (the provider's ``cost`` wins when present)."""
    if not isinstance(payload, Mapping):
        return LLMUsage()
    raw = payload.get("usage")
    if not isinstance(raw, Mapping):
        return LLMUsage()
    input_tokens = _as_int(raw.get("prompt_tokens"))
    if input_tokens is None:
        input_tokens = _as_int(raw.get("input_tokens"))
    output_tokens = _as_int(raw.get("completion_tokens"))
    if output_tokens is None:
        output_tokens = _as_int(raw.get("output_tokens"))
    input_tokens = int(input_tokens or 0)
    output_tokens = int(output_tokens or 0)
    provider_cost = _as_float(raw.get("cost"))
    if provider_cost is None:
        provider_cost = _as_float(raw.get("total_cost"))
    cost = (
        money(provider_cost)
        if provider_cost is not None
        else estimate_cost(input_tokens, output_tokens, model=model)
    )
    return LLMUsage(input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost)


# --------------------------------------------------------------------------- #
# protocol
# --------------------------------------------------------------------------- #
@runtime_checkable
class LLMClient(Protocol):
    """The narrator/formatter seam: anything that can complete a chat turn.

    :class:`DeepSeekLLM` implements it; tests inject a fake that returns a
    canned :class:`LLMResult` (no network, no key).
    """

    def complete(
        self,
        system: str,
        user: str,
        *,
        reasoning: str | Mapping[str, Any] | None = ...,
        max_tokens: int | None = ...,
        temperature: float | None = ...,
        purpose: str | None = ...,
    ) -> LLMResult:  # pragma: no cover - protocol
        ...


# --------------------------------------------------------------------------- #
# client
# --------------------------------------------------------------------------- #
class DeepSeekLLM:
    """Chat client for ``deepseek/deepseek-v4.1-flash`` through OpenRouter.

    Example (offline-safe: the transport is injected)::

        llm = DeepSeekLLM(get_key, transport=fake_transport)
        result = llm.complete("You narrate.", "RECENT EVENTS: ...", reasoning="high")
        result.text, result.usage.cost_usd

    Args:
        get_key: callable returning the OpenRouter API key, resolved per request.
        model: OpenRouter model id.
        transport: httpx-like object (``post(url, *, headers, json, timeout)``),
            a plain callable, or ``None`` for a lazily created ``httpx.Client``.
        max_tokens / temperature: defaults for :meth:`complete`.
        default_reasoning: what ``reasoning="auto"`` resolves to (default: none).
        usage_callback: called with an :class:`LLMUsageEvent` after every
            successful call (the spend-ledger seam).
        sleep: injectable sleeper for backoff (``None`` resolves
            :func:`time.sleep` at call time, so monkeypatching works).
    """

    endpoint = DEFAULT_ENDPOINT

    def __init__(
        self,
        get_key: Callable[[], str],
        model: str = DEFAULT_MODEL,
        transport: Any = None,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout: float = DEFAULT_TIMEOUT,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
        max_backoff: float = DEFAULT_MAX_BACKOFF,
        max_retry_after: float = DEFAULT_MAX_RETRY_AFTER,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        default_reasoning: str | Mapping[str, Any] | None = None,
        usage_callback: UsageCallback | None = None,
        sleep: Callable[[float], Any] | None = None,
        provider: str = "openrouter",
    ) -> None:
        if not callable(get_key):
            raise LLMRequestError("get_key must be a callable returning the API key")
        if not isinstance(model, str) or not model.strip():
            raise LLMRequestError("model must be a non-empty string")
        if int(max_attempts) < 1:
            raise LLMRequestError(f"max_attempts must be >= 1, got {max_attempts!r}")
        self.get_key = get_key
        self.model = model
        self.provider = provider
        self.timeout = float(timeout)
        self.max_attempts = int(max_attempts)
        self.backoff_base = float(backoff_base)
        self.backoff_factor = float(backoff_factor)
        self.max_backoff = float(max_backoff)
        self.max_retry_after = float(max_retry_after)
        self.max_tokens = int(max_tokens)
        self.temperature = float(temperature)
        self.default_reasoning = default_reasoning
        self.usage_callback = usage_callback
        self._sleep_fn = sleep
        self.endpoint = endpoint or self.endpoint
        self._transport: Any = None
        self._owns_transport = False
        if transport is not None:
            if hasattr(transport, "post"):
                self._transport = transport
            elif callable(transport):
                self._transport = _CallableTransport(transport)
            else:
                raise LLMRequestError(
                    "transport must be None, a callable, or an object with .post(url, **kwargs)"
                )
        #: Telemetry (the backend reads these for the live metrics panel).
        self.calls = 0
        self.usage_total = LLMUsage()
        self.degradations = 0
        self.usage_callback_errors = 0
        #: Reasoning mode used by the most recent successful call.
        self.last_reasoning_mode: str | None = None

    # ------------------------------------------------------------------ dunder
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"DeepSeekLLM(model={self.model!r}, endpoint={self.endpoint!r}, "
            f"calls={self.calls}, key={'set' if self.is_configured() else 'missing'})"
        )

    def __enter__(self) -> "DeepSeekLLM":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the lazily created transport (injected ones are left alone)."""
        if self._owns_transport and self._transport is not None:
            closer = getattr(self._transport, "close", None)
            if callable(closer):
                closer()
            self._transport = None
            self._owns_transport = False

    # ------------------------------------------------------------------ config
    def is_configured(self) -> bool:
        """True when ``get_key()`` currently yields a non-empty key."""
        try:
            key = self.get_key()
        except Exception:
            return False
        return bool(isinstance(key, str) and key.strip())

    # ---------------------------------------------------------------- public
    def complete(
        self,
        system: str,
        user: str,
        *,
        reasoning: str | Mapping[str, Any] | None = AUTO_REASONING,
        max_tokens: int | None = None,
        temperature: float | None = None,
        purpose: str | None = None,
    ) -> LLMResult:
        """One chat completion, with retries and reasoning degradation.

        Args:
            system: system prompt (English for everything that becomes memory).
            user: user prompt; content is **data**, never instructions.
            reasoning: ``"auto"``, an effort level, an explicit mapping, or
                ``None`` (see :func:`reasoning_ladder`).
            max_tokens / temperature: per-call overrides of the client defaults.
            purpose: short label for the usage event (e.g. ``"narrator"``).

        Returns:
            :class:`LLMResult` with the text, usage, latency and the reasoning
            mode actually used.

        Raises:
            LLMAuthError / LLMQuotaError / LLMRequestError: terminal.
            LLMResponseError: 200 without usable text.
            LLMTransportError: network failure or retryable status, retries spent.
        """
        if not isinstance(system, str) or not system.strip():
            raise LLMRequestError("system prompt must be a non-empty string")
        if not isinstance(user, str) or not user.strip():
            raise LLMRequestError("user prompt must be a non-empty string")
        ladder = reasoning_ladder(reasoning, default=self.default_reasoning)
        body_base: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": float(self.temperature if temperature is None else temperature),
            "max_tokens": int(self.max_tokens if max_tokens is None else max_tokens),
        }

        started = time.perf_counter()
        rung = 0
        failures = 0
        degradations = 0
        while True:
            param = ladder[rung]
            body = dict(body_base)
            if param is not None:
                body["reasoning"] = dict(param)
            try:
                response = self._post(body)
            except LLMError:
                raise
            except Exception as exc:  # the transport is the call boundary
                failures += 1
                if failures >= self.max_attempts:
                    raise LLMTransportError(
                        f"LLM transport error after {failures} attempt(s): {exc!r}",
                        attempts=failures,
                        model=self.model,
                    ) from exc
                self._sleep(self._backoff_delay(failures, None))
                continue

            status = getattr(response, "status_code", None)
            if not isinstance(status, int):
                raise LLMTransportError(
                    f"transport returned a response without a status code: {response!r}",
                    model=self.model,
                )
            payload = _json_payload(response)
            detail = _error_detail(response)

            if status == 200:
                body_status = _error_status_from_body(payload)
                if body_status is None:
                    return self._finish(
                        payload,
                        response,
                        started,
                        param=param,
                        attempts=failures + 1,
                        degradations=degradations,
                        purpose=purpose,
                    )
                status, detail = body_status, detail or "error body in a 200 response"

            if status in _REASONING_REJECT_STATUSES and rung < len(ladder) - 1:
                # The API rejected this reasoning rung: step one level down and
                # retry. A reasoning parameter must never cost a call.
                rung += 1
                degradations += 1
                self.degradations += 1
                log.debug(
                    "reasoning %s rejected (HTTP %s); falling back to %s",
                    reasoning_mode_name(param),
                    status,
                    reasoning_mode_name(ladder[rung]),
                )
                continue

            if status == 401:
                raise LLMAuthError(
                    "LLM request rejected with HTTP 401 "
                    "(missing or invalid OpenRouter API key)"
                    + (f": {detail}" if detail else ""),
                    status_code=status,
                    detail=detail,
                    attempts=failures + 1,
                    model=self.model,
                )
            if status == 402:
                raise LLMQuotaError(
                    "LLM request rejected with HTTP 402 (no credit on the account); "
                    "terminal - retrying cannot help" + (f": {detail}" if detail else ""),
                    status_code=status,
                    detail=detail,
                    attempts=failures + 1,
                    model=self.model,
                )
            if status in RETRYABLE_STATUSES or 500 <= status < 600:
                failures += 1
                if failures >= self.max_attempts:
                    raise LLMTransportError(
                        f"LLM request failed with HTTP {status} after {failures} attempt(s)"
                        + (f": {detail}" if detail else ""),
                        status_code=status,
                        detail=detail,
                        attempts=failures,
                        model=self.model,
                    )
                retry_after = _parse_retry_after(_header(response, "Retry-After"))
                self._sleep(self._backoff_delay(failures, retry_after))
                continue
            raise LLMRequestError(
                f"LLM request rejected with HTTP {status}" + (f": {detail}" if detail else ""),
                status_code=status,
                detail=detail,
                attempts=failures + 1,
                model=self.model,
            )

    # ------------------------------------------------------------- internals
    def _get_transport(self) -> Any:
        if self._transport is None:
            import httpx  # local import: only needed for the real transport

            self._transport = httpx.Client(timeout=self.timeout)
            self._owns_transport = True
        return self._transport

    def _api_key(self) -> str:
        try:
            key = self.get_key()
        except Exception as exc:
            raise LLMAuthError(
                f"no OpenRouter API key available for {self.model}", model=self.model
            ) from exc
        if not isinstance(key, str) or not key.strip():
            raise LLMAuthError(
                f"no OpenRouter API key configured for {self.model}", model=self.model
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
            raise LLMTransportError(f"transport {transport!r} has no post() method")
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

    def _finish(
        self,
        payload: Any,
        response: Any,
        started: float,
        *,
        param: Mapping[str, Any] | None,
        attempts: int,
        degradations: int,
        purpose: str | None,
    ) -> LLMResult:
        content = _completion_content(payload)
        if content is None:
            raise LLMResponseError(
                "LLM response carries no completion content"
                + (f": {_error_detail(response) or ''}" if _response_text(response) else ""),
                model=self.model,
                attempts=attempts,
            )
        text = content.strip()
        if not text:
            raise LLMResponseError(
                "LLM response carries empty completion text",
                model=self.model,
                attempts=attempts,
            )
        usage = parse_usage(payload, self.model)
        model = self.model
        if isinstance(payload, Mapping):
            reported = payload.get("model")
            if isinstance(reported, str) and reported:
                model = reported
        latency_ms = int(round((time.perf_counter() - started) * 1000))
        mode = reasoning_mode_name(param)

        self.calls += 1
        self.usage_total = self.usage_total + usage
        self.last_reasoning_mode = mode
        result = LLMResult(
            text=text,
            usage=usage,
            latency_ms=latency_ms,
            model=model,
            reasoning=mode,
            reasoning_param=dict(param) if param is not None else None,
            attempts=attempts,
            degraded=degradations,
            raw=dict(payload) if isinstance(payload, Mapping) else {},
        )
        self._emit_usage(
            LLMUsageEvent(
                model=model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=usage.cost_usd,
                latency_ms=latency_ms,
                provider=self.provider,
                purpose=purpose,
                reasoning=mode,
                attempts=attempts,
                degraded=degradations,
                ts=utc_now_iso(),
            )
        )
        return result

    def _emit_usage(self, event: LLMUsageEvent) -> None:
        callback = self.usage_callback
        if callback is None:
            return
        try:
            callback(event)
        except Exception:  # accounting must never break the game loop
            self.usage_callback_errors += 1
            log.warning("usage_callback failed; call accounting skipped", exc_info=True)


def spend_recorder(
    ledger: Any,
    *,
    provider: str = "openrouter",
    reason: str | None = None,
) -> UsageCallback:
    """Adapt a ``SpendLedger``-like object into a ``usage_callback``.

    The callback records every successful call::

        llm = DeepSeekLLM(get_key, usage_callback=spend_recorder(ledger))

    ``ledger`` only needs ``record(provider, model, *, cost_usd, input_tokens,
    output_tokens, reason=None, ts=None)`` — the signature of
    :meth:`system_one_dwarf.campaign.spend.SpendLedger.record`.
    """

    def record(event: LLMUsageEvent) -> None:
        ledger.record(
            provider,
            event.model,
            cost_usd=money(event.cost_usd),
            input_tokens=int(event.input_tokens),
            output_tokens=int(event.output_tokens),
            reason=reason or event.purpose,
            ts=event.ts,
        )

    return record