"""Pytest wiring for ``tests/translate``.

Two jobs:

* make ``src/`` importable (the package is not installed in the venv);
* guarantee the suite is **offline**: an autouse fixture replaces
  ``httpx.Client``/``httpx.post``/``httpx.get``/``httpx.request`` and
  ``socket`` connections with hard failures, so a test that tried to touch the
  network would fail loudly instead of hanging or passing slowly.

Test doubles and payload builders live in ``dwarf_translate_fakes.py`` (a
project-unique module name, so it cannot collide with another piece's helpers).
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:  # the package is not installed in the venv
    sys.path.insert(0, str(SRC))

from system_one_dwarf.translate import Glossary  # noqa: E402


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any attempt to open a real connection fails the test loudly."""

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network access is forbidden: tests must run offline")

    monkeypatch.setattr(httpx, "Client", _boom)
    monkeypatch.setattr(httpx, "post", _boom)
    monkeypatch.setattr(httpx, "get", _boom)
    monkeypatch.setattr(httpx, "request", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket.socket, "connect", _boom)


@pytest.fixture
def glossary() -> Glossary:
    """The real, read-only project glossary (``dfhack/glossary-pt-en.tsv``)."""
    return Glossary.load()
