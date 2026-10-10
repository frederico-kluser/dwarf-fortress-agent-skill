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
* :meth:`adventure_driver` / :meth:`adventure_tasks` — the adventure flows
  (``/api/campaigns/{id}/adventures``): one async task per campaign driving the
  game menus through the ``game/driver.py`` piece (seams: the driver factory and
  the task runner).
* :meth:`system_status` / :meth:`game_action` — ``/api/system``: the game
  process (start/stop at the configured path) plus frontend/loop health.
* :meth:`runtime` — the live per-campaign store.  When the campaign has a root on
  disk it is backed by ``<root>/runtime.sqlite`` (:mod:`.persist`, H3): the audit
  trail, the pending approvals and the id counters survive a backend restart.
  ``persist_runtime=False`` / ``runtime_store_factory`` are the test seams.
"""

from __future__ import annotations

import logging
import socket
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import __version__
from ..campaign import (
    PROVIDERS,
    Campaign,
    CampaignManager,
    IdentityTranslator,
    KeyStore,
    MemoryStore,
    ObjectiveStore,
    SpendLedger,
    Translator,
)
from ..game.driver import AdventureDriver, GameProcessError, SubprocessBridge
from .adventure_flow import AdventureTasks
from .bus import EventBus
from .loop_runner import DEFAULT_STOP_TIMEOUT, LoopRunner, build_loop
from .persist import RuntimeStore, runtime_db_path
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

#: Porta da UI web (Vite) — a do `scripts/sod.sh` por omissão.
DEFAULT_WEB_PORT = 5173


def probe_tcp_port(port: int, timeout: float = 0.3) -> bool:
    """Sonda TCP best-effort (frontend a correr? porta do Vite aberta?)."""
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


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
    # ---- fluxos de aventura + system control (docs/api.md: /adventures, /api/system)
    #: Caminho configurado do DF (guarda do GameProcessManager: só aqui se gere o jogo).
    df_path: str | None = None
    #: Construtor do driver de aventura por campanha (test seam; omissão: o real).
    adventure_driver_factory: Callable[[Campaign], Any] | None = None
    #: Arranque do worker das tarefas (test seam; omissão: thread daemon).
    adventure_task_runner: Callable[[Callable[[], None]], Any] | None = None
    #: Gestor do processo do jogo (test seam; omissão: ``GameProcessManager``).
    game: Any | None = None
    web_port: int = DEFAULT_WEB_PORT
    #: Sonda do frontend (test seam; omissão: :func:`probe_tcp_port`).
    frontend_probe: Callable[[int], bool] | None = None
    _adventures: AdventureTasks | None = None
    # ---- persistência dos stores rotativos (ARQUITETURA §9 · PROBLEMAS H3)
    #: Guarda decisions/questions/approvals/inputs em ``<campanha>/runtime.sqlite``
    #: (só acrescenta durabilidade); ``False`` = só memória (testes/embedders).
    persist_runtime: bool = True
    #: Construtor do store por campanha — ``(Campaign) -> store | None`` (test
    #: seam; omissão: :class:`~system_one_dwarf.app.persist.RuntimeStore`).
    runtime_store_factory: Callable[[Campaign], Any] | None = None
    _runtime_lock: Any = field(default_factory=threading.Lock, repr=False, compare=False)
    #: One callback per campaign, fired when the human answers a question — it is
    #: how the session watchdog (``agent.watchdog``) learns the human is present.
    _answer_listeners: dict[str, Callable[[], Any]] = field(
        default_factory=dict, repr=False, compare=False
    )

    # ------------------------------------------------------------------ campaigns

    def resolve(self, campaign_id: str) -> Campaign:
        """Campaign by opaque id (or slug); raises :class:`CampaignNotFound`."""
        return self.manager.require(campaign_id)

    def runtime(self, campaign_id: str) -> CampaignRuntime:
        """The live store of a campaign (built once; reloaded from its SQLite)."""
        runtime = self.runtimes.get(campaign_id)
        if runtime is None:
            # Lock: a loop thread and a request thread may both ask for the first
            # runtime of a campaign; two instances over one file would lose edits.
            with self._runtime_lock:
                runtime = self.runtimes.get(campaign_id)
                if runtime is None:
                    runtime = CampaignRuntime(
                        campaign_id=campaign_id, store=self._runtime_store_for(campaign_id)
                    )
                    self.runtimes[campaign_id] = runtime
        return runtime

    def _runtime_store_for(self, campaign_id: str) -> Any:
        """The persistent store of ``campaign_id`` (``None`` = memory-only).

        Only a campaign that exists with a root directory on disk gets one — an
        unknown id (tests, stray events) stays in memory instead of conjuring a
        directory.  Never raises: the runtime must come up without its store.
        """
        if not self.persist_runtime:
            return None
        try:
            campaign = self.manager.get(campaign_id)
            if campaign is None or not Path(campaign.root).is_dir():
                return None
            if self.runtime_store_factory is not None:
                return self.runtime_store_factory(campaign)
            return RuntimeStore(
                runtime_db_path(campaign.root), secrets=self._live_secrets(campaign)
            )
        except Exception:
            log.warning("no persistent runtime store for %s", campaign_id, exc_info=True)
            return None

    def _live_secrets(self, campaign: Campaign) -> Callable[[], list[str]]:
        """The campaign's live API keys, so the store can mask them literally."""
        keys = self.keys(campaign)

        def read() -> list[str]:
            found: list[str] = []
            for provider in PROVIDERS:
                try:
                    key = keys.get_key(provider)
                except Exception:  # a broken key store must not break persistence
                    continue
                if isinstance(key, str) and key.strip():
                    found.append(key.strip())
            return found

        return read

    def set_answer_listener(
        self, campaign_id: str, callback: Callable[[], Any] | None
    ) -> None:
        """Register (replacing the previous one; ``None`` clears) the callback that
        runs on every ``question_answered`` of ``campaign_id``.  It must be cheap
        and must not raise (a failure is logged and swallowed by ``apply_event``)."""
        if callback is None:
            self._answer_listeners.pop(campaign_id, None)
        else:
            self._answer_listeners[campaign_id] = callback

    def pause_campaign(self, campaign_id: str, *, reason: str = "") -> dict[str, Any] | None:
        """Put the campaign in ``paused`` — what ``pause_and_ask`` / a budget stop ask for.

        Exactly ``POST .../control {mode: "paused"}``: persisted in ``meta.json``,
        the loop is stopped and ``control_state`` is fanned out.  Safe to call from
        the loop's own thread (``LoopRunner.stop`` never joins the current thread).
        Never raises; ``None`` when the campaign is unknown or the write failed.
        """
        try:
            campaign = self.manager.get(campaign_id)
            if campaign is None:
                return None
            payload = self.manager.set_control(campaign, mode="paused")
            control = {"mode": payload["mode"], "kill_switch": payload["kill_switch"]}
            log.info("campaign %s paused%s", campaign.id, f" ({reason})" if reason else "")
            self.sync_loop(campaign, control)
            self.publish_event_threadsafe(campaign.id, "control_state", control)
            return payload
        except Exception:
            log.warning("could not pause campaign %s", campaign_id, exc_info=True)
            return None

    def drop_runtime(self, campaign_id: str) -> None:
        runtime = self.runtimes.pop(campaign_id, None)
        if runtime is not None:
            runtime.close()
        runner = self.loops.pop(campaign_id, None)
        if runner is not None:
            runner.shutdown()
        self._translators.pop(campaign_id, None)

    def close_runtimes(self) -> None:
        """Flush every runtime and release its store connection — never raises.

        The runtimes stay usable (a store reopens on demand): this is the app
        shutdown courtesy that checkpoints the SQLite WAL, not a terminal state.
        """
        for runtime in list(self.runtimes.values()):
            try:
                runtime.close()
            except Exception:  # pragma: no cover - shutdown is best effort
                log.warning("could not close the runtime of %s", runtime.campaign_id,
                            exc_info=True)

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
        self.close_runtimes()

    # ---------------------------------------------------- adventures + system

    def adventure_driver(self, campaign: Campaign) -> Any:
        """O driver de aventura da campanha (real; ``adventure_driver_factory``)."""
        if self.adventure_driver_factory is not None:
            return self.adventure_driver_factory(campaign)
        return AdventureDriver(
            SubprocessBridge(df_path=self.df_path),
            translator=self.translator_for(campaign),
        )

    def adventure_tasks(self) -> AdventureTasks:
        """O store das tarefas de aventura (uma por campanha de cada vez)."""
        if self._adventures is None:
            self._adventures = AdventureTasks(
                driver_factory=lambda campaign_id: self.adventure_driver(
                    self.manager.require(campaign_id)
                ),
                publish=self.publish_event_threadsafe,
                translator_for=lambda campaign_id: self.translator_for(
                    self.manager.require(campaign_id)
                ),
                runner=self.adventure_task_runner,
            )
        return self._adventures

    def system_status(self) -> dict[str, Any]:
        """O payload de ``GET /api/system/status`` (jogo/backend/frontend/loop)."""
        if self.game is not None:
            game = self.game.status()
        else:  # sem gestor: o jogo está simplesmente "em baixo"
            game = {"running": False, "pid": None, "rpc": False, "path": self.df_path or ""}
        probe = self.frontend_probe or probe_tcp_port
        campaigns = []
        for campaign in self.manager.list_campaigns():
            loop = self.loop_status(campaign.id)
            campaigns.append({
                "id": campaign.id,
                "mode": loop.get("mode") or campaign.mode,
                "running": bool(loop.get("running")),
            })
        return {
            "game": {
                "running": bool(game.get("running")),
                "pid": game.get("pid"),
                "rpc": bool(game.get("rpc")),
                "path": str(game.get("path") or self.df_path or ""),
            },
            "backend": {"version": __version__},
            "frontend": {"running": bool(probe(self.web_port)), "port": self.web_port},
            "loop": {"campaigns": campaigns},
        }

    def game_action(self, action: str) -> dict[str, Any]:
        """``POST /api/system/game`` — start/stop do processo do jogo.

        A guarda é do gestor: só gere o jogo no caminho configurado
        (:class:`~system_one_dwarf.game.driver.GameProcessManager`).
        """
        if self.game is None:
            raise GameProcessError("gestor do processo do jogo nao configurado")
        if action == "start":
            return self.game.start()
        if action == "stop":
            return self.game.stop()
        raise GameProcessError(f"acao invalida: {action!r} (esperava 'start' ou 'stop')")

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
            elif event_type == "question_answered":
                # O humano está presente: o watchdog da sessão conta isto como
                # progresso (ver ``set_answer_listener``).
                listener = self._answer_listeners.get(campaign_id)
                if listener is not None:
                    listener()
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
    "DEFAULT_WEB_PORT",
    "SOURCE_LIVE",
    "SOURCE_STUB",
    "AppServices",
    "IdentityTranslator",
    "normalize_question",
    "probe_tcp_port",
]