"""Answering a human question — one flow shared by REST and WebSocket.

``POST /api/campaigns/{id}/questions/{qid}/answer`` and the WS message
``{"type": "answer_question", ...}`` must behave identically:

1. the question is marked ``answered`` in the live store;
2. the answer is translated PT -> EN and persisted **in the campaign memory**
   (contract: "A resposta vai para a memória");
3. clients are told: a contract-typed ``memory_update`` event, plus the additive
   ``question_answered`` event carrying the same body as the REST response.

A question that is a *checkpoint approval* also decides the mirrored approval
entry (queue → approvals is a mirror, never a second source of truth), which is
echoed as the additive ``approval_updated`` event.
"""

from __future__ import annotations

import logging
from typing import Any

from starlette.concurrency import run_in_threadpool

from ..campaign import Campaign, CoalaError
from ..campaign.memory import TAG_HUMAN_ANSWER, safe_translate
from .state import AppServices
from .views import memory_payload

log = logging.getLogger("system_one_dwarf.app.question_flow")


def _persist_answer(
    services: AppServices,
    campaign: Campaign,
    question_id: str,
    answer_en: str | None,
    option_id: str | None,
) -> None:
    detail = answer_en or (f"option {option_id}" if option_id else "(no text)")
    try:
        services.memory(campaign).add_episode(
            f"Human answer to question {question_id}: {detail}",
            key=f"answer:{question_id}",
            tags=TAG_HUMAN_ANSWER,
            origin="owner",
            source="ui:questions",
        )
    except CoalaError as exc:  # the answer is kept in the live store regardless
        log.warning("could not persist answer %s in the campaign memory: %s", question_id, exc)


async def answer_question(
    services: AppServices,
    campaign: Campaign,
    question_id: str,
    *,
    answer_text: str | None = None,
    option_id: str | None = None,
) -> dict[str, Any] | None:
    """Run the full answer flow; ``None`` when the question id is unknown."""
    runtime = services.runtime(campaign.id)
    if runtime.get_question(question_id) is None:
        return None

    answer_en = safe_translate(
        services.translator_for(campaign), (answer_text or "").strip(), "en"
    ) or None
    runtime.answer_question(question_id, answer_text=answer_en, option_id=option_id)
    payload = runtime.question_answer(question_id) or {
        "id": question_id,
        "status": "answered",
        "answer_text": answer_en,
        "option_id": option_id,
    }

    # Imported here: approval_flow imports this module (the decision flows back
    # through the question-answer path, so the two modules are mutually visible).
    from .approval_flow import mirror_answer

    mirrored = mirror_answer(runtime, question_id, option_id)

    await run_in_threadpool(_persist_answer, services, campaign, question_id, answer_en, option_id)
    await services.publish_event(campaign.id, "question_answered", payload)
    try:
        fresh = await run_in_threadpool(memory_payload, services, campaign)
        await services.publish_event(campaign.id, "memory_update", fresh)
    except CoalaError as exc:  # pragma: no cover - memory hiccup after a valid answer
        log.warning("memory_update after answer %s skipped: %s", question_id, exc)
    if mirrored is not None:
        await services.publish_event(campaign.id, "approval_updated", dict(mirrored))
    return payload
