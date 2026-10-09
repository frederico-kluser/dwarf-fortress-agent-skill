"""Jev fleet — typed decision calls to ``typesafe/jev-1.13`` via OpenRouter.

The Jev model is a **System One** model: it does not generate text, it answers
typed questions about a ``state`` (all questions in one request are evaluated in
parallel and cannot see each other's answers). This package is the fleet's
Python client:

* :mod:`~system_one_dwarf.jev.schema` — builders/validators for the three
  primitives (``noul``, ``choice``, ``score``);
* :mod:`~system_one_dwarf.jev.validate` — offline whole-request validation
  (context budget, context rot, name collisions, atomicity);
* :mod:`~system_one_dwarf.jev.bands` — action bands ``auto``/``hitl``/``abstain``
  with the *unavailable confidence* rule;
* :mod:`~system_one_dwarf.jev.client` — :class:`JevClient` with an injectable
  transport, typed errors and bounded retries;
* :mod:`~system_one_dwarf.jev.fleet` — request builders for the four roles
  (JEV-R retrieval, JEV-A action, JEV-M memory, JEV-D doubt).

Quick start::

    from system_one_dwarf.jev import JevClient, build_action

    request = build_action(action_candidates, state_summary)
    with JevClient(api_key) as client:              # key injected at runtime
        result = client.decide_request(request)
    if result.band("action") == "auto":
        execute(result.value("action"))

Hard rules baked in: the API key is never hardcoded nor logged; English states;
one atomic decision per question; ``choice`` always offers an exit option;
``score`` levels are concrete situations; a missing/zero confidence is
*unavailable* and never reaches the ``auto`` band.
"""

from __future__ import annotations

from .bands import (
    BAND_ABSTAIN,
    BAND_AUTO,
    BAND_HITL,
    BANDS,
    DEFAULT_AUTO_THRESHOLD,
    DEFAULT_HITL_THRESHOLD,
    band_for_choice_or_score,
    band_for_noul,
    band_rank,
    cap_band,
    certainty_from_noul,
    confidence_available,
    validate_thresholds,
)
from .client import (
    DEFAULT_ENDPOINT,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MODEL,
    RETRYABLE_STATUSES,
    Answer,
    DecisionResult,
    JevClient,
    Usage,
)
from .errors import (
    JevAuthError,
    JevError,
    JevQuotaError,
    JevRequestError,
    JevTransportError,
    JevWarning,
)
from .fleet import (
    DEFAULT_URGENCY_LEVELS,
    MAX_RETRIEVAL_CANDIDATES,
    NARRATOR_EFFORTS,
    QUESTION_FORMATS,
    RECORD_TYPES,
    build_action,
    build_doubt,
    build_memory,
    build_retrieval,
    shuffle_options,
    unshuffle,
)
from .schema import (
    EXIT_OPTION_KEYS,
    MAX_CHOICE_OPTIONS,
    MAX_SCORE_LEVELS,
    MIN_SCORE_LEVELS,
    QUESTION_TYPES,
    Question,
    QuestionIssues,
    as_question,
    asks_for_text,
    check_question,
    choice,
    is_exit_option,
    looks_numeric_level,
    normalize_questions,
    noul,
    score,
)
from .validate import (
    MAX_CONTEXT_TOKENS,
    MAX_SESSION_ID_CHARS,
    STATE_ROT_WARN_TOKENS,
    WARN_CONTEXT_TOKENS,
    ValidationReport,
    estimate_tokens,
    looks_non_atomic,
    validate_request,
    validate_state_and_questions,
)

__all__ = [
    # client
    "JevClient",
    "DecisionResult",
    "Answer",
    "Usage",
    "DEFAULT_MODEL",
    "DEFAULT_ENDPOINT",
    "DEFAULT_MAX_ATTEMPTS",
    "RETRYABLE_STATUSES",
    # errors
    "JevError",
    "JevAuthError",
    "JevQuotaError",
    "JevRequestError",
    "JevTransportError",
    "JevWarning",
    # schema
    "Question",
    "QuestionIssues",
    "noul",
    "choice",
    "score",
    "check_question",
    "as_question",
    "normalize_questions",
    "is_exit_option",
    "looks_numeric_level",
    "asks_for_text",
    "QUESTION_TYPES",
    "MAX_CHOICE_OPTIONS",
    "MIN_SCORE_LEVELS",
    "MAX_SCORE_LEVELS",
    "EXIT_OPTION_KEYS",
    # validation
    "ValidationReport",
    "validate_request",
    "validate_state_and_questions",
    "estimate_tokens",
    "looks_non_atomic",
    "MAX_CONTEXT_TOKENS",
    "WARN_CONTEXT_TOKENS",
    "STATE_ROT_WARN_TOKENS",
    "MAX_SESSION_ID_CHARS",
    # bands
    "band_for_choice_or_score",
    "band_for_noul",
    "certainty_from_noul",
    "confidence_available",
    "validate_thresholds",
    "band_rank",
    "cap_band",
    "BAND_AUTO",
    "BAND_HITL",
    "BAND_ABSTAIN",
    "BANDS",
    "DEFAULT_AUTO_THRESHOLD",
    "DEFAULT_HITL_THRESHOLD",
    # fleet
    "build_retrieval",
    "build_action",
    "build_memory",
    "build_doubt",
    "shuffle_options",
    "unshuffle",
    "MAX_RETRIEVAL_CANDIDATES",
    "DEFAULT_URGENCY_LEVELS",
    "NARRATOR_EFFORTS",
    "RECORD_TYPES",
    "QUESTION_FORMATS",
]
