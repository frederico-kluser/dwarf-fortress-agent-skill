"""HTTP client for the Jev fleet (``POST /api/alpha/decisions`` on OpenRouter).

Design constraints that shaped this module:

* **The transport is injectable.** ``transport`` may be an httpx-like object
  (anything with ``post(url, *, headers, json, timeout)``), a plain callable, or
  ``None`` (a lazy :class:`httpx.Client`, reused so DNS+TLS stay warm). Every
  test in this repo injects a fake, so the suite never touches the network.
* **Optional fields are optional.** On the alpha schema only ``type`` plus
  ``choice``/``score``/``noul`` are required: ``confidence``, ``probabilities``
  and ``legend`` may be missing, and ``confidence == 0.0`` is used by fallbacks
  to mean *unavailable* (indistinguishable from a flat distribution). A missing
  or zero confidence is never read as "evenly spread": the answer is capped at
  the ``hitl`` band (see :mod:`system_one_dwarf.jev.bands`).
* **Retries are typed and bounded.** Up to ``max_attempts`` (default 3) on
  429/529, other 5xx, timeouts and network errors, with exponential backoff
  honouring ``Retry-After`` (seconds or HTTP-date). 401/402/422 and any other
  4xx are **terminal**: retrying cannot fix them.
* **The API key is never logged.** Errors carry status codes and server text
  only; ``repr(client)`` masks the key.
* **OpenRouter attribution on every request.** ``HTTP-Referer`` +
  ``X-OpenRouter-Title`` (never ``X-OpenRouter-App``), so the app shows up in
  the OpenRouter rankings; a provider error arriving with HTTP 200 and an
  ``error`` body is mapped and handled like the error it is.

Prices (used to estimate ``usage.cost`` when the payload omits it): $0.042/Mtok
input, output free; a reported ``usage.cost`` always wins, and money is the
project's 6-decimal contract.
"""

from __future__ import annotations

import inspect
import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Mapping, Sequence

from .bands import (
    BAND_HITL,
    BAND_AUTO,
    band_for_choice_or_score,
    band_for_noul,
    cap_band,
    confidence_available,
    validate_thresholds,
)
from .bands import DEFAULT_AUTO_THRESHOLD, DEFAULT_HITL_THRESHOLD
from .errors import (
    JevAuthError,
    JevQuotaError,
    JevRequestError,
    JevTransportError,
)
from .schema import QUESTION_TYPES, Question, normalize_questions
from .validate import MAX_SESSION_ID_CHARS, validate_state_and_questions

__all__ = [
    "JevClient",
    "DecisionResult",
    "Answer",
    "Usage",
    "DEFAULT_MODEL",
    "DEFAULT_ENDPOINT",
    "OPENROUTER_REFERER",
    "OPENROUTER_TITLE",
    "PRICES_USD_PER_MTOK",
    "RETRYABLE_STATUSES",
    "DEFAULT_MAX_ATTEMPTS",
    "estimate_cost",
    "money",
    "price_for",
]

DEFAULT_MODEL = "typesafe/jev-1.13"
DEFAULT_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"

#: OpenRouter attribution on every outbound request (skill gotcha 11:
#: ``HTTP-Referer`` + ``X-OpenRouter-Title`` — never ``X-OpenRouter-App``).
OPENROUTER_REFERER = "https://github.com/frederico-kluser/dwarf-fortress-agent-skill"
OPENROUTER_TITLE = "System One Dwarf"

#: USD per million tokens: (input, output). Output is free on Jev.
PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    DEFAULT_MODEL: (0.042, 0.0),
}
FALLBACK_PRICES_USD_PER_MTOK: tuple[float, float] = (0.042, 0.0)

#: 429/529 are the documented transient codes; the other 5xx are transient too.
RETRYABLE_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504, 529})

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_BASE = 0.5
DEFAULT_BACKOFF_FACTOR = 2.0
DEFAULT_MAX_BACKOFF = 8.0
DEFAULT_MAX_RETRY_AFTER = 30.0
DEFAULT_TIMEOUT = 20.0


