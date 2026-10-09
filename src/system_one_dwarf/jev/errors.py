"""Typed exceptions and the warning category of the Jev fleet client.

Every error raised by this package derives from :class:`JevError`, so callers can
catch one type. The HTTP layer maps OpenRouter status codes to these types:

===========  =====================  =========================================
Status       Exception              Retried?
===========  =====================  =========================================
401          ``JevAuthError``       never (terminal: bad/missing API key)
402          ``JevQuotaError``      never (terminal: no credit left)
422          ``JevRequestError``    never (the request itself is invalid)
429 / 529    retried, then          yes, with backoff honouring Retry-After
             ``JevTransportError``
5xx          retried, then          yes (transient upstream failure)
             ``JevTransportError``
timeout /    retried, then          yes
network      ``JevTransportError``
===========  =====================  =========================================

Local request validation (schema.py / validate.py) also raises
``JevRequestError``: a request that cannot be right is never sent, so no tokens
are spent on it.

Judgement calls that are suspicious but legal (a ``choice`` without an exit
option, numeric-looking ``score`` levels, ...) never raise: they emit
:class:`JevWarning` through the standard :mod:`warnings` machinery and are
collected by :func:`system_one_dwarf.jev.validate.validate_request`.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "JevError",
    "JevAuthError",
    "JevQuotaError",
    "JevRequestError",
    "JevTransportError",
    "JevWarning",
]


class JevError(Exception):
    """Base class for every error raised by the Jev client package."""

    def __init__(
        self,
        message: str = "",
        *,
        status_code: int | None = None,
        detail: Any = None,
        attempts: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        #: Server-provided explanation (already safe to log: no credentials).
        self.detail = detail
        #: How many HTTP attempts were made before giving up, when relevant.
        self.attempts = attempts

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


class JevAuthError(JevError):
    """HTTP 401 — the API key is missing, malformed or revoked. Terminal."""


class JevQuotaError(JevError):
    """HTTP 402 — the account has no credit. Terminal: retrying cannot help."""


class JevRequestError(JevError):
    """The request is invalid (local validation or HTTP 4xx). Never retried."""


class JevTransportError(JevError):
    """Network failure or exhausted retries (429/529/5xx/timeouts)."""


class JevWarning(UserWarning):
    """A suspicious-but-legal request shape; emitted by the builders."""
