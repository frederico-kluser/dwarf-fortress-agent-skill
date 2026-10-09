"""In-memory pub/sub bus for the per-campaign WebSocket channel.

Other pieces (agent loop, JEV fleet, narrator) publish here; ``ws.py`` subscribes
one queue per connected client.  Messages are always the contract envelope
``{"type": ..., "ts": ..., "payload": ...}``.

Usage from async code::

    await services.bus.publish(campaign_id, "decision", decision_dict)

Usage from a worker thread (or a sync callback)::

    services.bus.publish_threadsafe(campaign_id, "metrics_update", metrics)

Nothing is persisted here: the bus is a fan-out of *live* events.  Durable state
lives in the campaign layer (memory/objective/meta) and in ``runtime.py``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..campaign.clock import utc_now_iso

log = logging.getLogger("system_one_dwarf.app.bus")

#: Contract event types (server -> client) from docs/api.md.
EVENT_TYPES: tuple[str, ...] = (
    "metrics_update",
    "screen_translation",
    "decision",
    "question_new",
    "memory_update",
    "spend_update",
    "game_state",
    "control_state",
    # additive, documented extensions (clients ignore unknown types per contract):
    "question_answered",
    "approval_new",
    "approval_updated",
    "approval_released",
    "error",
)

DEFAULT_QUEUE_SIZE = 256


def envelope(event_type: str, payload: dict[str, Any] | None = None, ts: str | None = None) -> dict[str, Any]:
    """Build the contract envelope ``{type, ts, payload}``."""
    return {
        "type": event_type,
        "ts": ts or utc_now_iso(),
        "payload": payload if isinstance(payload, dict) else {},
    }


class EventBus:
    """Fan-out of envelopes to per-connection ``asyncio.Queue`` subscribers."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------ plumbing

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None) -> None:
        """Remember the app's event loop (lets other threads publish safely)."""
        self._loop = loop

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        if self._loop is not None and not self._loop.is_closed():
            return self._loop
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    def subscribe(self, campaign_id: str, maxsize: int = DEFAULT_QUEUE_SIZE) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers.setdefault(campaign_id, set()).add(queue)
        return queue

    def unsubscribe(self, campaign_id: str, queue: asyncio.Queue) -> None:
        subs = self._subscribers.get(campaign_id)
        if not subs:
            return
        subs.discard(queue)
        if not subs:
            self._subscribers.pop(campaign_id, None)

    def subscriber_count(self, campaign_id: str) -> int:
        return len(self._subscribers.get(campaign_id, ()))

    def campaigns(self) -> list[str]:
        return list(self._subscribers)

    # ------------------------------------------------------------------ publishing

    async def publish(
        self,
        campaign_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        ts: str | None = None,
    ) -> int:
        """Fan out one envelope; returns how many clients received it."""
        message = envelope(event_type, payload, ts)
        delivered = 0
        for queue in list(self._subscribers.get(campaign_id, ())):
            try:
                queue.put_nowait(message)
                delivered += 1
            except asyncio.QueueFull:  # slow client: drop the oldest, keep the newest
                try:
                    queue.get_nowait()
                    queue.put_nowait(message)
                    delivered += 1
                except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
                    log.warning("dropping %s for a slow WS subscriber", event_type)
        return delivered

    def publish_threadsafe(
        self,
        campaign_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        ts: str | None = None,
    ) -> None:
        """Publish from a non-async context (worker thread, game loop callback)."""
        loop = self.loop
        message = envelope(event_type, payload, ts)
        if loop is None or not loop.is_running():  # pragma: no cover - no loop bound
            log.debug("no running loop; %s event dropped", event_type)
            return
        loop.call_soon_threadsafe(self._deliver_nowait, campaign_id, message)

    def _deliver_nowait(self, campaign_id: str, message: dict[str, Any]) -> None:  # pragma: no cover
        for queue in list(self._subscribers.get(campaign_id, ())):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                pass
