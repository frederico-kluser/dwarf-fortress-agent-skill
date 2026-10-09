"""Typed builders and validators for the three Jev primitives.

The Jev model (``typesafe/jev-1.13`` via OpenRouter) answers **typed questions**,
never free text. This module builds those questions and enforces every rule the
model's documentation makes hard, so a broken request fails locally instead of
burning tokens (or, worse, producing a confident-looking wrong answer).

Wire shapes (``criteria`` is the only field whose shape changes per type)::

    noul  {"type": "noul",  "instructions": "...",
           "criteria": {"true": "...", "false": "..."}}
    choice {"type": "choice", "instructions": "...",
            "criteria": {"opt": "description", ...}}          # max 255 options
    score {"type": "score", "instructions": "...",
           "criteria": ["level 0 situation", ...]}            # 2..10 levels

Hard rules (raise :class:`~system_one_dwarf.jev.errors.JevRequestError`):

* instructions must be a non-empty string and must **not** ask for text
  generation — the model does not write prose ("explain", "write", "summarize",
  "describe why", ...). Encode the answer as ``choice`` options instead.
* ``choice`` must have between 1 and 255 options with non-empty string keys and
  values.
* ``score`` must have between 2 and 10 levels, each a non-empty string.

Judgement calls (emit :class:`~system_one_dwarf.jev.errors.JevWarning`):

* a ``choice`` without any exit option (``other``/``none``/``undecided``/...) —
  the model is then forced to pick something invalid when the list does not
  cover reality;
* ``score`` levels that look numeric or like bare scale words ("0", "1",
  "low", "medium", "high"): those carry almost no information, levels must
  describe **concrete situations**;
* identical ``true``/``false`` criteria of a ``noul`` (contradictory criteria
  hurt accuracy).

Builders return :class:`Question`, a ``dict`` subclass holding exactly the wire
body plus a ``name`` attribute (the key used in the request's ``questions``
map). It serialises with ``json.dumps`` as the plain body, so it can be put
straight into a request payload.
"""

from __future__ import annotations

import re
import warnings as _warnings
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .errors import JevRequestError, JevWarning

__all__ = [
    "Question",
    "QuestionIssues",
    "QUESTION_TYPES",
    "MAX_CHOICE_OPTIONS",
    "MIN_SCORE_LEVELS",
    "MAX_SCORE_LEVELS",
    "EXIT_OPTION_KEYS",
    "TEXT_GENERATION_PATTERNS",
    "BARE_SCALE_WORDS",
    "noul",
    "choice",
    "score",
    "check_question",
    "as_question",
    "normalize_questions",
    "is_exit_option",
    "looks_numeric_level",
    "asks_for_text",
    "is_confidence_available",
]

#: Question types accepted by the alpha ``/decisions`` endpoint.
QUESTION_TYPES: tuple[str, ...] = ("noul", "choice", "score")

#: Documented maximum number of ``choice`` options.
MAX_CHOICE_OPTIONS = 255

#: Documented ``score`` level bounds (a ruler of concrete situations).
MIN_SCORE_LEVELS = 2
MAX_SCORE_LEVELS = 10

#: Keys that count as an exit option of a ``choice`` (matched after
#: normalisation: lower-case, spaces/hyphens collapsed to ``_``).
EXIT_OPTION_KEYS = frozenset(
    {
        "other",
        "others",
        "none",
        "no",
        "no_action",
        "no_op",
        "undecided",
        "unknown",
        "unclear",
        "neither",
        "skip",
        "abstain",
        "n_a",
        "na",
        "not_applicable",
        "do_nothing",
        "nothing",
        "irrelevant",
        # An open/free-text option is the catch-all escape hatch of a closed set
        # (used by JEV-D's question-format choice).
        "open",
        "open_question",
        "free_text",
        "freeform",
        "free_form",
    }
)

