"""Application services shared by the REST routes and the WebSocket channel.

``create_app()`` builds one :class:`AppServices` and stores it on
``app.state.services``.  Other pieces reach the live bus and the campaign stores
through it::

    services = app.state.services
    await services.publish_event(campaign_id, "decision", decision)

which keeps ``GET /api/campaigns/{id}/decisions`` and the WS ``decision`` event
in sync with a single call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..campaign import (
    Campaign,
    CampaignManager,
    IdentityTranslator,
    KeyStore,
    MemoryStore,
    ObjectiveStore,
    SpendLedger,
    Translator,
)
from .bus import EventBus
from .runtime import CampaignRuntime

log = logging.getLogger("system_one_dwarf.app.state")


@dataclass
class AppServices:
    """Manager + bus + live runtimes + translator (the app's whole backend state)."""

    manager: CampaignManager
    bus: EventBus = field(default_factory=EventBus)
    translator: Translator = field(default_factory=IdentityTranslator)
    runtimes: dict[str, CampaignRuntime] = field(default_factory=dict)

    # ------------------------------------------------------------------ campaigns

    def resolve(self, campaign_id: str) -> Campaign:
        """Campaign by opaque id (or slug); raises :class:`CampaignNotFound`."""
        return self.manager.require(campaign_id)

    def runtime(self, campaign_id: str) -> CampaignRuntime:
        runtime = self.runtimes.get(campaign_id)
        if runtime is None:
            runtime = CampaignRuntime(campaign_id=campaign_id)
            self.runtimes[campaign_id] = runtime
        return runtime

    def drop_runtime(self, campaign_id: str) -> None:
        self.runtimes.pop(campaign_id, None)

    # ----------------------------------------------------------------- sub-stores

    def keys(self, campaign: Campaign) -> KeyStore:
        return self.manager.keys(campaign)

    def spend(self, campaign: Campaign) -> SpendLedger:
        return self.manager.spend(campaign)

    def memory(self, campaign: Campaign) -> MemoryStore:
        return self.manager.memory(campaign, self.translator)

    def objective(self, campaign: Campaign) -> ObjectiveStore:
        return self.manager.objective(campaign, self.translator)

    # -------------------------------------------------------------------- events

    def apply_event(self, campaign_id: str, event_type: str, payload: dict[str, Any]) -> None:
        """Mirror a live event into the REST-visible runtime store."""
        runtime = self.runtime(campaign_id)
        try:
            if event_type == "metrics_update":
                runtime.set_metrics(payload)
            elif event_type == "screen_translation":
                runtime.set_screen(str(payload.get("translated_pt") or ""), ts=payload.get("ts"))
            elif event_type == "decision":
                runtime.add_decision(payload)
            elif event_type == "question_new":
                runtime.add_question(payload)
        except Exception:  # pragma: no cover - never let a bad event kill the loop
            log.warning("could not mirror %s into the runtime store", event_type, exc_info=True)

    async def publish_event(
        self, campaign_id: str, event_type: str, payload: dict[str, Any] | None = None
    ) -> int:
        """Update the runtime store and fan the envelope out to WS clients."""
        payload = dict(payload or {})
        self.apply_event(campaign_id, event_type, payload)
        return await self.bus.publish(campaign_id, event_type, payload)

    def publish_event_threadsafe(
        self, campaign_id: str, event_type: str, payload: dict[str, Any] | None = None
    ) -> None:
        """Same as :meth:`publish_event` for worker threads / sync callbacks."""
        payload = dict(payload or {})
        self.apply_event(campaign_id, event_type, payload)
        self.bus.publish_threadsafe(campaign_id, event_type, payload)
