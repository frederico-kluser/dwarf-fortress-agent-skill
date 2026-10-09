"""FastAPI + WebSocket backend implementing the frozen API contract.

* ``main.create_app()`` — application factory (``app`` is the module-level app
  for uvicorn).
* ``routes/`` — one module per area of ``docs/api.md``.
* ``ws.py`` + ``bus.py`` — per-campaign realtime channel and its in-memory bus.
* ``state.AppServices`` — the shared services object other pieces publish to.
* ``runtime.py`` — live per-campaign store; ``stub`` payloads until the agent
  loop is wired.
"""

from __future__ import annotations

__all__ = ["create_app", "main"]


def __getattr__(name: str):  # lazy: importing the package must not build the app
    if name in ("create_app", "main"):
        from . import main as _main

        return _main.create_app if name == "create_app" else _main
    raise AttributeError(name)