#: Stems that mean "produce text" — the model cannot do that. Kept to
#: unambiguous *commands*: content nouns ("narrative", "story", "summary") are
#: legitimate inside an atomic typed question.
TEXT_GENERATION_PATTERNS: tuple[str, ...] = (
    r"\bexplain\w*\b",
    r"\bwrit(?:e|es)\b",
    r"\brewrite\b",
    r"\bsummar(?:i[sz]e|i[sz]ing|i[sz]ed)\b",
    r"\bdescribe\s+why\b",
    r"\belaborate\b",
    r"\bparaphras\w*\b",
    r"\btranslate\b",
    r"\bnarrat(?:e|ing)\b",
    r"\bcompose\b",
    r"\bin\s+your\s+own\s+words\b",
    r"\bgenerate\s+(?:a\s+|the\s+)?(?:text|sentence|paragraph|story|report|prose|summary)\b",
)

#: Bare scale words that are not a situation ("low", "medium", "high", ...).
BARE_SCALE_WORDS = frozenset(
    {
        "none",
        "nothing",
        "minimal",
        "minimum",
        "min",
        "very low",
        "low",
        "lower",
        "medium",
        "med",
        "mid",
        "middle",
        "moderate",
        "normal",
        "high",
        "higher",
        "very high",
        "max",
        "maximal",
        "maximum",
        "critical",
        "urgent",
        "severe",
        "slight",
        "mild",
        "extreme",
    }
)

_TEXT_GENERATION_RE = re.compile("|".join(TEXT_GENERATION_PATTERNS), re.IGNORECASE)
_NUMERIC_LEVEL_RE = re.compile(r"^(?:level\s*)?[-+]?\d+(?:[.,]\d+)?(?:\s*(?:-|–|—|to|/)\s*(?:level\s*)?[-+]?\d+(?:[.,]\d+)?)?$")
_WHITESPACE_RE = re.compile(r"[\s\-–—]+")

#: Longest question name accepted without a warning (names are wire overhead).
MAX_QUESTION_NAME_CHARS = 128


class Question(dict):
    """A wire-shaped question body that also remembers its request key.

    ``Question`` **is** the body dict the API expects::

        >>> q = noul("is_bug", "Is this a bug report?", "It is", "It is not")
        >>> sorted(q)                    # only wire fields, nothing extra
        ['criteria', 'instructions', 'type']
        >>> q.name
        'is_bug'
    """

    __slots__ = ("name",)

    def __init__(
        self,
        name: str,
        question_type: str,
        instructions: str,
        criteria: Mapping[str, Any] | Sequence[str],
    ) -> None:
        super().__init__(type=question_type, instructions=instructions, criteria=criteria)
        self.name = name

    @property
    def question_type(self) -> str:
        return str(self.get("type"))

    def to_body(self) -> dict[str, Any]:
        """Plain ``dict`` copy of the wire body (no ``Question`` attributes)."""
        return {
            "type": self.get("type"),
            "instructions": self.get("instructions"),
            "criteria": self.get("criteria"),
        }

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"Question(name={self.name!r}, type={self.question_type!r})"


