"""Offline validation of a whole Jev request — no network, no tokens spent.

The alpha ``/decisions`` endpoint is unforgiving in ways that are cheap to check
locally: a request that exceeds the context budget is rejected, a question name
that collides silently loses an answer, and a fat or repetitive ``state``
degrades accuracy (*context rot*) without any error at all.

This module never raises for judgement calls: it returns a
:class:`ValidationReport` with ``errors`` (the request must not be sent) and
``warnings`` (legal, but suspicious). Call :meth:`ValidationReport.raise_if_invalid`
to fail fast with :class:`~system_one_dwarf.jev.errors.JevRequestError`.

Checks
------
============================  ==========================================
state + longest question      error above 32K estimated tokens,
                              warning above 24K (chars / 4 estimate)
state size / shape            warning above 8K tokens, above 500 lines,
                              long single lines or many repeated lines
injection-looking state       warning (state is data, never instructions)
question name collisions      error (duplicates after case/space folding)
question schema               errors/warnings from
                              :func:`system_one_dwarf.jev.schema.check_question`
non-atomic instructions       warning ("and" joining two decisions)
empty criteria / empty keys   error
``session_id`` longer than     error (documented limit: 256 chars)
256 chars
============================  ==========================================
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .errors import JevRequestError
from .schema import check_question, normalize_questions

__all__ = [
    "ValidationReport",
    "validate_request",
    "validate_state_and_questions",
    "estimate_tokens",
    "state_to_text",
    "looks_non_atomic",
    "MAX_CONTEXT_TOKENS",
    "WARN_CONTEXT_TOKENS",
    "STATE_ROT_WARN_TOKENS",
    "MAX_SESSION_ID_CHARS",
    "INJECTION_HINTS",
]

#: Hard budget: ``state`` + the longest single question (documented limit).
MAX_CONTEXT_TOKENS = 32_000

#: Soft budget: above this the request is legal but accuracy starts to suffer.
WARN_CONTEXT_TOKENS = 24_000

#: A state this big is a context-rot risk even when the total budget holds.
STATE_ROT_WARN_TOKENS = 8_000

#: Documented maximum length of ``session_id`` (observability only).
MAX_SESSION_ID_CHARS = 256

_STATE_MAX_LINES = 500
_STATE_LONG_LINE_CHARS = 2_000
_STATE_MIN_LINES_FOR_REPETITION = 20

#: Conservative phrases that suggest prompt injection inside the state. The
#: state is **not** treated as hostile by the API, so this is a warning for the
#: human/agent loop, never a rewrite of the content.
INJECTION_HINTS: tuple[str, ...] = (
    "ignore previous",
    "ignore all previous",
    "ignore the above",
    "disregard previous",
    "disregard the above",
    "forget previous",
    "new instructions",
    "system prompt",
    "you are now",
    "as an ai",
    "jailbreak",
)

_INJECTION_RE = re.compile("|".join(re.escape(hint) for hint in INJECTION_HINTS), re.IGNORECASE)
_ATOMIC_AND_RE = re.compile(
    r"\band\b[^.?;]{0,80}?\b(?:should|shall|is|are|does|do|can|could|will|would|must|"
    r"needs?|has|have|was|were|decide|choose|pick)\b",
    re.IGNORECASE,
)


def estimate_tokens(text: str) -> int:
    """Cheap token estimate: ``ceil(chars / 4)`` (never call a tokenizer)."""
    if not text:
        return 0
    return int(math.ceil(len(text) / 4.0))


def state_to_text(state: Any) -> str:
    """Serialise ``state`` (string, object or array) the way it goes on the wire."""
    if isinstance(state, str):
        return state
    if state is None:
        return ""
    return json.dumps(state, ensure_ascii=False, separators=(",", ":"), default=str)


def looks_non_atomic(instructions: Any) -> bool:
    """True when instructions appear to bundle two decisions into one question.

    The documented failure mode is *literal reading*: the model answers one of
    the two decisions and silently drops the other. Separate questions run in
    parallel inside the same request, so splitting them is free.
    """
    if not isinstance(instructions, str):
        return False
    if instructions.count("?") > 1:
        return True
    return bool(_ATOMIC_AND_RE.search(instructions))


@dataclass
class ValidationReport:
    """Result of an offline validation pass over one request."""

    state_tokens: int = 0
    longest_question_tokens: int = 0
    question_names: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        """``state`` + longest single question — the documented budget basis."""
        return self.state_tokens + self.longest_question_tokens

    @property
    def ok(self) -> bool:
        """True when the request may be sent (warnings are allowed)."""
        return not self.errors

    def raise_if_invalid(self, *, strict: bool = False) -> None:
        """Raise :class:`JevRequestError` when the request must not be sent.

        ``strict=True`` treats warnings as errors as well (useful in tests and
        before expensive calls).
        """
        problems = list(self.errors)
        if strict:
            problems += list(self.warnings)
        if problems:
            raise JevRequestError("invalid Jev request: " + "; ".join(problems))

    def format(self) -> str:
        lines = [
            f"Jev request: {len(self.question_names)} question(s) "
            f"{self.question_names}, {self.total_tokens} estimated tokens "
            f"(state {self.state_tokens} + longest question {self.longest_question_tokens})"
        ]
        lines += [f"ERROR: {e}" for e in self.errors]
        lines += [f"WARNING: {w}" for w in self.warnings]
        return "\n".join(lines)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.format()


def _check_state(state: Any, report: ValidationReport) -> None:
    if state is None:
        report.errors.append("'state' is required (string, object or array)")
        return
    if not isinstance(state, (str, Mapping, Sequence)) or isinstance(state, (bytes, bytearray)):
        report.errors.append(
            f"'state' must be a string, object or array, got {type(state).__name__}"
        )
        return
    if isinstance(state, str):
        text = state
    else:
        try:
            # strict + allow_nan=False: this is exactly what must go on the wire
            text = json.dumps(state, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            report.errors.append(f"'state' is not JSON-serializable: {exc}")
            return

    report.state_tokens = estimate_tokens(text)
    if not text.strip():
        report.errors.append("'state' is empty; give the model the critical situation in English")
        return

    if report.state_tokens > STATE_ROT_WARN_TOKENS:
        report.warnings.append(
            f"state is ~{report.state_tokens} tokens; context rot degrades accuracy — "
            "keep only the critical, correlated facts in English"
        )
    lines = text.splitlines()
    if len(lines) > _STATE_MAX_LINES:
        report.warnings.append(
            f"state has {len(lines)} lines; noisy states hurt accuracy (context rot)"
        )
    if lines and max(len(line) for line in lines) > _STATE_LONG_LINE_CHARS:
        report.warnings.append(
            "state contains a very long single line (metadata dump?); summarise it in code"
        )
    if len(lines) >= _STATE_MIN_LINES_FOR_REPETITION:
        unique = len(set(lines))
        if unique * 2 < len(lines):
            report.warnings.append(
                f"state looks repetitive ({len(lines) - unique} duplicated lines); "
                "deduplicate it before sending"
            )
    hint = _INJECTION_RE.search(text)
    if hint:
        report.warnings.append(
            f"state contains instruction-like text ({hint.group(0)!r}); state content is DATA — "
            "injection can steer answers, so gate the result with bands, never with trust"
        )


def _check_questions(questions: Any, report: ValidationReport) -> None:
    if questions is None:
        report.errors.append("'questions' is required (mapping {name: question})")
        return
    try:
        normalized = normalize_questions(questions)
    except JevRequestError as exc:
        report.errors.append(str(exc))
        return

    if not normalized:
        report.errors.append("'questions' is empty; a request needs at least one question")
        return

    seen: dict[str, str] = {}
    for name, question in normalized:
        report.question_names.append(name)
        folded = name.strip().lower() if isinstance(name, str) else str(name)
        if folded in seen:
            report.errors.append(
                f"question name collision: {name!r} and {seen[folded]!r} normalise to the same "
                "key; answers would overwrite each other"
            )
        else:
            seen[folded] = name

        issues = check_question(name, question)
        report.errors.extend(issues.errors)
        report.warnings.extend(issues.warnings)

        body = question.to_body() if hasattr(question, "to_body") else question
        if isinstance(body, Mapping) and looks_non_atomic(body.get("instructions")):
            report.warnings.append(
                f"question {name!r}: instructions may bundle more than one decision; "
                "split it into separate questions (they run in parallel in one request)"
            )

        try:
            serialized = json.dumps({name: body}, ensure_ascii=False, separators=(",", ":"), default=str)
        except (TypeError, ValueError):
            report.errors.append(f"question {name!r} is not JSON-serializable")
            continue
        report.longest_question_tokens = max(
            report.longest_question_tokens, estimate_tokens(serialized)
        )


def _check_session_id(session_id: Any, report: ValidationReport) -> None:
    if session_id is None:
        return
    if not isinstance(session_id, str):
        report.errors.append(f"'session_id' must be a string, got {type(session_id).__name__}")
        return
    if len(session_id) > MAX_SESSION_ID_CHARS:
        report.errors.append(
            f"'session_id' is {len(session_id)} chars, the maximum is {MAX_SESSION_ID_CHARS}"
        )
    elif not session_id.strip():
        report.warnings.append("'session_id' is empty and will be dropped")


def _validate(state: Any, questions: Any, session_id: Any) -> ValidationReport:
    report = ValidationReport()
    _check_state(state, report)
    _check_questions(questions, report)
    _check_session_id(session_id, report)

    if report.total_tokens > MAX_CONTEXT_TOKENS:
        report.errors.append(
            f"state + longest question is ~{report.total_tokens} tokens, above the "
            f"{MAX_CONTEXT_TOKENS} limit"
        )
    elif report.total_tokens > WARN_CONTEXT_TOKENS:
        report.warnings.append(
            f"state + longest question is ~{report.total_tokens} tokens, close to the "
            f"{MAX_CONTEXT_TOKENS} limit; trim the state"
        )
    return report


def validate_request(
    request: Mapping[str, Any],
    *,
    strict: bool = False,
    raise_errors: bool = True,
) -> ValidationReport:
    """Validate a full request mapping ``{state, questions[, session_id]}``.

    Hard errors raise :class:`JevRequestError` unless ``raise_errors=False``;
    in both cases the returned report carries ``errors`` and ``warnings``.
    ``strict=True`` also raises on warnings.

    Unknown top-level keys (for example the ``ordering``/``meta`` bookkeeping
    added by :mod:`system_one_dwarf.jev.fleet`) are ignored, so a fleet request
    can be validated as-is. Offline: this never touches the network.
    """
    if not isinstance(request, Mapping):
        report = ValidationReport()
        report.errors.append(f"request must be a mapping, got {type(request).__name__}")
    else:
        report = _validate(
            request.get("state"),
            request.get("questions"),
            request.get("session_id"),
        )
    if raise_errors:
        report.raise_if_invalid(strict=strict)
    return report


def validate_state_and_questions(
    state: Any,
    questions: Any,
    session_id: str | None = None,
    *,
    strict: bool = False,
    raise_errors: bool = True,
) -> ValidationReport:
    """Same as :func:`validate_request` for the ``decide(state, questions)`` form."""
    report = _validate(state, questions, session_id)
    if raise_errors:
        report.raise_if_invalid(strict=strict)
    return report
