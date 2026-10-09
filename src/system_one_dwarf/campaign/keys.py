"""Per-campaign API-key storage.

Rules (authoritative):

* One dedicated SQLite file per campaign: ``campanhas/<slug>/chaves.sqlite``.
* The campaign directory is 0700 and the SQLite file is 0600 (forced on every
  open, so a pre-existing file with a loose mode gets tightened).
* Providers: ``openrouter``, ``typesafe``, ``deepseek``.
* A full key is **never** returned through any interface: only the masked form
  (``sk-or-v1-••••••••3f9a``) leaves this module.  ``get_key()`` is the only way
  code obtains a key in memory, and key material is never logged.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from .clock import utc_now_iso
from .paths import PRIVATE_FILE_MODE, ensure_private_dir

PROVIDERS: tuple[str, ...] = ("openrouter", "typesafe", "deepseek")

MASK_BULLETS = "••••••••"
#: Number of leading key characters kept visible (covers ``sk-or-v1-``).
MASK_PREFIX_CHARS = 9
MASK_TAIL_CHARS = 4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS keys (
    provider   TEXT PRIMARY KEY,
    key        TEXT NOT NULL,
    masked     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class UnknownProvider(ValueError):
    """Raised when a provider outside :data:`PROVIDERS` is used."""


class InvalidKey(ValueError):
    """Raised when an empty/blank key is submitted."""


def validate_provider(provider: str) -> str:
    value = (provider or "").strip().lower()
    if value not in PROVIDERS:
        raise UnknownProvider(
            f"unknown provider {provider!r}; expected one of {', '.join(PROVIDERS)}"
        )
    return value


def mask_key(key: str) -> str:
    """Return the only representation of a key that may cross an interface.

    ``sk-or-v1-0123456789abcdef3f9a`` -> ``sk-or-v1-••••••••3f9a``.
    Keys too short to mask safely collapse to bullets.
    """
    key = key or ""
    if not key:
        return MASK_BULLETS
    if len(key) <= MASK_TAIL_CHARS:
        return MASK_BULLETS
    tail = key[-MASK_TAIL_CHARS:]
    visible = min(MASK_PREFIX_CHARS, max(0, len(key) - MASK_TAIL_CHARS - len(MASK_BULLETS)))
    return f"{key[:visible]}{MASK_BULLETS}{tail}"


class KeyStore:
    """Key storage bound to one campaign directory.

    A connection is opened per operation: the object stays cheap and safe to
    share across threads (uvicorn runs sync endpoints in a threadpool).
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    # ------------------------------------------------------------------ plumbing

    def _connect(self) -> sqlite3.Connection:
        ensure_private_dir(self.db_path.parent)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        conn.commit()
        # SQLite creates the file with the process umask; force 0600.
        if self.db_path.exists():
            os.chmod(self.db_path, PRIVATE_FILE_MODE)
        return conn

    def __repr__(self) -> str:  # never leak key material through repr/logs
        return f"KeyStore(db_path={str(self.db_path)!r}, providers={list(PROVIDERS)})"

    # --------------------------------------------------------------------- write

    def set_key(self, provider: str, key: str, *, ts: str | None = None) -> dict[str, Any]:
        """Store (or replace) ``key`` for ``provider``; returns the masked record."""
        provider = validate_provider(provider)
        key = (key or "").strip()
        if not key:
            raise InvalidKey("key must not be empty")
        masked = mask_key(key)
        now = ts or _utc_now()
        conn = self._connect()
        try:
            existing = conn.execute(
                "SELECT created_at FROM keys WHERE provider = ?", (provider,)
            ).fetchone()
            created = existing["created_at"] if existing else now
            conn.execute(
                "INSERT INTO keys (provider, key, masked, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(provider) DO UPDATE SET key = excluded.key, "
                "masked = excluded.masked, updated_at = excluded.updated_at",
                (provider, key, masked, created, now),
            )
            conn.commit()
        finally:
            conn.close()
        return {"provider": provider, "masked_key": masked, "set": True}

    def delete_key(self, provider: str) -> bool:
        provider = validate_provider(provider)
        conn = self._connect()
        try:
            cur = conn.execute("DELETE FROM keys WHERE provider = ?", (provider,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    # ---------------------------------------------------------------------- read

    def get_key(self, provider: str) -> str | None:
        """Return the **full** key — the single in-memory accessor for secrets."""
        provider = validate_provider(provider)
        if not self.db_path.exists():
            return None
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT key FROM keys WHERE provider = ?", (provider,)
            ).fetchone()
        finally:
            conn.close()
        return row["key"] if row else None

    def has_key(self, provider: str) -> bool:
        provider = validate_provider(provider)
        if not self.db_path.exists():
            return False
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM keys WHERE provider = ?", (provider,)
            ).fetchone()
        finally:
            conn.close()
        return row is not None

    def masked_key(self, provider: str) -> str | None:
        provider = validate_provider(provider)
        if not self.db_path.exists():
            return None
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT masked FROM keys WHERE provider = ?", (provider,)
            ).fetchone()
        finally:
            conn.close()
        return row["masked"] if row else None

    def list_masked(self) -> list[dict[str, Any]]:
        """Every provider in canonical order, masked form only."""
        return [
            {
                "provider": provider,
                "masked_key": self.masked_key(provider),
                "set": self.has_key(provider),
            }
            for provider in PROVIDERS
        ]


def _utc_now() -> str:
    return utc_now_iso()
