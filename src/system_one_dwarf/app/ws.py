"""``WS /ws/campaigns/{id}`` — per-campaign realtime channel.

Server -> client: the contract envelope ``{"type", "ts", "payload"}`` for every
event type in ``docs/api.md`` (``metrics_update``, ``screen_translation``,
``decision``, ``question_new``, ``memory_update``, ``spend_update``,
``game_state``, ``control_state``).  Four additive types are also emitted and are
safe by construction because the contract requires clients to ignore unknown
types: ``question_answered`` (same body as the REST answer response),
``approval_new``/``approval_updated`` (the checkpoint approval queue of
``GET .../approvals``) and ``error`` (``{"detail": "..."}``).

Client -> server: ``answer_question`` and ``set_control`` (exact equivalents of
the REST calls).  Anything else is ignored — never fatal.

All outbound traffic goes through a single queue + pump task, so concurrent
publishers can never interleave frames on one socket.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from ..campaign import Campaign, CampaignError, CampaignNotFound
from .bus import envelope
from .question_flow import answer_question
from .state import AppServices

log = logging.getLogger("system_one_dwarf.app.ws")

router = APIRouter()

#: Close code used when the campaign does not exist (application-defined range).
WS_CAMPAIGN_NOT_FOUND = 4404


async def _pump(websocket: WebSocket, queue: asyncio.Queue) -> None:
    """Single writer: forward every queued envelope to the socket, in order."""
    try:
        while True:
            message = await queue.get()
            await websocket.send_json(message)
    except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
        return


def _queue_send(queue: asyncio.Queue, event_type: str, payload: dict[str, Any] | None = None) -> None:
    try:
        queue.put_nowait(envelope(event_type, payload))
    except asyncio.QueueFull:  # pragma: no cover - client is hopelessly behind
        log.warning("outbound WS queue full; dropping %s", event_type)


async def _handle_message(
    services: AppServices,
    campaign: Campaign,
    raw: str,
    queue: asyncio.Queue,
) -> None:
    try:
        message = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        _queue_send(queue, "error", {"detail": "invalid JSON message"})
        return
    if not isinstance(message, dict):
        _queue_send(queue, "error", {"detail": "message must be a JSON object"})
        return

    kind = message.get("type")

    if kind == "answer_question":
        question_id = str(message.get("question_id") or "")
        if not question_id:
            _queue_send(queue, "error", {"detail": "question_id is required"})
            return
        payload = await answer_question(
            services,
            campaign,
            question_id,
            answer_text=message.get("answer_text"),
            option_id=message.get("option_id"),
        )
        if payload is None:
            _queue_send(queue, "error", {"detail": "question not found"})
        return

    if kind == "set_control":
        try:
            payload = await run_in_threadpool(
                services.manager.set_control,
                campaign,
                mode=message.get("mode"),
                kill_switch=message.get("kill_switch"),
            )
        except (CampaignError, ValueError) as exc:
            _queue_send(queue, "error", {"detail": str(exc)})
            return
        # The mode also drives the decision loop (start on autopilot/checkpoint,
        # stop on paused/kill-switch) — in a threadpool, since stopping waits for
        # the loop's worker thread.
        await run_in_threadpool(
            services.sync_loop,
            campaign,
            {"mode": payload["mode"], "kill_switch": payload["kill_switch"]},
        )
        await services.publish_event(
            campaign.id,
            "control_state",
            {"mode": payload["mode"], "kill_switch": payload["kill_switch"]},
        )
        return

    # Unknown client events are ignored on purpose (non-fatal by contract).
    log.debug("ignoring unknown WS message type %r", kind)


@router.websocket("/ws/campaigns/{campaign_id}")
async def campaign_socket(websocket: WebSocket, campaign_id: str) -> None:
    services: AppServices = websocket.app.state.services
    try:
        campaign = services.resolve(campaign_id)
    except CampaignNotFound:
        await websocket.accept()
        await websocket.close(code=WS_CAMPAIGN_NOT_FOUND, reason="campaign not found")
        return

    await websocket.accept()
    queue = services.bus.subscribe(campaign.id)
    pump = asyncio.create_task(_pump(websocket, queue))
    try:
        # Initial snapshot so a client can render the control panel immediately.
        _queue_send(queue, "control_state", campaign.control)
        while True:
            raw = await websocket.receive_text()
            await _handle_message(services, campaign, raw, queue)
    except WebSocketDisconnect:
        pass
    except Exception:  # pragma: no cover - defensive, never crash the server
        log.exception("WS error on campaign %s", campaign_id)
    finally:
        services.bus.unsubscribe(campaign.id, queue)
        pump.cancel()
