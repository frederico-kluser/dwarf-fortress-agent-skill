"""``BudgetGuard``: teto de orçamento OPT-IN (ARQUITETURA §9 · Custo).

Por omissão **desligado** — o comportamento documentado ("sem teto de orçamento,
só visibilidade") não pode mudar para quem não configura nada.  Ligado, o guard
tem três estados (``ok``/``warn``/``exceeded``) e as fronteiras são exatas: o
dinheiro é comparado em micro-USD inteiros, por isso erro de vírgula flutuante
nunca põe um total do lado errado do limiar.
"""
from __future__ import annotations

import json
import math
from types import SimpleNamespace

import pytest

from system_one_dwarf.agent.budget import (
    BUDGET_EXCEEDED,
    BUDGET_OK,
    BUDGET_STATES,
    BUDGET_WARN,
    DEFAULT_WARN_FRACTION,
    BudgetGuard,
    BudgetStatus,
    money,
    parse_limit,
)

# ------------------------------------------------------------------ desligado


@pytest.mark.parametrize("limit", [None, 0, 0.0, -1, -0.5, float("nan"), float("inf"),
                                   "", "0", "off", "none", "abc", True])
def test_the_guard_is_disabled_by_default_and_for_any_non_positive_limit(limit):
    guard = BudgetGuard(limit)

    assert guard.enabled is False
    assert guard.limit_usd is None
    for total in (0, 1.5, 1_000_000):
        status = guard.check(total)
        assert status.state == BUDGET_OK
        assert status.enabled is False
        assert status.exceeded is False and status.holds_actions is False
        assert status.fraction == 0.0
        assert status.limit_usd is None and status.remaining_usd is None
        assert "Sem teto" in status.message_pt


def test_default_constructor_is_the_documented_no_cap_behaviour():
    guard = BudgetGuard()

    assert guard.enabled is False
    assert guard.warn_fraction == DEFAULT_WARN_FRACTION == 0.8
    assert guard.check(3.52).total_usd == 3.52  # só visibilidade: o total aparece


def test_a_disabled_guard_never_reports_a_transition():
    guard = BudgetGuard()

    assert [guard.poll(total).changed for total in (0, 5, 50)] == [False, False, False]


# ------------------------------------------------------------------ fronteiras


@pytest.mark.parametrize(
    ("total", "state"),
    [
        (0.0, BUDGET_OK),
        (0.000001, BUDGET_OK),
        (7.999999, BUDGET_OK),       # um micro-dólar abaixo do aviso
        (8.0, BUDGET_WARN),          # exatamente 80%
        (8.000001, BUDGET_WARN),
        (9.999999, BUDGET_WARN),     # um micro-dólar abaixo do teto
        (10.0, BUDGET_EXCEEDED),     # exatamente o teto = esgotado
        (10.000001, BUDGET_EXCEEDED),
        (250.0, BUDGET_EXCEEDED),
    ],
)
def test_boundaries_of_a_ten_dollar_cap(total, state):
    assert BudgetGuard(10.0).check(total).state == state


def test_float_drift_does_not_move_a_total_across_a_threshold():
    # em vírgula flutuante: 0.1 × 0.8 = 0.08000000000000002 (> 0.08) e
    # 0.7 + 0.1 = 0.7999999999999999 (< 0.8) — o dinheiro tem 6 casas, não 17
    assert 0.1 * 0.8 > 0.08 and 0.7 + 0.1 < 0.8
    assert BudgetGuard(0.1, warn_fraction=0.8).check(0.08).state == BUDGET_WARN
    assert BudgetGuard(0.2, warn_fraction=0.8).check(0.16).state == BUDGET_WARN
    assert BudgetGuard(0.75, warn_fraction=0.8).check(0.6).state == BUDGET_WARN
    assert BudgetGuard(0.8).check(0.7 + 0.1).state == BUDGET_EXCEEDED
    assert BudgetGuard(0.3).check(0.1 + 0.2).state == BUDGET_EXCEEDED
    # e um micro-dólar abaixo continua abaixo
    assert BudgetGuard(0.1, warn_fraction=0.8).check(0.079999).state == BUDGET_OK
    assert BudgetGuard(0.8).check(0.799999).state == BUDGET_WARN


def test_fraction_reaches_one_only_when_the_cap_is_actually_reached():
    guard = BudgetGuard(10.0)

    assert guard.check(9.999999).fraction < 1.0
    assert guard.check(10.0).fraction == 1.0
    assert guard.check(12.5).fraction == 1.25
    assert guard.check(5.0).fraction == 0.5


def test_money_is_rounded_to_six_decimals():
    status = BudgetGuard(10.0).check(1.23456789)

    assert status.total_usd == 1.234568
    assert money(0.1 + 0.2) == 0.3
    assert money(None) == 0.0 and money("x") == 0.0 and money(float("nan")) == 0.0


@pytest.mark.parametrize("total", [None, float("nan"), float("inf"), float("-inf"), -3, "abc", object()])
def test_a_junk_total_never_blocks_nor_raises(total):
    status = BudgetGuard(10.0).check(total)

    assert status.state == BUDGET_OK
    assert status.total_usd == 0.0
    json.dumps(status.as_dict())  # o evento tem de ser JSON válido (sem Infinity/NaN)


def test_remaining_never_goes_negative():
    guard = BudgetGuard(2.0)

    assert guard.check(0.5).remaining_usd == 1.5
    assert guard.check(2.0).remaining_usd == 0.0
    assert guard.check(9.0).remaining_usd == 0.0


# --------------------------------------------------------------- warn_fraction


