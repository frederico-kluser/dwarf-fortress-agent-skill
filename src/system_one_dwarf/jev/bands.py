"""Action bands: how much autonomy a Jev answer has earned.

Three bands, configurable thresholds (defaults from the architecture doc):

===========  ===========================  ====================================
Band         Confidence / certainty       Behaviour
===========  ===========================  ====================================
``auto``     >= ``auto`` (default 0.90)   execute the macro-action
``hitl``     >= ``hitl`` (default 0.50)   confirm with the human first
``abstain``  < ``hitl``                   do not act; escalate
===========  ===========================  ====================================

Semantics that matter (and are easy to get wrong):

* ``confidence`` (``choice``/``score``) describes the **concentration** of the
  answer distribution. It is not a probability of being correct, and it is
  **optional** in the alpha schema.
* ``noul`` **never** has a confidence. Its certainty is ``|2p - 1|`` where
  ``p = P(true)``: ``p = 0.5`` means *total uncertainty* (not "medium
  intensity"), and ``p = 0.05`` is just as certain as ``p = 0.95``.
* Missing confidence and ``confidence == 0.0`` are both **UNAVAILABLE**: the
  alpha schema allows the field to be absent and fallbacks report ``0``, so the
  two are indistinguishable from a flat distribution. ``0`` is therefore never
  read as "evenly spread": unavailable answers are capped at ``hitl`` — they can
  never reach ``auto``, but they are not treated as evidence *against* the
  answer either.
* Out-of-range or non-numeric inputs raise
  :class:`~system_one_dwarf.jev.errors.JevRequestError` (a NaN confidence is a
  bug upstream, not a low-confidence answer).
"""

from __future__ import annotations

import math
from typing import Any

from .errors import JevRequestError
from .schema import is_confidence_available

__all__ = [
    "BAND_AUTO",
    "BAND_HITL",
    "BAND_ABSTAIN",
    "BANDS",
    "DEFAULT_AUTO_THRESHOLD",
    "DEFAULT_HITL_THRESHOLD",
    "certainty_from_noul",
    "validate_thresholds",
    "confidence_available",
    "band_for_choice_or_score",
    "band_for_noul",
    "band_rank",
    "cap_band",
]

BAND_AUTO = "auto"
BAND_HITL = "hitl"
BAND_ABSTAIN = "abstain"

#: Ordered from most to least autonomy.
BANDS: tuple[str, str, str] = (BAND_AUTO, BAND_HITL, BAND_ABSTAIN)

DEFAULT_AUTO_THRESHOLD = 0.90
DEFAULT_HITL_THRESHOLD = 0.50

_RANK = {BAND_ABSTAIN: 0, BAND_HITL: 1, BAND_AUTO: 2}

#: Tolerance for threshold comparisons: ``|2 * 0.95 - 1|`` is
#: ``0.8999999999999999`` in binary floating point, and a certainty that is
#: *meant* to sit exactly on the threshold must reach the band above it.
_BOUNDARY_EPSILON = 1e-9


def validate_thresholds(auto: float, hitl: float) -> None:
    """Validate a threshold pair (``0 <= hitl <= auto <= 1``).

    Raises:
        JevRequestError: non-numeric, out-of-range or inverted thresholds.
    """
    for label, value in (("auto", auto), ("hitl", hitl)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isnan(value):
            raise JevRequestError(f"{label} threshold must be a number in [0, 1], got {value!r}")
        if not 0.0 <= float(value) <= 1.0:
            raise JevRequestError(f"{label} threshold must be in [0, 1], got {value!r}")
    if float(hitl) > float(auto):
        raise JevRequestError(
            f"hitl threshold ({hitl}) must not exceed the auto threshold ({auto})"
        )


def _check_unit_interval(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JevRequestError(f"{what} must be a number in [0, 1], got {value!r}")
    number = float(value)
    if math.isnan(number) or not 0.0 <= number <= 1.0:
        raise JevRequestError(f"{what} must be in [0, 1], got {value!r}")
    return number


def certainty_from_noul(p: Any) -> float:
    """Certainty of a ``noul`` answer: ``|2p - 1|``.

    ``p`` is ``P(true)`` in ``[0, 1]``. ``0.5`` yields ``0.0`` (total
    uncertainty); both ``0.0`` and ``1.0`` yield ``1.0`` (maximum certainty).
    """
    value = _check_unit_interval(p, "noul p")
    return abs(2.0 * value - 1.0)


def confidence_available(confidence: Any) -> bool:
    """False when ``confidence`` is missing, non-numeric, or exactly ``0.0``."""
    return is_confidence_available(confidence)


def _band_from_value(value: float, auto: float, hitl: float) -> str:
    """Map a real value in ``[0, 1]`` onto the bands (thresholds inclusive)."""
    if value + _BOUNDARY_EPSILON >= float(auto):
        return BAND_AUTO
    if value + _BOUNDARY_EPSILON >= float(hitl):
        return BAND_HITL
    return BAND_ABSTAIN


def band_for_choice_or_score(
    confidence: Any,
    auto: float = DEFAULT_AUTO_THRESHOLD,
    hitl: float = DEFAULT_HITL_THRESHOLD,
) -> str:
    """Band for a ``choice``/``score`` answer from its reported confidence.

    UNAVAILABLE confidence (``None``, missing, or ``0.0``) returns ``"hitl"``:
    the answer is never auto-executed, and it is not read as a flat
    distribution either (see the module docstring).

    Raises:
        JevRequestError: invalid confidence or invalid thresholds.
    """
    validate_thresholds(auto, hitl)
    if not confidence_available(confidence):
        return BAND_HITL
    return _band_from_value(_check_unit_interval(confidence, "confidence"), auto, hitl)


def band_for_noul(
    p: Any,
    auto: float = DEFAULT_AUTO_THRESHOLD,
    hitl: float = DEFAULT_HITL_THRESHOLD,
) -> str:
    """Band for a ``noul`` answer from ``p = P(true)``, using ``|2p - 1|``.

    ``p = 0.5`` (total uncertainty) falls to ``"abstain"``; ``p = 0.95`` and
    ``p = 0.05`` both reach ``"auto"`` at the default thresholds. ``None``
    (answer missing) is UNAVAILABLE and capped at ``"hitl"``.

    Unlike ``confidence``, a certainty of ``0.0`` is a *real* value here: it
    means ``p = 0.5`` (total uncertainty), so it falls to ``"abstain"`` — the
    unavailable-confidence rule does not apply to ``noul``.

    Raises:
        JevRequestError: invalid ``p`` or invalid thresholds.
    """
    validate_thresholds(auto, hitl)
    if p is None or (not isinstance(p, bool) and not isinstance(p, (int, float))):
        return BAND_HITL
    return _band_from_value(certainty_from_noul(p), auto, hitl)


def band_rank(band: str) -> int:
    """Autonomy rank of a band: ``auto`` 2 > ``hitl`` 1 > ``abstain`` 0."""
    try:
        return _RANK[band]
    except KeyError:
        raise JevRequestError(f"unknown band {band!r} (expected one of {', '.join(BANDS)})") from None


def cap_band(band: str, cap: str) -> str:
    """Downgrade ``band`` so it never exceeds ``cap`` (e.g. cap UNAVAILABLE at hitl)."""
    return band if band_rank(band) <= band_rank(cap) else cap
