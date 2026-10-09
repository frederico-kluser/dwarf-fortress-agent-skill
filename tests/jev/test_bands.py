"""Band computation, including the *unavailable confidence* rule."""

from __future__ import annotations

import math

import pytest

from system_one_dwarf.jev import (
    BAND_ABSTAIN,
    BAND_AUTO,
    BAND_HITL,
    JevRequestError,
    band_for_choice_or_score,
    band_for_noul,
    band_rank,
    cap_band,
    certainty_from_noul,
    confidence_available,
)


# --------------------------------------------------------------------------- #
# choice / score
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "confidence,expected",
    [
        (1.0, BAND_AUTO),
        (0.95, BAND_AUTO),
        (0.90, BAND_AUTO),  # boundary is inclusive
        (0.89, BAND_HITL),
        (0.67, BAND_HITL),
        (0.50, BAND_HITL),  # boundary is inclusive
        (0.49, BAND_ABSTAIN),
        (0.01, BAND_ABSTAIN),
    ],
)
def test_band_for_choice_or_score_defaults(confidence, expected):
    assert band_for_choice_or_score(confidence) == expected


@pytest.mark.parametrize("confidence", [None, 0.0, "0.9", True])
def test_unavailable_confidence_never_reaches_auto(confidence):
    band = band_for_choice_or_score(confidence)
    assert band == BAND_HITL
    assert band != BAND_AUTO


def test_missing_confidence_is_not_read_as_a_flat_distribution():
    # a flat distribution (0.25 < hitl) would be abstain; "unavailable" is not that
    assert band_for_choice_or_score(0.25) == BAND_ABSTAIN
    assert band_for_choice_or_score(0.0) == BAND_HITL
    assert band_for_choice_or_score(None) == BAND_HITL


def test_thresholds_are_configurable():
    assert band_for_choice_or_score(0.8, auto=0.75, hitl=0.4) == BAND_AUTO
    assert band_for_choice_or_score(0.5, auto=0.75, hitl=0.4) == BAND_HITL
    assert band_for_choice_or_score(0.3, auto=0.75, hitl=0.4) == BAND_ABSTAIN
    assert band_for_noul(0.9, auto=0.75, hitl=0.4) == BAND_AUTO


@pytest.mark.parametrize("confidence", [1.5, -0.1, math.nan, math.inf])
def test_out_of_range_confidence_raises(confidence):
    with pytest.raises(JevRequestError):
        band_for_choice_or_score(confidence)


@pytest.mark.parametrize("confidence", ["abc", object(), [0.9]])
def test_non_numeric_confidence_is_unavailable(confidence):
    assert band_for_choice_or_score(confidence) == BAND_HITL


@pytest.mark.parametrize("auto,hitl", [(0.4, 0.9), (1.5, 0.5), (0.9, -0.1), ("a", 0.5)])
def test_invalid_thresholds_raise(auto, hitl):
    with pytest.raises(JevRequestError):
        band_for_choice_or_score(0.95, auto=auto, hitl=hitl)


# --------------------------------------------------------------------------- #
# noul
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "p,expected",
    [
        (0.5, BAND_ABSTAIN),  # total uncertainty, NOT medium intensity
        (0.7, BAND_ABSTAIN),
        (0.8, BAND_HITL),
        (0.9, BAND_HITL),
        (0.95, BAND_AUTO),
        (0.96, BAND_AUTO),
        (0.05, BAND_AUTO),  # certainty is symmetric
        (1.0, BAND_AUTO),
        (0.0, BAND_AUTO),
    ],
)
def test_band_for_noul_uses_certainty(p, expected):
    assert band_for_noul(p) == expected


def test_band_for_noul_missing_p_is_unavailable():
    assert band_for_noul(None) == BAND_HITL
    assert band_for_noul(None) != BAND_AUTO


@pytest.mark.parametrize(
    "p,expected", [(0.5, 0.0), (0.96, 0.92), (0.04, 0.92), (1.0, 1.0), (0.0, 1.0)]
)
def test_certainty_from_noul(p, expected):
    assert certainty_from_noul(p) == pytest.approx(expected)


@pytest.mark.parametrize("p", [-0.01, 1.01, math.nan, "0.5", True])
def test_invalid_noul_p_raises_where_numeric_is_expected(p):
    with pytest.raises(JevRequestError):
        certainty_from_noul(p)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def test_confidence_available():
    assert confidence_available(0.5) is True
    assert confidence_available(0.0) is False
    assert confidence_available(None) is False
    assert confidence_available("0.5") is False


def test_band_rank_and_cap_band():
    assert band_rank(BAND_AUTO) > band_rank(BAND_HITL) > band_rank(BAND_ABSTAIN)
    assert cap_band(BAND_AUTO, BAND_HITL) == BAND_HITL
    assert cap_band(BAND_HITL, BAND_HITL) == BAND_HITL
    assert cap_band(BAND_ABSTAIN, BAND_HITL) == BAND_ABSTAIN
    with pytest.raises(JevRequestError, match="unknown band"):
        band_rank("confident")