# --------------------------------------------------------------------------- #
# normalised result objects
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Usage:
    """Token/cost usage of one decision. Missing fields default to zero."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Answer:
    """One normalised answer.

    ``value`` is the primitive's payload: a float in ``[0, 1]`` for ``noul``, an
    option key for ``choice``, a probability-weighted float for ``score``.
    ``confidence`` is ``None`` whenever the field was absent, non-numeric or
    ``0.0`` (all mean *unavailable*, and ``0.0`` is normalised to ``None`` so it
    can never be read as a real value); ``band`` reflects that — an unavailable
    answer is never ``"auto"``. The untouched server payload stays in ``raw``.
    """

    name: str
    type: str | None
    value: float | str | None
    confidence: float | None
    probabilities: dict[str, float] | None
    legend: dict[str, str] | None
    band: str
    chosen_probability: float | None
    raw: dict[str, Any] = field(default_factory=dict)
    issues: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        """True when the answer has a usable value and (choice/score) confidence.

        ``noul`` never carries a confidence by design, so a present value is
        enough for it.
        """
        if self.value is None:
            return False
        if self.type == "noul":
            return True
        return confidence_available(self.confidence)

    @property
    def certainty(self) -> float | None:
        """``|2p - 1|`` for a ``noul`` answer, ``None`` for the other types."""
        if self.type == "noul" and isinstance(self.value, (int, float)):
            return abs(2.0 * float(self.value) - 1.0)
        return None

    @property
    def probability(self) -> float | None:
        """Probability of the chosen option, when the model reported one."""
        return self.chosen_probability


@dataclass(frozen=True)
class DecisionResult:
    """Everything the caller needs to log, gate and act on one decision.

    ``answers`` holds one entry per **requested** question (a question the model
    did not answer is present with ``value=None`` and band ``hitl``), plus any
    unexpected answer, flagged in ``issues`` and capped at ``hitl``.
    """

    answers: dict[str, Answer]
    usage: Usage
    latency_ms: int
    model: str | None
    raw: dict[str, Any]
    request_id: str | None = None
    provider: str | None = None
    attempts: int = 1
    issues: tuple[str, ...] = ()

    def answer(self, name: str) -> Answer:
        """The :class:`Answer` of a question; raises if the name is unknown."""
        try:
            return self.answers[name]
        except KeyError:
            raise JevRequestError(
                f"no answer named {name!r}; answers: {sorted(self.answers)}"
            ) from None

    def value(self, name: str) -> float | str | None:
        """Shorthand for ``result.answer(name).value``."""
        return self.answer(name).value

    def band(self, name: str) -> str:
        """Shorthand for ``result.answer(name).band``."""
        return self.answer(name).band


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
            kwargs = {k: v for k, v in kwargs.items() if k in self._accepted}
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
    json_method = getattr(response, "json", None)
    if callable(json_method):
        try:
            return json.dumps(json_method())
        except Exception:  # pragma: no cover - defensive
            return ""
    return ""


def _error_detail(response: Any) -> str | None:
    text = _response_text(response)
    if not text:
        return None
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return text[:300]
    if isinstance(payload, Mapping):
        detail = payload.get("detail") or payload.get("error") or payload.get("message")
        if isinstance(detail, Mapping):
            detail = detail.get("message") or detail.get("detail")
        if detail is not None:
            return str(detail)[:300]
    return text[:300]


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


def _error_status_from_body(payload: Any) -> int | None:
    """HTTP-equivalent status of a 200 response carrying an ``error`` body.

    Provider errors do not always arrive with a non-200 status (skill gotcha
    20): the body may hold ``{"error": …}`` with HTTP 200. The mapping is the
    same one the chat clients use — an explicit ``code`` wins, otherwise the
    message is classified (rate limit → 429, credits/quota → 402, auth → 401,
    anything else → 400).
    """
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
    else:
        text = str(error).lower()
    if "rate" in text or "limit" in text:
        return 429
    if "quota" in text or "credit" in text or "insufficient" in text:
        return 402
    if "auth" in text or "api key" in text or "unauthor" in text:
        return 401
    return 400


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


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def _as_int(value: Any) -> int | None:
    number = _as_float(value)
    return None if number is None else int(number)


def money(value: float | int | None) -> float:
    """Project money contract: ``float`` in USD with 6 decimals."""
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


def _as_probabilities(value: Any, problems: list[str] | None = None) -> dict[str, float] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        if problems is not None:
            problems.append("probabilities is not an object; ignored")
        return None
    cleaned: dict[str, float] = {}
    for key, item in value.items():
        number = _as_float(item)
        if number is None:
            if problems is not None:
                problems.append(f"probabilities[{key!r}] is not numeric; ignored")
            continue
        cleaned[str(key)] = number
    return cleaned


def _option_keys(question: Question | None) -> set[str] | None:
    if question is None:
        return None
    criteria = question.get("criteria")
    if isinstance(criteria, Mapping):
        return {str(key) for key in criteria}
    return None


def _levels(question: Question | None) -> list[str] | None:
    if question is None:
        return None
    criteria = question.get("criteria")
    if isinstance(criteria, Sequence) and not isinstance(criteria, (str, bytes)):
        return [str(level) for level in criteria]
    return None


# --------------------------------------------------------------------------- #
# client
# --------------------------------------------------------------------------- #
class JevClient:
    """Typed decision calls to ``typesafe/jev-1.13`` through OpenRouter.

    Example (offline-safe, transport injected)::

        client = JevClient(api_key, transport=my_fake_transport)
        result = client.decide("state: ...", {"is_bug": noul(...)})
        if result.band("is_bug") == "auto":
            act(result.value("is_bug"))

    Args:
        api_key: OpenRouter key, injected at runtime by the campaign layer.
            Never hardcode it and never log it.
        model: model id sent in the body.
        transport: httpx-like object, plain callable, or ``None`` for a lazy
            ``httpx.Client`` (reused across calls to keep the socket warm).
        timeout: per-request timeout in seconds.
        max_attempts: total HTTP attempts (1 = no retry).
        backoff_base / backoff_factor / max_backoff: exponential backoff used
            when the server does not send ``Retry-After``.
        max_retry_after: ceiling for a server-provided ``Retry-After`` so a bad
            header cannot stall the game loop.
        sleep: injection point for the sleeper (tests record values); ``None``
            resolves ``time.sleep`` at call time, so monkeypatching works.
    """

    endpoint = DEFAULT_ENDPOINT

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        transport: Any = None,
        timeout: float = DEFAULT_TIMEOUT,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
        max_backoff: float = DEFAULT_MAX_BACKOFF,
        max_retry_after: float = DEFAULT_MAX_RETRY_AFTER,
        sleep: Callable[[float], Any] | None = None,
        endpoint: str | None = None,
        auto_threshold: float = DEFAULT_AUTO_THRESHOLD,
        hitl_threshold: float = DEFAULT_HITL_THRESHOLD,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise JevAuthError(
                "an OpenRouter API key is required; inject it at runtime "
                "(the campaign layer holds it) — never hardcode it"
            )
        if not isinstance(model, str) or not model.strip():
            raise JevRequestError("model must be a non-empty string")
        if max_attempts < 1:
            raise JevRequestError(f"max_attempts must be >= 1, got {max_attempts!r}")
        validate_thresholds(auto_threshold, hitl_threshold)
        self._api_key = api_key
        self.model = model
        self.timeout = float(timeout)
        self.max_attempts = int(max_attempts)
        self.backoff_base = float(backoff_base)
        self.backoff_factor = float(backoff_factor)
        self.max_backoff = float(max_backoff)
        self.max_retry_after = float(max_retry_after)
        self.auto_threshold = float(auto_threshold)
        self.hitl_threshold = float(hitl_threshold)
        self._sleep_fn = sleep
        if endpoint:
            self.endpoint = endpoint
        self._owns_transport = False
        if transport is None:
            self._transport: Any = None
        elif hasattr(transport, "post"):
            self._transport = transport
        elif callable(transport):
            self._transport = _CallableTransport(transport)
        else:
            raise JevRequestError(
                "transport must be None, a callable, or an object with .post(url, **kwargs)"
            )

    # -- lifecycle ---------------------------------------------------------- #
    def _get_transport(self) -> Any:
        if self._transport is None:
            import httpx  # local import: only needed for the real transport

            self._transport = httpx.Client(timeout=self.timeout)
            self._owns_transport = True
        return self._transport

    def close(self) -> None:
        """Close the default httpx transport (injected transports are left alone)."""
        if self._owns_transport and self._transport is not None:
            closer = getattr(self._transport, "close", None)
            if callable(closer):
                closer()
            self._transport = None
            self._owns_transport = False

    def __enter__(self) -> "JevClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return (
            f"JevClient(model={self.model!r}, endpoint={self.endpoint!r}, "
            f"api_key={'set' if self._api_key else 'missing'})"
        )

    # -- public API --------------------------------------------------------- #
    def decide(
        self,
        state: Any,
        questions: Mapping[str, Any] | Sequence[Question],
        session_id: str | None = None,
        *,
        validate: bool = True,
        model: str | None = None,
    ) -> DecisionResult:
        """Ask the fleet one request with N questions (evaluated in parallel).

        Args:
            state: string/object/array with the compact English situation.
                Content is **data**, never instructions.
            questions: ``{name: question}`` or a sequence of
                :class:`~system_one_dwarf.jev.schema.Question`.
            session_id: optional trace id (<= 256 chars, observability only).
            validate: run the offline validation first and refuse to spend
                tokens on a request that cannot be right (default ``True``).
            model: per-call model override.

        Returns:
            :class:`DecisionResult` with one normalised :class:`Answer` per
            question, usage, latency, model and the raw payload.

        Raises:
            JevRequestError: invalid request (local validation or HTTP 4xx).
            JevAuthError: HTTP 401 — bad/missing key. Terminal.
            JevQuotaError: HTTP 402 — no credit. Terminal.
            JevTransportError: network error or exhausted retries (429/529/5xx).
        """
        return self._decide(state, questions, session_id, validate=validate, model=model)

    def decide_request(
        self,
        request: Mapping[str, Any],
        session_id: str | None = None,
        *,
        validate: bool = True,
        model: str | None = None,
    ) -> DecisionResult:
        """Convenience wrapper for the dict returned by the fleet builders.

        Reads ``state``, ``questions`` and (unless overridden) ``session_id`` and
        ``model`` from ``request``; ignores bookkeeping keys (``ordering``/``meta``).
        """
        if not isinstance(request, Mapping):
            raise JevRequestError(f"request must be a mapping, got {type(request).__name__}")
        if "state" not in request or "questions" not in request:
            raise JevRequestError("request must contain 'state' and 'questions'")
        if session_id is None:
            session_id = request.get("session_id")
        if model is None:
            model = request.get("model")
        return self._decide(
            request["state"],
            request["questions"],
            session_id,
            validate=validate,
            model=model,
        )

    # -- internals ---------------------------------------------------------- #
    def _decide(
        self,
        state: Any,
        questions: Any,
        session_id: str | None,
        *,
        validate: bool,
        model: str | None,
    ) -> DecisionResult:
        if session_id is not None and not isinstance(session_id, str):
            raise JevRequestError(
                f"session_id must be a string, got {type(session_id).__name__}"
            )
        if isinstance(session_id, str) and len(session_id) > MAX_SESSION_ID_CHARS:
            raise JevRequestError(
                f"session_id is {len(session_id)} chars, the maximum is {MAX_SESSION_ID_CHARS}"
            )

        requested = normalize_questions(questions)
        if validate:
            validate_state_and_questions(state, questions, session_id)

        body: dict[str, Any] = {
            "model": model or self.model,
            "state": state,
            "questions": {name: question.to_body() for name, question in requested},
        }
        if session_id:
            body["session_id"] = session_id

        started = time.monotonic()
        response, attempts = self._post_with_retries(body)
        latency_ms = int(round((time.monotonic() - started) * 1000.0))
        return self._parse_success(
            response,
            requested,
            attempts=attempts,
            latency_ms=latency_ms,
            model_sent=body["model"],
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "HTTP-Referer": OPENROUTER_REFERER,
            "X-OpenRouter-Title": OPENROUTER_TITLE,
        }

    def _post(self, body: Mapping[str, Any]) -> Any:
        transport = self._get_transport()
        post = getattr(transport, "post")
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

    def _post_with_retries(self, body: Mapping[str, Any]) -> tuple[Any, int]:
        attempt = 0
        while attempt < self.max_attempts:
            attempt += 1
            try:
                response = self._post(body)
            except Exception as exc:  # noqa: BLE001 - the call boundary is the transport
                if attempt >= self.max_attempts:
                    raise JevTransportError(
                        f"Jev transport error after {attempt} attempt(s): {exc!r}",
                        attempts=attempt,
                    ) from exc
                self._sleep(self._backoff_delay(attempt, None))
                continue

            status = getattr(response, "status_code", None)
            if not isinstance(status, int):
                raise JevTransportError(
                    f"transport returned a response without a status code: {response!r}"
                )
            detail = _error_detail(response)

            if status == 200:
                # gotcha 20: a provider error can arrive with HTTP 200 and an
                # ``error`` body — it is the error it says it is, not success.
                body_status = _error_status_from_body(_json_payload(response))
                if body_status is None:
                    return response, attempt
                status, detail = body_status, detail or "error body in a 200 response"
            if status == 401:
                raise JevAuthError(
                    "Jev request rejected with HTTP 401 (missing or invalid OpenRouter API key)"
                    + (f": {detail}" if detail else ""),
                    status_code=status,
                    detail=detail,
                    attempts=attempt,
                )
            if status == 402:
                raise JevQuotaError(
                    "Jev request rejected with HTTP 402 (no credit on the account); "
                    "terminal — retrying cannot help"
                    + (f": {detail}" if detail else ""),
                    status_code=status,
                    detail=detail,
                    attempts=attempt,
                )
            if status == 422:
                raise JevRequestError(
                    "Jev request rejected with HTTP 422 (invalid request body); fix the request, "
                    "never retry it blindly" + (f": {detail}" if detail else ""),
                    status_code=status,
                    detail=detail,
                    attempts=attempt,
                )
            if status in RETRYABLE_STATUSES or 500 <= status < 600:
                if attempt >= self.max_attempts:
                    raise JevTransportError(
                        f"Jev request failed with HTTP {status} after {attempt} attempt(s)"
                        + (f": {detail}" if detail else ""),
                        status_code=status,
                        detail=detail,
                        attempts=attempt,
                    )
                retry_after = _parse_retry_after(_header(response, "Retry-After"))
                self._sleep(self._backoff_delay(attempt, retry_after))
                continue
            raise JevRequestError(
                f"Jev request rejected with HTTP {status}" + (f": {detail}" if detail else ""),
                status_code=status,
                detail=detail,
                attempts=attempt,
            )

        raise JevTransportError(  # pragma: no cover - loop always returns or raises
            f"Jev request failed after {attempt} attempt(s)",
            attempts=attempt,
        )

    # -- response parsing --------------------------------------------------- #
    def _parse_success(
        self,
        response: Any,
        requested: list[tuple[str, Question]],
        *,
        attempts: int,
        latency_ms: int,
        model_sent: str | None = None,
    ) -> DecisionResult:
        text = _response_text(response)
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            raise JevRequestError(
                f"malformed decision response: body is not JSON ({text[:200]!r})"
            ) from None
        if not isinstance(payload, Mapping):
            raise JevRequestError(
                f"malformed decision response: expected an object, got {type(payload).__name__}"
            )
        raw_answers = payload.get("answers")
        if not isinstance(raw_answers, Mapping):
            raise JevRequestError(
                "malformed decision response: 'answers' is missing or not an object"
            )

        answers, issues = self._normalise_answers(requested, raw_answers)
        usage, usage_issues = _normalise_usage(payload)
        issues.extend(usage_issues)

        return DecisionResult(
            answers=answers,
            usage=usage,
            latency_ms=latency_ms,
            model=payload.get("model") or model_sent or self.model,
            raw=dict(payload),
            request_id=payload.get("id"),
            provider=payload.get("provider"),
            attempts=attempts,
            issues=tuple(issues),
        )

    def _normalise_answers(
        self,
        requested: list[tuple[str, Question]],
        raw_answers: Mapping[str, Any],
    ) -> tuple[dict[str, Answer], list[str]]:
        answers: dict[str, Answer] = {}
        issues: list[str] = []
        known = {name for name, _ in requested}

        for name, question in requested:
            raw = raw_answers.get(name)
            if raw is None:
                problem = f"question {name!r} has no answer in the response"
                issues.append(problem)
                answers[name] = Answer(
                    name=name,
                    type=question.question_type,
                    value=None,
                    confidence=None,
                    probabilities=None,
                    legend=None,
                    band=BAND_HITL,
                    chosen_probability=None,
                    raw={},
                    issues=(problem,),
                )
                continue
            if not isinstance(raw, Mapping):
                problem = f"answer {name!r} is not an object ({type(raw).__name__}); ignored"
                issues.append(problem)
                answers[name] = Answer(
                    name=name,
                    type=question.question_type,
                    value=None,
                    confidence=None,
                    probabilities=None,
                    legend=None,
                    band=BAND_HITL,
                    chosen_probability=None,
                    raw={},
                    issues=(problem,),
                )
                continue
            answer = self._normalise_answer(name, question, raw)
            answers[name] = answer
            issues.extend(f"{name}: {problem}" for problem in answer.issues)

        for name, raw in raw_answers.items():
            if name in known or not isinstance(raw, Mapping):
                continue
            problem = f"answer {name!r} was not requested; ignored"
            issues.append(problem)
            extra_confidence = _as_float(raw.get("confidence"))
            answers[str(name)] = Answer(
                name=str(name),
                type=raw.get("type") if raw.get("type") in QUESTION_TYPES else None,
                value=raw.get("noul", raw.get("choice", raw.get("score"))),
                confidence=None if extra_confidence == 0.0 else extra_confidence,
                probabilities=_as_probabilities(raw.get("probabilities")),
                legend=dict(raw["legend"]) if isinstance(raw.get("legend"), Mapping) else None,
                band=BAND_HITL,
                chosen_probability=None,
                raw=dict(raw),
                issues=(problem,),
            )

        return answers, issues

    def _normalise_answer(
        self,
        name: str,
        question: Question,
        raw: Mapping[str, Any],
    ) -> Answer:
        problems: list[str] = []
        declared = question.question_type
        body_type = raw.get("type")
        if body_type is not None and body_type != declared:
            problems.append(f"response type {body_type!r} differs from the requested {declared!r}")
        qtype = body_type if body_type in QUESTION_TYPES else declared

        confidence = _as_float(raw.get("confidence"))
        if "confidence" in raw and confidence is None:
            problems.append("confidence is not numeric; treated as unavailable")
        if confidence == 0.0:
            # ``0`` is the fallback's "no confidence reported": never expose it as a
            # value a caller could read as "evenly spread". ``raw`` keeps the original.
            confidence = None
        probabilities = _as_probabilities(raw.get("probabilities"), problems)
        legend = (
            {str(k): str(v) for k, v in raw["legend"].items()}
            if isinstance(raw.get("legend"), Mapping)
            else None
        )

        chosen_probability: float | None = None
        if qtype == "noul":
            # noul NEVER carries a confidence: never look for one, never warn.
            confidence = None
            value: float | str | None = _as_float(raw.get("noul"))
            if value is None:
                problems.append("noul value is missing or not numeric")
                band = BAND_HITL
            elif not 0.0 <= float(value) <= 1.0:
                problems.append(f"noul value {value} is outside [0, 1]; treated as unavailable")
                value = None
                band = BAND_HITL
            else:
                band = band_for_noul(value, auto=self.auto_threshold, hitl=self.hitl_threshold)
        elif qtype == "choice":
            choice_value = raw.get("choice")
            if not isinstance(choice_value, str) or not choice_value.strip():
                problems.append("choice value is missing or not a string")
                value = None
                band = BAND_HITL
            else:
                value = choice_value
                band = band_for_choice_or_score(
                    confidence, auto=self.auto_threshold, hitl=self.hitl_threshold
                )
                if not confidence_available(confidence):
                    problems.append(
                        "confidence unavailable (absent or 0.0) → band capped at hitl"
                    )
                offered = _option_keys(question)
                if offered is not None and value not in offered:
                    problems.append(
                        f"choice {value!r} is not one of the offered options; "
                        "band capped at hitl"
                    )
                    band = cap_band(band, BAND_HITL)
                if probabilities is not None:
                    chosen_probability = probabilities.get(value)
        elif qtype == "score":
            score_value = _as_float(raw.get("score"))
            if score_value is None:
                problems.append("score value is missing or not numeric")
                value = None
                band = BAND_HITL
            else:
                value = score_value
                band = band_for_choice_or_score(
                    confidence, auto=self.auto_threshold, hitl=self.hitl_threshold
                )
                if not confidence_available(confidence):
                    problems.append(
                        "confidence unavailable (absent or 0.0) → band capped at hitl"
                    )
                levels = _levels(question)
                if levels and not 0.0 <= score_value <= float(len(levels) - 1):
                    problems.append(
                        f"score {score_value} is outside the offered level range "
                        f"0..{len(levels) - 1}; band capped at hitl"
                    )
                    band = cap_band(band, BAND_HITL)
        else:
            problems.append(f"unknown answer type {body_type!r}")
            value = None
            band = BAND_HITL

        if band == BAND_AUTO and problems:
            # Any degradation is a reason to keep a human in the loop.
            band = cap_band(band, BAND_HITL)

        return Answer(
            name=name,
            type=qtype,
            value=value,
            confidence=confidence,
            probabilities=probabilities,
            legend=legend,
            band=band,
            chosen_probability=chosen_probability,
            raw=dict(raw),
            issues=tuple(problems),
        )


def _normalise_usage(payload: Mapping[str, Any]) -> tuple[Usage, list[str]]:
    problems: list[str] = []
    raw = payload.get("usage")
    if raw is None:
        return Usage(raw={}), problems
    if not isinstance(raw, Mapping):
        problems.append(f"usage is not an object ({type(raw).__name__}); counted as zero")
        return Usage(raw={}), problems
    input_tokens = _as_int(raw.get("input_tokens"))
    output_tokens = _as_int(raw.get("output_tokens"))
    cost = _as_float(raw.get("cost"))
    if raw.get("cost") is not None and cost is None:
        problems.append("usage.cost is not numeric; estimated from token pricing")
    if cost is None:
        # gotcha 19: no provider cost in the payload -> estimate from the
        # model's token pricing (input $0.042/Mtok, output free).
        reported_model = payload.get("model")
        cost = estimate_cost(
            input_tokens or 0,
            output_tokens or 0,
            model=reported_model if isinstance(reported_model, str) else DEFAULT_MODEL,
        )
    return (
        Usage(
            input_tokens=input_tokens or 0,
            output_tokens=output_tokens or 0,
            cost_usd=money(cost),
            raw=dict(raw),
        ),
        problems,
    )
