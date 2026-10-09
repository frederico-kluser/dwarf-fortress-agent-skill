"""Application services shared by the REST routes and the WebSocket channel.

``create_app()`` builds one :class:`AppServices` and stores it on
``app.state.services``.  Other pieces reach the live bus and the campaign stores
through it::

    services = app.state.services
    await services.publish_event(campaign_id, "decision", decision)

which keeps ``GET /api/campaigns/{id}/decisions`` and the WS ``decision`` event
in sync with a single call.

Three seams live here on purpose, because they are *app* policy:

* :meth:`translator_for` — the campaign's translator: an explicitly injected one
  for every campaign, otherwise a per-campaign
  :class:`~system_one_dwarf.app.translator.CampaignTranslator` that builds the
  real DeepSeek translator from ``KeyStore.get_key("openrouter")`` and degrades
  to identity without a key;
* :meth:`loop_runner` / :meth:`sync_loop` — the decision loop of each campaign
  (started on ``autopilot``/``checkpoint``, stopped on ``paused``/kill-switch,
  never started without keys);
* :meth:`apply_event` — mirroring a live event into the REST-visible stores,
  including the additive approvals queue derived from the loop's checkpoint
  questions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

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
from .loop_runner import DEFAULT_STOP_TIMEOUT, LoopRunner, build_loop
from .runtime import (
    APPROVAL_PENDING,
    SOURCE_LIVE,
    SOURCE_STUB,
    CampaignRuntime,
    normalize_question,
)
from .translator import (
    TRANSLATOR_PROVIDER,
    CampaignTranslator,
    TranslatorFactory,
    default_translator_factory,
)

log = logging.getLogger("system_one_dwarf.app.state")

#: Control mode in which the loop's held actions become approvals.
CHECKPOINT_MODE = "checkpoint"


@dataclass
class AppServices:
    """Manager + bus + live runtimes + loops + translator (the app's backend state)."""

    manager: CampaignManager
    bus: EventBus = field(default_factory=EventBus)
    #: Explicit translator for **every** campaign (tests/embedders); ``None``
    #: means "per campaign, from the campaign's own key".
    translator: Translator | None = None
    #: Factory used by :class:`CampaignTranslator` (defaults to
    #: ``system_one_dwarf.translate.build_translator``).
    translator_factory: TranslatorFactory | None = None
    #: Builder of the per-campaign loop; ``None`` means the real
    #: :func:`~system_one_dwarf.app.loop_runner.build_loop`.
    loop_builder: Callable[..., Any] | None = None
    loop_stop_timeout: float = DEFAULT_STOP_TIMEOUT
    runtimes: dict[str, CampaignRuntime] = field(default_factory=dict)
    loops: dict[str, LoopRunner] = field(default_factory=dict)
    _translators: dict[str, CampaignTranslator] = field(default_factory=dict)

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
        runner = self.loops.pop(campaign_id, None)
        if runner is not None:
            runner.shutdown()
        self._translators.pop(campaign_id, None)

    # ----------------------------------------------------------------- sub-stores

    def keys(self, campaign: Campaign) -> KeyStore:
        return self.manager.keys(campaign)

    def spend(self, campaign: Campaign) -> SpendLedger:
        return self.manager.spend(campaign)

    def translator_for(self, campaign: Campaign) -> Translator:
        """The translator of ``campaign`` (real with a key, identity without)."""
        if self.translator is not None:
            return self.translator
        translator = self._translators.get(campaign.id)
        if translator is None:
            translator = CampaignTranslator(
                self.keys(campaign),
                provider=TRANSLATOR_PROVIDER,
                factory=self.translator_factory or default_translator_factory,
            )
            self._translators[campaign.id] = translator
        return translator

    def memory(self, campaign: Campaign) -> MemoryStore:
        return self.manager.memory(campaign, self.translator_for(campaign))

    def objective(self, campaign: Campaign) -> ObjectiveStore:
        return self.manager.objective(campaign, self.translator_for(campaign))

    # ---------------------------------------------------------------- loop wiring

    def control_of(self, campaign: Campaign) -> dict[str, Any]:
        """Live control (``meta.json`` is the source of truth, not the snapshot)."""
        fresh = self.manager.get_by_slug(campaign.slug) or campaign
        return fresh.control

    def loop_runner(self, campaign: Campaign) -> LoopRunner:
        """The (single) loop runner of a campaign, created on first use."""
        runner = self.loops.get(campaign.id)
        if runner is None:
            builder = self.loop_builder
            if builder is None:
                def builder() -> Any:  # type: ignore[misc]
                    return build_loop(self, campaign)
            runner = LoopRunner(
                campaign.id,
                builder=builder,
                control=lambda: self.control_of(campaign),
                stop_timeout=self.loop_stop_timeout,
            )
            self.loops[campaign.id] = runner
        return runner

    def sync_loop(self, campaign: Campaign, control: Any = None) -> dict[str, Any]:
        """Start/stop the campaign loop according to the (live) control mode."""
        try:
            return self.loop_runner(campaign).sync(control)
        except Exception:  # pragma: no cover - control must never 500
            log.warning("could not sync the loop of %s", campaign.id, exc_info=True)
            return self.loop_status(campaign.id)

    def loop_status(self, campaign_id: str) -> dict[str, Any]:
        runner = self.loops.get(campaign_id)
        if runner is None:
            campaign = self.manager.get(campaign_id)
            return {
                "campaign_id": campaign_id,
                "running": False,
                "reason": "stopped",
                "detail": "the loop was never started",
                "mode": campaign.mode if campaign is not None else None,
                "kill_switch": bool(campaign.kill_switch) if campaign is not None else False,
                "starts": 0,
                "stops": 0,
                "started_at": None,
                "thread": None,
                "last_error": None,
                "last_report": None,
            }
        return runner.status()

    def sync_all_loops(self) -> list[dict[str, Any]]:
        """Startup sweep over every known campaign (no keys ⇒ nothing starts)."""
        statuses = []
        for campaign in self.manager.list_campaigns():
            statuses.append(self.sync_loop(campaign))
        return statuses

    def shutdown_loops(self) -> None:
        """Stop every loop (app shutdown) — never raises."""
        for runner in list(self.loops.values()):
            try:
                runner.shutdown()
            except Exception:  # pragma: no cover - shutdown is best effort
                log.warning("could not stop the loop of %s", runner.campaign_id, exc_info=True)

    # -------------------------------------------------------------------- events

    def apply_event(
        self, campaign_id: str, event_type: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]:
        """Mirror a live event into the REST-visible runtime store.

        Returns the payload that should be fanned out (normalised for
        ``question_new``) plus the additive follow-up events it produced (the
        ``approval_new`` of a checkpoint approval question).
        """
        runtime = self.runtime(campaign_id)
        data = dict(payload or {})
        follow: list[tuple[str, dict[str, Any]]] = []
        try:
            if event_type == "metrics_update":
                runtime.set_metrics(data)
            elif event_type == "screen_translation":
                runtime.set_screen(str(data.get("translated_pt") or ""), ts=data.get("ts"))
            elif event_type == "decision":
                runtime.add_decision(data)
            elif event_type == "approval_released":
                # A libertação é decidida depois de o evento ``decision`` ter sido
                # publicado (passo 6 vs 7 do loop): o espelho marca aqui o registo
                # do JEV-A com ``released_by_approval`` e com o ``status``
                # aditivo ``executed`` — é a execução que o humano libertou.
                runtime.mark_decision_released(data)
            elif event_type == "action_executed":
                # Evento aditivo do loop (a ação despachou mesmo, com ou sem
                # aprovação): o registo do JEV-A ganha ``status: "executed"``.
                runtime.mark_decision_executed(data)
            elif event_type == "question_new":
                # A ação não correu: virou pergunta ao humano.  O espelho é pela
                # ``action_id`` (o registo do JEV-A que escolheu essa ação).
                runtime.mark_decision_question(data)
                data = runtime.add_question(data)
                approval = self._mirror_approval(campaign_id, runtime, data)
                if approval is not None:
                    follow.append(("approval_new", dict(approval)))
            elif event_type == "error":
                # Passo falhou antes de executar: a decisão de ação mais recente
                # ainda sem estado morreu com ele.
                runtime.mark_decision_error(data)
        except Exception:  # pragma: no cover - never let a bad event kill the loop
            log.warning("could not mirror %s into the runtime store", event_type, exc_info=True)
        return data, follow

    def _mirror_approval(
        self, campaign_id: str, runtime: CampaignRuntime, question: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Mirror a checkpoint approval question into the approvals queue.

        Only in ``checkpoint`` mode: in autopilot the loop does not hold actions
        for permission, so the approvals list stays empty by construction.

        The returned entry is ``None`` when nothing *new* appeared: the queue is
        deduped by ``action_id`` (a re-ask bumps the pending entry), so only a
        freshly created approval is announced as ``approval_new``.
        """
        if self.campaign_mode(campaign_id) != CHECKPOINT_MODE:
            return None
        known = set(runtime.approvals)
        approval = runtime.add_approval(question)
        if approval is None or str(approval.get("id") or "") in known:
            return None
        return approval

    def campaign_mode(self, campaign_id: str) -> str | None:
        try:
            campaign = self.manager.get(campaign_id)
        except Exception:  # pragma: no cover - defensive
            log.warning("could not read the mode of %s", campaign_id, exc_info=True)
            return None
        return campaign.mode if campaign is not None else None

    async def publish_event(
        self, campaign_id: str, event_type: str, payload: dict[str, Any] | None = None
    ) -> int:
        """Update the runtime store and fan the envelope out to WS clients."""
        data, follow = self.apply_event(campaign_id, event_type, dict(payload or {}))
        delivered = await self.bus.publish(campaign_id, event_type, data)
        for extra_type, extra_payload in follow:
            await self.bus.publish(campaign_id, extra_type, extra_payload)
        return delivered

    def publish_event_threadsafe(
        self, campaign_id: str, event_type: str, payload: dict[str, Any] | None = None
    ) -> None:
        """Same as :meth:`publish_event` for worker threads / sync callbacks."""
        data, follow = self.apply_event(campaign_id, event_type, dict(payload or {}))
        self.bus.publish_threadsafe(campaign_id, event_type, data)
        for extra_type, extra_payload in follow:
            self.bus.publish_threadsafe(campaign_id, extra_type, extra_payload)


__all__ = [
    "APPROVAL_PENDING",
    "CHECKPOINT_MODE",
    "SOURCE_LIVE",
    "SOURCE_STUB",
    "AppServices",
    "IdentityTranslator",
    "normalize_question",
]