@dataclass
class QuestionIssues:
    """Errors (block the request) and warnings (judgement calls) of one question."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def __bool__(self) -> bool:  # truthy when the question is usable
        return self.ok

    def extend(self, other: "QuestionIssues") -> "QuestionIssues":
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        return self

    def format(self) -> str:
        lines = [f"ERROR: {e}" for e in self.errors]
        lines += [f"WARNING: {w}" for w in self.warnings]
        return "\n".join(lines)

    def raise_if_invalid(self, *, what: str = "question") -> None:
        if self.errors:
            raise JevRequestError(f"invalid {what}: " + "; ".join(self.errors))


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _normalize_key(key: str) -> str:
    return _WHITESPACE_RE.sub("_", str(key).strip().lower()).strip("_")


def is_exit_option(key: Any) -> bool:
    """True when ``key`` names an escape hatch of a ``choice``.

    Accepts the documented ``other``/``none``/``undecided`` plus close variants
    (``skip``, ``unknown``, ``no_action``, ``none_of_the_above``, ...).
    """
    normalized = _normalize_key(key)
    if normalized in EXIT_OPTION_KEYS:
        return True
    return normalized.startswith(("other", "none_", "no_", "not_"))


def looks_numeric_level(level: Any) -> bool:
    """True when a ``score`` level is a number or a bare scale word.

    Levels must describe concrete situations; "0", "1", "low", "high" alone are
    documented as performing badly.
    """
    if not isinstance(level, str):
        return True
    text = level.strip().lower()
    if not text:
        return True
    if _NUMERIC_LEVEL_RE.match(text):
        return True
    return text in BARE_SCALE_WORDS


def asks_for_text(instructions: Any) -> str | None:
    """Return the offending phrase when instructions ask for generated text."""
    if not isinstance(instructions, str):
        return None
    match = _TEXT_GENERATION_RE.search(instructions)
    return match.group(0) if match else None


def is_confidence_available(confidence: Any) -> bool:
    """False for ``None``, missing, or ``0.0`` — all mean *unavailable*.

    The alpha schema lets ``confidence`` be absent, and fallbacks report ``0``;
    ``0`` is indistinguishable from a flat distribution, so it is never read as
    a real value.
    """
    if confidence is None or isinstance(confidence, bool):
        return False
    if not isinstance(confidence, (int, float)):
        return False
    return float(confidence) != 0.0


def as_question(value: Any, name: str | None = None) -> Question:
    """Coerce a mapping (or :class:`Question`) into a :class:`Question`.

    Raises :class:`JevRequestError` when ``value`` is not a mapping.
    """
    if isinstance(value, Question):
        if name is None or name == value.name:
            return value
        criteria = value.get("criteria")
        if isinstance(criteria, Mapping):
            criteria = dict(criteria)
        elif isinstance(criteria, list):
            criteria = list(criteria)
        return Question(name, value.question_type, value.get("instructions"), criteria)
    if isinstance(value, Mapping):
        qname = name if name is not None else value.get("name")
        if not isinstance(qname, str) or not qname:
            raise JevRequestError("question is missing a name")
        return Question(qname, value.get("type"), value.get("instructions"), value.get("criteria"))
    raise JevRequestError(f"question {name!r} must be a mapping, got {type(value).__name__}")


def normalize_questions(questions: Any) -> list[tuple[str, Question]]:
    """Normalise ``questions`` into an ordered ``[(name, Question), ...]`` list.

    Accepts the request-map form (``{name: question}``) or a sequence of
    :class:`Question` objects. Order is preserved: it is the order the model
    sees, which matters because the model leans towards the first option.
    """
    if isinstance(questions, Mapping):
        return [(str(name), as_question(body, str(name))) for name, body in questions.items()]
    if isinstance(questions, (str, bytes)) or not isinstance(questions, Iterable):
        raise JevRequestError(
            "questions must be a mapping {name: question} or a sequence of Question objects"
        )
    normalized: list[tuple[str, Question]] = []
    for item in questions:
        question = as_question(item)
        normalized.append((question.name, question))
    return normalized


# --------------------------------------------------------------------------- #
# validators
# --------------------------------------------------------------------------- #
def check_question(
    name: str,
    question: Any,
    *,
    declared_type: str | None = None,
) -> QuestionIssues:
    """Validate one question **without raising**; returns errors and warnings.

    Used by :func:`system_one_dwarf.jev.validate.validate_request` to collect a
    whole-request report, and by the builders to decide whether to raise.
    """
    issues = QuestionIssues()

    label = repr(name)
    if not isinstance(name, str) or not name.strip():
        issues.errors.append(f"question name {name!r} must be a non-empty string")
    else:
        if len(name) > MAX_QUESTION_NAME_CHARS:
            issues.warnings.append(
                f"question name {label} is longer than {MAX_QUESTION_NAME_CHARS} chars; "
                "short names keep the state/question budget small"
            )

    try:
        body: Any = question.to_body() if isinstance(question, Question) else question
    except Exception:  # pragma: no cover - defensive
        body = None

    if not isinstance(body, Mapping):
        issues.errors.append(f"question {label} must be a mapping with type/instructions/criteria")
        return issues

    qtype = body.get("type")
    if qtype not in QUESTION_TYPES:
        issues.errors.append(
            f"question {label}: unknown type {qtype!r} (expected one of {', '.join(QUESTION_TYPES)})"
        )
    elif declared_type is not None and qtype != declared_type:
        issues.warnings.append(
            f"question {label}: declared type {declared_type!r} but body says {qtype!r}"
        )

    instructions = body.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        issues.errors.append(f"question {label}: instructions must be a non-empty string")
    else:
        offending = asks_for_text(instructions)
        if offending:
            issues.errors.append(
                f"question {label}: instructions ask for generated text ({offending!r}); "
                "Jev returns typed decisions only — encode the answer as choice options instead"
            )

    criteria = body.get("criteria")
    if qtype == "noul":
        _check_noul_criteria(label, criteria, issues)
    elif qtype == "choice":
        _check_choice_criteria(label, criteria, issues)
    elif qtype == "score":
        _check_score_criteria(label, criteria, issues)

    return issues


def _check_noul_criteria(label: str, criteria: Any, issues: QuestionIssues) -> None:
    if not isinstance(criteria, Mapping):
        issues.errors.append(
            f"question {label}: noul criteria must be a mapping with 'true' and 'false' descriptions"
        )
        return
    keys = {_normalize_key(k) for k in criteria}
    missing = {"true", "false"} - keys
    if missing:
        issues.errors.append(
            f"question {label}: noul criteria missing {sorted(missing)} "
            "(both 'true' and 'false' need a concrete description)"
        )
    extra = keys - {"true", "false"}
    if extra:
        issues.errors.append(f"question {label}: noul criteria has unexpected keys {sorted(extra)}")
    for key, value in criteria.items():
        if not isinstance(value, str) or not value.strip():
            issues.errors.append(
                f"question {label}: noul criteria[{key!r}] must be a non-empty description"
            )
    true_desc = _criteria_get(criteria, "true")
    false_desc = _criteria_get(criteria, "false")
    if true_desc and false_desc and true_desc.strip() == false_desc.strip():
        issues.warnings.append(
            f"question {label}: 'true' and 'false' criteria are identical "
            "(contradictory criteria hurt accuracy)"
        )


def _criteria_get(criteria: Mapping[Any, Any], wanted: str) -> str | None:
    for key, value in criteria.items():
        if _normalize_key(key) == wanted and isinstance(value, str):
            return value
    return None


def _check_choice_criteria(label: str, criteria: Any, issues: QuestionIssues) -> None:
    if not isinstance(criteria, Mapping):
        issues.errors.append(
            f"question {label}: choice criteria must be a mapping {{option: description}}"
        )
        return
    if not criteria:
        issues.errors.append(f"question {label}: choice has no options (criteria is empty)")
        return
    if len(criteria) > MAX_CHOICE_OPTIONS:
        issues.errors.append(
            f"question {label}: choice has {len(criteria)} options, the maximum is "
            f"{MAX_CHOICE_OPTIONS}"
        )
    seen: list[str] = []
    for key, value in criteria.items():
        if not isinstance(key, str) or not key.strip():
            issues.errors.append(f"question {label}: choice has an empty option key")
            continue
        if not isinstance(value, str) or not value.strip():
            issues.errors.append(
                f"question {label}: choice option {key!r} needs a non-empty description"
            )
        normalized = _normalize_key(key)
        if normalized in seen:
            issues.warnings.append(
                f"question {label}: options {key!r} and a previous one normalise to the same "
                f"key ({normalized!r}); keep option keys unambiguous"
            )
        else:
            seen.append(normalized)
    if not any(is_exit_option(key) for key in criteria):
        issues.warnings.append(
            f"question {label}: choice has no exit option; add 'other' or 'none' "
            "(otherwise the model is forced to pick an option that does not fit)"
        )


def _check_score_criteria(label: str, criteria: Any, issues: QuestionIssues) -> None:
    if isinstance(criteria, (str, bytes)) or not isinstance(criteria, Sequence):
        issues.errors.append(
            f"question {label}: score criteria must be a list of level descriptions"
        )
        return
    levels = list(criteria)
    if not (MIN_SCORE_LEVELS <= len(levels) <= MAX_SCORE_LEVELS):
        issues.errors.append(
            f"question {label}: score has {len(levels)} levels, expected between "
            f"{MIN_SCORE_LEVELS} and {MAX_SCORE_LEVELS}"
        )
    numeric: list[str] = []
    cleaned: list[str] = []
    for level in levels:
        if not isinstance(level, str) or not level.strip():
            issues.errors.append(f"question {label}: every score level must be a non-empty string")
            continue
        cleaned.append(level.strip())
        if looks_numeric_level(level):
            numeric.append(level)
    if numeric:
        issues.warnings.append(
            f"question {label}: score levels {numeric} look numeric or like bare scale words; "
            "levels must describe concrete situations"
        )
    duplicates = sorted({level for level in cleaned if cleaned.count(level) > 1})
    if duplicates:
        issues.warnings.append(
            f"question {label}: duplicated score levels {duplicates}; each level must describe a "
            "distinct situation"
        )


# --------------------------------------------------------------------------- #
# builders
# --------------------------------------------------------------------------- #
def _finish(question: Question, what: str) -> Question:
    issues = check_question(question.name, question)
    for warning in issues.warnings:
        _warnings.warn(f"{what}: {warning}", JevWarning, stacklevel=3)
    issues.raise_if_invalid(what=what)
    return question


def noul(
    name: str,
    instructions: str,
    criteria_true: str,
    criteria_false: str,
) -> Question:
    """Build a ``noul`` question: a probability in ``[0, 1]`` of "true".

    The answer is ``{"type": "noul", "noul": <float>}`` and **never** carries a
    confidence; certainty is ``|2p - 1|`` (see
    :func:`system_one_dwarf.jev.bands.band_for_noul`). ``p = 0.5`` means total
    uncertainty, not medium intensity.

    Args:
        name: key of the question in the request's ``questions`` map.
        instructions: one atomic yes/no decision, in English.
        criteria_true: concrete situation in which the answer is true.
        criteria_false: concrete situation in which the answer is false.
    """
    question = Question(
        name,
        "noul",
        instructions,
        {"true": criteria_true, "false": criteria_false},
    )
    return _finish(question, "invalid noul question")


def choice(name: str, instructions: str, criteria: Mapping[str, str]) -> Question:
    """Build a ``choice`` question: one option key plus its distribution.

    The answer is ``{"type": "choice", "choice": <key>, "confidence": <float>,
    "probabilities": {<key>: <float>}}``; ``confidence``, ``probabilities`` and
    ``legend`` may be **missing** (the alpha schema only requires ``type`` and
    ``choice``).

    Args:
        name: key of the question in the request's ``questions`` map.
        instructions: one atomic decision, in English.
        criteria: ``{option_key: description}``, at most 255 options; include an
            exit option (``other``/``none``/``undecided``) whenever the list
            cannot cover reality.
    """
    question = Question(name, "choice", instructions, dict(criteria) if isinstance(criteria, Mapping) else criteria)
    return _finish(question, "invalid choice question")


def score(name: str, instructions: str, levels: Sequence[str]) -> Question:
    """Build a ``score`` question: a probability-weighted position on a ruler.

    The answer is ``{"type": "score", "score": <float>, "confidence": <float>,
    "probabilities": {...}, "legend": {...}}``; the score may fall between
    levels. ``confidence``, ``probabilities`` and ``legend`` may be missing.

    Args:
        name: key of the question in the request's ``questions`` map.
        instructions: one atomic judgement, in English.
        levels: 2 to 10 levels, each a **concrete situation** (never a numeric
            label such as "0"/"1" or a bare "low"/"medium"/"high").
    """
    question = Question(name, "score", instructions, list(levels) if isinstance(levels, Sequence) and not isinstance(levels, (str, bytes)) else levels)
    return _finish(question, "invalid score question")
