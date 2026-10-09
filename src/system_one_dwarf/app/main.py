"""FastAPI application factory + startup.

Run it with::

    .venv/bin/python -m uvicorn system_one_dwarf.app.main:app --port 8799

The campaign root (``campanhas/``) is **runtime data**: gitignored, created on
startup, never committed.

Other pieces plug in here:

* ``create_app(installer=...)`` / ``cli_factory=...`` — test seams (the real CoALA
  installer/CLI are subprocesses).
* ``create_app(translator=...)`` — the DeepSeek PT<->EN translator
  (``campaign.memory.Translator`` protocol; identity until plugged in).
* ``app.state.services.publish_event(campaign_id, type, payload)`` — the single
  entry point that keeps the REST reads and the WS channel in sync.
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
from ..campaign import CampaignManager, IdentityTranslator, Translator
from .routes import all_routers
from .state import AppServices
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
    yield
    log.info("shutting down")


def create_app(
    *,
    campaigns_root: Path | str | None = None,
    installer: Any | None = None,
    cli_factory: Callable[[Path, str], Any] | None = None,
    translator: Translator | None = None,
    python: str | None = None,
) -> FastAPI:
    """Build the application (all state lives on ``app.state.services``)."""
    active_translator = translator or IdentityTranslator()
    manager = CampaignManager(
        root=campaigns_root,
        installer=installer,
        python=python,
        translator=active_translator,
        cli_factory=cli_factory,
    )
    services = AppServices(manager=manager, translator=active_translator)

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