@pytest.mark.parametrize("value", [0, -0.2, "abc", None, float("nan"), 1.5 * math.inf])
def test_an_invalid_warn_fraction_falls_back_to_the_default(value):
    assert BudgetGuard(10.0, warn_fraction=value).warn_fraction == DEFAULT_WARN_FRACTION


def test_a_custom_warn_fraction_moves_the_warning_threshold():
    guard = BudgetGuard(10.0, warn_fraction=0.5)

    assert guard.check(4.999999).state == BUDGET_OK
    assert guard.check(5.0).state == BUDGET_WARN


def test_warn_fraction_one_means_no_warning_only_exceeded():
    guard = BudgetGuard(10.0, warn_fraction=1.0)

    assert guard.check(9.999999).state == BUDGET_OK
    assert guard.check(10.0).state == BUDGET_EXCEEDED


def test_a_warn_fraction_above_one_is_clamped_to_one():
    assert BudgetGuard(10.0, warn_fraction=7).warn_fraction == 1.0


# --------------------------------------------------------------------- mensagens


def test_messages_are_portuguese_and_tell_what_to_do():
    guard = BudgetGuard(5.0)

    ok = guard.check(1.0)
    warn = guard.check(4.25)
    exceeded = guard.check(5.0)

    assert "dentro do teto" in ok.message_pt
    assert "Atenção" in warn.message_pt and "85%" in warn.message_pt
    assert "restam US$ 0.75" in warn.message_pt
    assert "esgotado" in exceeded.message_pt and "retidas" in exceeded.message_pt
    assert "US$ 5.00 de US$ 5.00" in exceeded.message_pt


def test_only_exceeded_holds_the_actions():
    guard = BudgetGuard(1.0)

    assert [guard.check(t).holds_actions for t in (0.1, 0.9, 1.0)] == [False, False, True]
    assert [guard.check(t).state for t in (0.1, 0.9, 1.0)] == list(BUDGET_STATES)


def test_as_dict_is_the_json_payload_of_the_budget_event():
    payload = BudgetGuard(4.0).check(3.5).as_dict()

    assert json.loads(json.dumps(payload)) == payload
    assert set(payload) == {
        "state", "enabled", "fraction", "total_usd", "limit_usd", "remaining_usd", "message_pt",
    }
    assert payload["state"] == BUDGET_WARN and payload["limit_usd"] == 4.0


# ------------------------------------------------------------------ transições


def test_poll_flags_only_the_transitions():
    guard = BudgetGuard(1.0)
    totals = (0.1, 0.2, 0.85, 0.9, 1.2, 1.3)

    polled = [guard.poll(total) for total in totals]

    assert [status.state for status in polled] == [
        "ok", "ok", "warn", "warn", "exceeded", "exceeded",
    ]
    # o evento só sai quando o estado muda (ok→warn, warn→exceeded), não a cada passo
    assert [status.changed for status in polled] == [False, False, True, False, True, False]


def test_poll_announces_a_guard_that_starts_already_over_the_cap():
    guard = BudgetGuard(1.0)

    first = guard.poll(5.0)

    assert first.exceeded and first.changed is True


def test_poll_announces_a_recovery_and_reset_re_announces():
    guard = BudgetGuard(1.0)
    guard.poll(2.0)

    back = guard.poll(0.1)  # o humano subiu o teto / o ledger foi limpo
    assert back.ok and back.changed is True

    guard.poll(0.1)
    guard.reset()
    assert guard.poll(2.0).changed is True


def test_check_is_pure_and_does_not_record_transitions():
    guard = BudgetGuard(1.0)

    guard.check(5.0)

    assert guard.poll(0.1).changed is False  # nada ficou guardado pelo check()


# ------------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (5, 5.0), (5.5, 5.5), ("5", 5.0), (" 2,5 ", 2.5), ("0.000133", 0.000133),
        (1.23456789, 1.234568),
        (None, None), (0, None), ("0", None), ("", None), ("off", None), ("OFF", None),
        (-1, None), ("-3", None), ("abc", None), (float("nan"), None), (float("inf"), None),
        (True, None), ([], None), ({}, None),
    ],
)
def test_parse_limit_is_tolerant(raw, expected):
    assert parse_limit(raw) == expected


def test_a_cap_below_one_micro_dollar_is_disabled():
    assert parse_limit(0.0000001) is None


def test_from_settings_reads_a_mapping_an_object_and_nothing():
    from_mapping = BudgetGuard.from_settings({"budget_limit_usd": "7.5", "budget_warn_fraction": 0.6})
    from_object = BudgetGuard.from_settings(SimpleNamespace(budget_limit_usd=3, budget_warn_fraction=None))
    nothing = BudgetGuard.from_settings(None)

    assert (from_mapping.limit_usd, from_mapping.warn_fraction) == (7.5, 0.6)
    assert (from_object.limit_usd, from_object.warn_fraction) == (3.0, DEFAULT_WARN_FRACTION)
    assert nothing.enabled is False


def test_status_is_a_frozen_value():
    status = BudgetGuard(1.0).check(0.5)

    assert isinstance(status, BudgetStatus)
    with pytest.raises(Exception):
        status.state = "exceeded"  # type: ignore[misc]


def test_level_is_an_alias_of_state():
    guard = BudgetGuard(1.0)

    assert [guard.check(t).level for t in (0.1, 0.9, 1.0)] == ["ok", "warn", "exceeded"]


def test_repr_is_safe_and_informative():
    assert "limit_usd=2.5" in repr(BudgetGuard(2.5))
