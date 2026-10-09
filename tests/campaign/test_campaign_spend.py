"""Spend ledger: table shape, aggregation math and the contract payload."""

from __future__ import annotations

import sqlite3
import stat
from pathlib import Path

import pytest

from system_one_dwarf.campaign import SpendLedger, money
from system_one_dwarf.campaign.paths import PRIVATE_FILE_MODE


@pytest.fixture
def ledger(tmp_path: Path) -> SpendLedger:
    return SpendLedger(tmp_path / "gastos.sqlite")


class TestSchema:
    def test_table_columns_are_the_canonical_ones(self, ledger):
        ledger.record("typesafe", "typesafe/jev-1.13")
        conn = sqlite3.connect(ledger.db_path)
        columns = [row[1] for row in conn.execute("PRAGMA table_info(spend)")]
        conn.close()
        assert columns == [
            "ts",
            "provider",
            "model",
            "cost_usd",
            "input_tokens",
            "output_tokens",
            "reason",
        ]

    def test_file_is_0600(self, ledger):
        ledger.record("typesafe", "typesafe/jev-1.13")
        assert stat.S_IMODE(ledger.db_path.stat().st_mode) == PRIVATE_FILE_MODE


class TestRecord:
    def test_record_returns_the_stored_row(self, ledger):
        row = ledger.record(
            "Typesafe",
            "typesafe/jev-1.13",
            cost_usd=0.0423184,
            input_tokens=1007714,
            reason="jev-a",
        )
        assert row["provider"] == "typesafe"
        assert row["model"] == "typesafe/jev-1.13"
        assert row["cost_usd"] == 0.042318  # 6 decimals
        assert row["input_tokens"] == 1007714
        assert row["output_tokens"] == 0
        assert row["reason"] == "jev-a"
        assert row["ts"].endswith("Z")

    def test_defaults_are_zeroed(self, ledger):
        row = ledger.record("openrouter", "deepseek/deepseek-v4.1-flash")
        assert row["cost_usd"] == 0.0
        assert row["input_tokens"] == 0 and row["output_tokens"] == 0
        assert row["reason"] is None

    def test_entries_are_newest_first(self, ledger):
        ledger.record("typesafe", "m1", reason="first")
        ledger.record("typesafe", "m1", reason="second")
        assert [row["reason"] for row in ledger.entries()] == ["second", "first"]
        assert ledger.calls() == 2


class TestTotals:
    def test_empty_ledger_is_zeroed(self, ledger):
        totals = ledger.totals()
        assert totals["total_usd"] == 0.0
        assert totals["providers"] == []
        assert totals["ts"].endswith("Z")

    def test_groups_by_provider_and_model(self, ledger):
        ledger.record("typesafe", "typesafe/jev-1.13", cost_usd=0.04, input_tokens=1_000_000)
        ledger.record("typesafe", "typesafe/jev-1.13", cost_usd=0.002318, input_tokens=7714)
        ledger.record(
            "openrouter",
            "deepseek/deepseek-v4.1-flash",
            cost_usd=0.000123,
            input_tokens=8000,
            output_tokens=2100,
        )

        totals = ledger.totals()

        assert totals["total_usd"] == round(0.04 + 0.002318 + 0.000123, 6)
        by_provider = {p["provider"]: p for p in totals["providers"]}
        assert set(by_provider) == {"typesafe", "openrouter"}

        typesafe = by_provider["typesafe"]["models"][0]
        assert typesafe == {
            "model": "typesafe/jev-1.13",
            "calls": 2,
            "input_tokens": 1_007_714,
            "output_tokens": 0,
            "cost_usd": 0.042318,
        }
        openrouter = by_provider["openrouter"]["models"][0]
        assert openrouter["calls"] == 1
        assert openrouter["output_tokens"] == 2100
        assert openrouter["cost_usd"] == 0.000123

    def test_providers_and_models_are_ordered_by_cost(self, ledger):
        ledger.record("openrouter", "cheap", cost_usd=0.000001)
        ledger.record("typesafe", "expensive", cost_usd=1.5)
        ledger.record("typesafe", "cheaper", cost_usd=0.2)

        totals = ledger.totals()

        assert [p["provider"] for p in totals["providers"]] == ["typesafe", "openrouter"]
        assert [m["model"] for m in totals["providers"][0]["models"]] == ["expensive", "cheaper"]

    def test_total_matches_the_sum_of_models(self, ledger):
        for cost in (0.1, 0.2, 0.3):
            ledger.record("typesafe", "typesafe/jev-1.13", cost_usd=cost)
        assert ledger.total_usd() == ledger.totals()["total_usd"] == 0.6

    def test_rounding_is_six_decimals(self, ledger):
        ledger.record("typesafe", "m", cost_usd=0.0000004)
        assert ledger.totals()["total_usd"] == 0.0
        ledger.record("typesafe", "m", cost_usd=0.0000016)
        assert ledger.totals()["total_usd"] == 0.000002


class TestMoney:
    @pytest.mark.parametrize(
        "value,expected",
        [(None, 0.0), (0, 0.0), (0.04231849, 0.042318), (1.0000005, 1.000001), ("0.5", 0.5)],
    )
    def test_money_rounds(self, value, expected):
        assert money(value) == expected
