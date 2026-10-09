"""FastAPI application factory + startup.

Run it with::

    .venv/bin/python -m uvicorn system_one_dwarf.app.main:app --port 8799

The campaign root (``campanhas/``) is **runtime data**: gitignored, created on
startup, never committed.

Other pieces plug in here:

* ``create_app(installer=...)`` / ``cli_factory=...`` — test seams (the real CoALA
  installer/CLI are subprocesses).
* ``create_app(translator=...)`` — an explicit PT<->EN translator for **every**
  campaign.  Left out, each campaign gets the real one built from its own stored
  key (``KeyStore.get_key("openrouter")`` →
  ``system_one_dwarf.translate.build_translator``); with no key that is the
  documented identity behaviour (text stored as written, ``*_pt`` mirrors EN).
* ``create_app(translator_factory=...)`` — how that per-campaign translator is
  built (test seam; defaults to ``build_translator``).
* ``create_app(loop_builder=...)`` — per-campaign decision-loop builder (test
  seam; defaults to :func:`system_one_dwarf.app.loop_runner.build_loop`, the real
  ``AgentLoop`` with every collaborator wired).
* ``app.state.services.publish_event(campaign_id, type, payload)`` — the single
  entry point that keeps the REST reads and the WS channel in sync.

Lifecycle: at startup every known campaign is synced once — ``autopilot`` /
``checkpoint`` start its decision loop, ``paused``/kill-switch leave it stopped,
and **without keys nothing starts** (the API keeps serving its stub payloads).
At shutdown every loop is stopped.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .. import __version__
from ..campaign import CampaignManager, Translator
from .loop_runner import DEFAULT_STOP_TIMEOUT
from .routes import all_routers
from .state import AppServices
from .translator import TranslatorFactory
from . import ws as ws_module

log = logging.getLogger("system_one_dwarf.app")

API_TITLE = "System One Dwarf — API"
API_DESCRIPTION = (
    "Backend do agente que joga Dwarf Fortress (adventure mode). "
    "Contrato canónico em docs/api.md."
)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    services: AppServices = app.state.services
    services.bus.bind_loop(asyncio.get_running_loop())
    root = services.manager.ensure_root()
    log.info("campaigns root ready at %s", root)
    try:
        for status in services.sync_all_loops():
            log.info(
                "loop of campaign %s: running=%s reason=%s",
                status.get("campaign_id"), status.get("running"), status.get("reason"),
            )
    except Exception:  # a broken campaign must never block boot
        log.warning("could not sync the decision loops at startup", exc_info=True)
    yield
    log.info("shutting down")
    services.shutdown_loops()


def create_app(
    *,
    campaigns_root: Path | str | None = None,
    installer: Any | None = None,
    cli_factory: Callable[[Path, str], Any] | None = None,
    translator: Translator | None = None,
    translator_factory: TranslatorFactory | None = None,
    loop_builder: Callable[..., Any] | None = None,
    loop_stop_timeout: float = DEFAULT_STOP_TIMEOUT,
    python: str | None = None,
) -> FastAPI:
    """Build the application (all state lives on ``app.state.services``)."""
    manager = CampaignManager(
        root=campaigns_root,
        installer=installer,
        python=python,
        translator=translator,
        cli_factory=cli_factory,
    )
    services = AppServices(
        manager=manager,
        translator=translator,
        translator_factory=translator_factory,
        loop_builder=loop_builder,
        loop_stop_timeout=loop_stop_timeout,
    )

    app = FastAPI(
        title=API_TITLE,
        description=API_DESCRIPTION,
        version=__version__,
        lifespan=_lifespan,
    )
    app.state.services = services

    # The MUI front-end runs on another port during development.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Source"],
    )

    for router in all_routers():
        app.include_router(router)
    app.include_router(ws_module.router)
    return app


app = create_app()
