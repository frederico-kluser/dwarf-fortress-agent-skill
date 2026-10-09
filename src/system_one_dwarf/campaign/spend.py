"""Per-campaign spend ledger (no budget cap — visibility only).

Table (canonical)::

    spend(ts, provider, model, cost_usd REAL, input_tokens, output_tokens, reason)

The ledger lives in ``campanhas/<slug>/gastos.sqlite``.  ``totals()`` produces
exactly the payload of ``GET /api/campaigns/{id}/spend``:

    {"total_usd": float, "providers": [{"provider": str, "models": [...]}], "ts": str}

Money is always a float rounded to 6 decimals (contract).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from .clock import utc_now_iso
from .paths import PRIVATE_FILE_MODE, ensure_private_dir

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spend (
    ts            TEXT    NOT NULL,
    provider      TEXT    NOT NULL,
    model         TEXT    NOT NULL,
    cost_usd      REAL    NOT NULL DEFAULT 0,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    reason        TEXT
);
CREATE INDEX IF NOT EXISTS spend_provider_model ON spend (provider, model);
"""


def money(value: float | int | None) -> float:
    """Contract money format: float with 6 decimals."""
    if value is None:
        return 0.0
    return round(float(value), 6)


class SpendLedger:
    """Append-only spend ledger bound to one campaign directory."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        ensure_private_dir(self.db_path.parent)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        conn.commit()
        if self.db_path.exists():
            os.chmod(self.db_path, PRIVATE_FILE_MODE)
        return conn

    # --------------------------------------------------------------------- write

    def record(
        self,
        provider: str,
        model: str,
        *,
        cost_usd: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        reason: str | None = None,
        ts: str | None = None,
    ) -> dict[str, Any]:
        """Append one call to the ledger and return the stored row."""
        row = {
            "ts": ts or utc_now_iso(),
            "provider": (provider or "").strip().lower(),
            "model": (model or "").strip(),
            "cost_usd": float(cost_usd or 0.0),
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "reason": reason,
        }
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO spend (ts, provider, model, cost_usd, input_tokens, "
                "output_tokens, reason) VALUES (:ts, :provider, :model, :cost_usd, "
                ":input_tokens, :output_tokens, :reason)",
                row,
            )
            conn.commit()
        finally:
            conn.close()
        row["cost_usd"] = money(row["cost_usd"])
        return row

    # ---------------------------------------------------------------------- read

    def entries(self, limit: int = 100) -> list[dict[str, Any]]:
        """Most recent ledger rows first (debug/tests; not part of the contract)."""
        if not self.db_path.exists():
            return []
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT rowid AS id, ts, provider, model, cost_usd, input_tokens, "
                "output_tokens, reason FROM spend ORDER BY rowid DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        finally:
            conn.close()
        out = []
        for row in rows:
            item = dict(row)
            item["cost_usd"] = money(item["cost_usd"])
            out.append(item)
        return out

    def calls(self) -> int:
        if not self.db_path.exists():
            return 0
        conn = self._connect()
        try:
            return int(conn.execute("SELECT COUNT(*) AS n FROM spend").fetchone()["n"])
        finally:
            conn.close()

    def total_usd(self) -> float:
        if not self.db_path.exists():
            return 0.0
        conn = self._connect()
        try:
            row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) AS t FROM spend").fetchone()
        finally:
            conn.close()
        return money(row["t"])

    def totals(self) -> dict[str, Any]:
        """Full ``GET /api/campaigns/{id}/spend`` payload."""
        grouped: dict[str, dict[str, dict[str, Any]]] = {}
        if self.db_path.exists():
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT provider, model, COUNT(*) AS calls, "
                    "COALESCE(SUM(input_tokens), 0) AS input_tokens, "
                    "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
                    "COALESCE(SUM(cost_usd), 0) AS cost_usd "
                    "FROM spend GROUP BY provider, model"
                ).fetchall()
            finally:
                conn.close()
            for row in rows:
                provider = row["provider"] or ""
                model = row["model"] or ""
                grouped.setdefault(provider, {})[model] = {
                    "model": model,
                    "calls": int(row["calls"]),
                    "input_tokens": int(row["input_tokens"]),
                    "output_tokens": int(row["output_tokens"]),
                    "cost_usd": money(row["cost_usd"]),
                }

        providers = []
        for provider in grouped:
            models = sorted(
                grouped[provider].values(),
                key=lambda m: (-m["cost_usd"], m["model"]),
            )
            providers.append({"provider": provider, "models": models})
        providers.sort(key=lambda p: (-sum(m["cost_usd"] for m in p["models"]), p["provider"]))

        return {
            "total_usd": money(sum(m["cost_usd"] for p in providers for m in p["models"])),
            "providers": providers,
            "ts": utc_now_iso(),
        }
