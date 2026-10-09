"""LOOP ↔ RUNTIME: one :class:`~system_one_dwarf.agent.loop.AgentLoop` per campaign.

This module is the missing bridge between the verified decision loop and the
FastAPI app.  ``AppServices`` owns one :class:`LoopRunner` per campaign; the
runner

* **builds the real collaborators** (:func:`build_loop`): ``GameStateClient``,
  ``JevClient`` with the campaign's key, ``DeepSeekLLM`` + ``Narrator`` +
  ``QuestionFormatter``, the campaign translator (``translate.build_translator``
  when there is a key, identity otherwise), the campaign ``MemoryStore`` and
  ``SpendLedger``, and an :class:`~system_one_dwarf.agent.loop.EventSink` wired
  to :meth:`AppServices.publish_event_threadsafe` — so ``metrics_update``,
  ``decision``, ``question_new`` (with provenance), ``memory_update``,
  ``spend_update``, ``game_state``, ``screen_translation`` and ``control_state``
  all land in the live stores *and* on the WebSocket channel;
* **starts and stops it from the control mode** — ``autopilot``/``checkpoint``
  run the loop, ``paused``/``kill_switch`` stop it (the runner also stops the
  loop on app shutdown);
* **never runs without keys.**  Both the JEV key (``typesafe``) and the LLM key
  (``openrouter``) are required; without them :func:`build_loop` raises
  :class:`LoopUnavailable`, the loop stays stopped and every endpoint keeps
  answering with its documented stub payload.

Restartability: :meth:`AgentLoop.stop` is terminal by design (the same instance
cannot be resumed), so a start after a stop builds a **fresh** loop — the runner
holds at most one *active* loop and thread per campaign at any time, and
start/stop can be repeated indefinitely.

The builder and the individual factories are injectable
(``create_app(loop_builder=...)``) so the whole wiring is testable offline with
fakes for the game, the JEV, the LLM and the loop.
"""

from __future__ import annotations

import logging
import threading
from functools import partial
from typing import Any, Callable, Mapping

from ..agent.loop import AgentLoop, CallbackEventSink
from ..agent.types import ControlState, LoopConfig, MODE_PAUSED
from ..campaign import Campaign
from ..campaign.clock import utc_now_iso

log = logging.getLogger("system_one_dwarf.app.loop_runner")

#: How long a stop waits for the worker thread before giving up (seconds).
DEFAULT_STOP_TIMEOUT = 5.0

#: Status reasons (stable strings; the app documents them, no UI contract).
REASON_RUNNING = "running"
REASON_PAUSED = "paused"
REASON_KILL_SWITCH = "kill_switch"
REASON_NO_KEYS = "no_api_keys"
REASON_SHUTDOWN = "shutdown"
REASON_ERROR = "error"
REASON_STOPPED = "stopped"


