"""FastAPI + WebSocket backend implementing the frozen API contract.

* ``main.create_app()`` — application factory (``app`` is the module-level app
  for uvicorn).
* ``routes/`` — one module per area of ``docs/api.md``.
* ``ws.py`` + ``bus.py`` — per-campaign realtime channel and its in-memory bus.
* ``state.AppServices`` — the shared services object other pieces publish to;
  it owns the per-campaign translator, the loop runners and the event mirroring.
* ``runtime.py`` — live per-campaign store (questions *with provenance*,
  approvals, decisions, inputs, metrics); ``stub`` payloads until the agent loop
  runs.
* ``loop_runner.py`` — one ``AgentLoop`` per campaign, started/stopped from the
  control mode, with every real collaborator wired and an ``EventSink`` that
  feeds the stores + the WS channel.  Without API keys the loop stays stopped.
* ``translator.py`` — the real DeepSeek translator built from the campaign's
  stored OpenRouter key, identity without one.
* ``approval_flow.py`` — approve/reject of a checkpoint approval, reusing the
  question-answer path (memory + events).
"""

from __future__ import annotations

__all__ = ["create_app", "main"]


def __getattr__(name: str):  # lazy: importing the package must not build the app
    if name in ("create_app", "main"):
        from . import main as _main

        return _main.create_app if name == "create_app" else _main
    raise AttributeError(name)
