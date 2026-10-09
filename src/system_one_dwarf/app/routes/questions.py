"""``/api/campaigns/{id}/questions`` — the human-in-the-loop queue.

The queue is fed by the agent loop (band ``hitl``/``abstain``) through the bus
event ``question_new``; empty until then.  ``status`` distinguishes ``pending``
from ``answered`` entries, so a client can render recent answers too.

The additive provenance fields (``band``, ``role``, ``action_id``, ``why_pt``,
``source_data`` — finding #2 of ``docs/uxui-audit-ui.json``) travel only when the
loop published them, so the 6-field entry of the original contract is unchanged
for a client that never learned about them (``response_model_exclude_none``).

The additive ``?limit=&before=`` pair paginates the queue (``docs/api.md``):
``limit`` keeps the most recent entries (oldest-first in the answer) and
``before`` is an exclusive ISO-8601 cursor to walk history backwards.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ...campaign import Campaign
from ..deps import get_campaign, get_runtime, get_services, tag_source
from ..question_flow import answer_question
from ..runtime import CampaignRuntime
from ..schemas import AnswerOut, AnswerRequest, QuestionOut
from ..state import AppServices

router = APIRouter(tags=["questions"])


@router.get(
    "/api/campaigns/{campaign_id}/questions",
    response_model=list[QuestionOut],
    response_model_exclude_none=True,
)
def list_questions(
    response: Response,
    limit: int | None = Query(default=None, ge=1, description="máximo de entradas (as mais recentes)"),
    before: str | None = Query(
        default=None, description="cursor ISO-8601: só entradas estritamente anteriores"
    ),
    campaign: Campaign = Depends(get_campaign),
    runtime: CampaignRuntime = Depends(get_runtime),
) -> list[dict]:
    tag_source(response, runtime.source_of("questions"))
    try:
        return runtime.list_questions(limit=limit, before=before)
    except ValueError:
        raise HTTPException(status_code=422, detail="before must be an ISO-8601 timestamp")


@router.post(
    "/api/campaigns/{campaign_id}/questions/{question_id}/answer",
    response_model=AnswerOut,
)
async def answer(
    question_id: str,
    body: AnswerRequest,
    campaign: Campaign = Depends(get_campaign),
    services: AppServices = Depends(get_services),
) -> dict:
    payload = await answer_question(
        services,
        campaign,
        question_id,
        answer_text=body.answer_text,
        option_id=body.option_id,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="question not found")
    return payload
