"""Timestamp helpers — one single ISO-8601 UTC convention with a ``Z`` suffix.

The API contract fixes ``2026-09-27T19:44:03.512Z`` (millisecond precision,
UTC, literal ``Z``).  The CoALA CLI emits ``+00:00`` offsets, so everything that
crosses an interface goes through :func:`to_z`.
"""

from __future__ import annotations

from datetime import datetime, timezone

__all__ = ["utc_now", "utc_now_iso", "to_z", "parse_iso"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def utc_now_iso() -> str:
    """Current UTC time, e.g. ``2026-09-27T19:44:03.512Z``."""
    return _format(utc_now())


def parse_iso(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp (``Z`` or offset form); ``None`` on failure."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def to_z(value: str | datetime | None, *, fallback: str | None = None) -> str | None:
    """Normalise any ISO timestamp to the contract form (``...Z``)."""
    if value is None:
        return fallback
    if isinstance(value, datetime):
        return _format(value)
    parsed = parse_iso(value)
    if parsed is None:
        return fallback if fallback is not None else value
    return _format(parsed)