class LoopUnavailable(RuntimeError):
    """The loop cannot run: no keys (or an unusable collaborator).

    ``reason`` is a short stable code, ``missing`` the providers without a key.
    """

    def __init__(self, reason: str, *, missing: tuple[str, ...] = (), detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.missing = tuple(missing)
        self.detail = detail or reason


def missing_key_providers(key_store: Any, config: LoopConfig) -> list[str]:
    """Providers the loop needs a key for, in the order it needs them."""
    needed = [config.jev_provider, config.llm_provider]
    missing: list[str] = []
    for provider in needed:
        if provider in missing:
            continue
        getter = getattr(key_store, "get_key", None)
        if not callable(getter):
            missing.append(provider)
            continue
        try:
            key = getter(provider)
        except Exception:
            key = None
        if not (isinstance(key, str) and key.strip()):
            missing.append(provider)
    return missing


def build_loop(
    services: Any,
    campaign: Campaign,
    *,
    config: LoopConfig | Mapping[str, Any] | None = None,
    game_factory: Callable[..., Any] | None = None,
    jev_factory: Callable[..., Any] | None = None,
    llm_factory: Callable[..., Any] | None = None,
    executor_factory: Callable[..., Any] | None = None,
) -> AgentLoop:
    """Build the production :class:`AgentLoop` for ``campaign``.

    Raises :class:`LoopUnavailable` when the campaign has no key for the JEV
    (``typesafe``) or for the LLM (``openrouter``) — the documented "without keys
    the loop stays stopped" rule.  The factories default to the real pieces and
    exist so tests can swap the game/JEV/LLM without any network or game.
    """
    if game_factory is None:
        from ..game import GameStateClient as game_factory  # type: ignore[assignment]
    if jev_factory is None:
        from ..jev import JevClient as jev_factory  # type: ignore[assignment]
    if llm_factory is None:
        from ..agent import DeepSeekLLM as llm_factory  # type: ignore[assignment]
    from ..agent import Narrator, QuestionFormatter, spend_recorder

    loop_config: LoopConfig = config if isinstance(config, LoopConfig) else LoopConfig()
    if isinstance(config, Mapping):
        from ..agent.config import load_config

        loop_config = load_config(meta=config)

    keys = services.keys(campaign)
    missing = missing_key_providers(keys, loop_config)
    if missing:
        raise LoopUnavailable(
            REASON_NO_KEYS,
            missing=tuple(missing),
            detail=(
                "no API key stored for "
                + ", ".join(missing)
                + " — the decision loop stays stopped and the API serves stubs "
                "(POST /api/campaigns/{id}/keys)"
            ),
        )

    live = ControlState.from_any(
        services.manager.get_by_slug(campaign.slug) or campaign, None
    )
    loop_config = loop_config.with_control(live)

    spend = services.spend(campaign)
    memory = services.memory(campaign)
    get_llm_key = partial(keys.get_key, loop_config.llm_provider)
    llm = llm_factory(
        get_llm_key,
        model=loop_config.narrator_model,
        usage_callback=spend_recorder(spend, provider=loop_config.llm_provider),
        provider=loop_config.llm_provider,
    )
    jev = jev_factory(keys.get_key(loop_config.jev_provider), model=loop_config.jev_model)
    game = game_factory()
    executor = executor_factory(game, loop_config) if callable(executor_factory) else None

    def control_source() -> dict[str, Any]:
        fresh = services.manager.get_by_slug(campaign.slug) or campaign
        return fresh.control

    return AgentLoop(
        jev,
        game,
        memory,
        Narrator(llm, memory),
        QuestionFormatter(
            llm,
            auto_threshold=loop_config.auto_threshold,
            hitl_threshold=loop_config.hitl_threshold,
        ),
        services.translator_for(campaign),
        spend,
        keys,
        CallbackEventSink(partial(services.publish_event_threadsafe, campaign.id)),
        loop_config,
        objective_store=services.objective(campaign),
        control=control_source,
        executor=executor,
    )


class LoopRunner:
    """Owns the single active loop + worker thread of one campaign.

    Args:
        campaign_id: the campaign this runner belongs to (thread name, status).
        builder: ``callable() -> AgentLoop`` — called on **every** start (a
            stopped loop cannot be resumed, see the module docstring).
        control: ``callable() -> ControlState`` for the status report.
        stop_timeout: how long :meth:`stop` waits for the worker thread.
    """

    def __init__(
        self,
        campaign_id: str,
        *,
        builder: Callable[[], Any],
        control: Callable[[], Any] | None = None,
        stop_timeout: float = DEFAULT_STOP_TIMEOUT,
        logger: Any = None,
    ) -> None:
        self.campaign_id = campaign_id
        self._builder = builder
        self._control = control
        self.stop_timeout = float(stop_timeout)
        self.log = logger or log

        self._lock = threading.Lock()
        self._loop: Any = None
        self._thread: threading.Thread | None = None
        self._closed = False
        self._reason = REASON_STOPPED
        self._detail = "not started yet"
        self.starts = 0
        self.stops = 0
        self.last_report: dict[str, Any] | None = None
        self.last_error: str | None = None
        self._started_at: str | None = None

    # ------------------------------------------------------------------ state
    def control_state(self) -> ControlState:
        if self._control is None:
            return ControlState()
        try:
            value = self._control() if callable(self._control) else self._control
        except Exception:
            self.log.warning("control callback failed", exc_info=True)
            return ControlState()
        return ControlState.from_any(value, ControlState())

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def status(self) -> dict[str, Any]:
        """Small, JSON-friendly status report (used by tests and the app logs)."""
        control = self.control_state()
        return {
            "campaign_id": self.campaign_id,
            "running": self.running,
            "reason": REASON_RUNNING if self.running else self._reason,
            "detail": self._detail,
            "mode": control.mode,
            "kill_switch": bool(control.kill_switch),
            "starts": int(self.starts),
            "stops": int(self.stops),
            "started_at": self._started_at,
            "thread": self._thread.name if self._thread is not None else None,
            "last_error": self.last_error,
            "last_report": dict(self.last_report) if self.last_report else None,
        }

    # ------------------------------------------------------------------ start
    def start(self) -> bool:
        """Build and start a loop; ``False`` when it must stay stopped."""
        with self._lock:
            if self._closed:
                self._reason, self._detail = REASON_SHUTDOWN, "the app is shutting down"
                return False
            if self.running:
                return True
            try:
                loop = self._builder()
            except LoopUnavailable as exc:
                self._reason, self._detail = exc.reason, exc.detail
                self.log.info("loop for %s stays stopped: %s", self.campaign_id, exc.detail)
                return False
            except Exception as exc:  # a broken collaborator must not break the API
                self._reason, self._detail = REASON_ERROR, f"could not build the loop: {exc}"
                self.last_error = str(exc)
                self.log.warning("could not build the loop for %s", self.campaign_id,
                                 exc_info=True)
                return False
            if loop is None:
                self._reason, self._detail = REASON_NO_KEYS, "no loop was built"
                return False
            thread = threading.Thread(
                target=self._run,
                args=(loop,),
                name=f"sod-loop-{self.campaign_id}",
                daemon=True,
            )
            self._loop, self._thread = loop, thread
            self._reason, self._detail = REASON_RUNNING, "the decision loop is running"
            self._started_at = utc_now_iso()
            self.last_error = None
            self.starts += 1
            thread.start()
            return True

    def _run(self, loop: Any) -> None:
        try:
            report = loop.run()
            self.last_report = _report_dict(report)
        except Exception as exc:  # pragma: no cover - run() protects each step
            self.last_error = str(exc)
            self.log.warning("loop for %s failed", self.campaign_id, exc_info=True)
            with self._lock:
                if self._loop is loop:
                    self._reason, self._detail = REASON_ERROR, f"loop failed: {exc}"
        finally:
            with self._lock:
                if self._loop is loop:  # a stop already detached this loop
                    self._reason = self._reason if self._reason == REASON_ERROR else REASON_STOPPED
                    self._detail = "the loop ended"
                    self._loop = None
                    self._thread = None

    # ------------------------------------------------------------------- stop
    def stop(self, *, reason: str = REASON_STOPPED, detail: str = "") -> bool:
        """Ask the loop to stop and wait (bounded) for its thread; ``False`` if idle."""
        with self._lock:
            loop, thread = self._loop, self._thread
            if loop is None and thread is None:
                self._reason = reason
                self._detail = detail or "already stopped"
                return False
            self._loop, self._thread = None, None
            self._reason = reason
            self._detail = detail or "stopped by the control mode"
            self.stops += 1
        stopper = getattr(loop, "stop", None)
        if callable(stopper):
            try:
                stopper()
            except Exception:
                self.log.warning("loop.stop() failed", exc_info=True)
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=self.stop_timeout)
        closer = getattr(loop, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:  # pragma: no cover - closing pools is best effort
                self.log.warning("loop.close() failed", exc_info=True)
        return True

    # ------------------------------------------------------------------- sync
    def sync(self, control: Any = None) -> dict[str, Any]:
        """Apply the control mode: run on autopilot/checkpoint, stop otherwise."""
        state = ControlState.from_any(control, self.control_state())
        if state.kill_switch:
            self.stop(reason=REASON_KILL_SWITCH, detail="the kill-switch is on")
        elif state.mode == MODE_PAUSED:
            self.stop(reason=REASON_PAUSED, detail="the campaign is paused")
        else:
            started = self.start()
            if started:
                with self._lock:
                    self._reason, self._detail = REASON_RUNNING, (
                        "the decision loop is running (mode %s)" % state.mode
                    )
        return self.status()

    def shutdown(self) -> None:
        """App shutdown: stop now and refuse to start again."""
        self.stop(reason=REASON_SHUTDOWN, detail="the app is shutting down")
        self._closed = True


def _report_dict(report: Any) -> dict[str, Any] | None:
    if report is None:
        return None
    as_dict = getattr(report, "as_dict", None)
    if callable(as_dict):
        try:
            data = as_dict()
        except Exception:  # pragma: no cover - defensive
            return None
        return dict(data) if isinstance(data, Mapping) else None
    return dict(report) if isinstance(report, Mapping) else None


__all__ = [
    "DEFAULT_STOP_TIMEOUT",
    "LoopRunner",
    "LoopUnavailable",
    "REASON_ERROR",
    "REASON_KILL_SWITCH",
    "REASON_NO_KEYS",
    "REASON_PAUSED",
    "REASON_RUNNING",
    "REASON_SHUTDOWN",
    "REASON_STOPPED",
    "build_loop",
    "missing_key_providers",
]