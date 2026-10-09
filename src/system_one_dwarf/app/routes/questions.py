"""``/api/campaigns/{id}/questions`` — the human-in-the-loop queue.

The queue is fed by the agent loop (band ``hitl``/``abstain``) through the bus
event ``question_new``; empty until then.  ``status`` distinguishes ``pending``
from ``answered`` entries, so a client can render recent answers too.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from ...campaign import Campaign
from ..deps import get_campaign, get_runtime, get_services, tag_source
from ..question_flow import answer_question
from ..runtime import CampaignRuntime
from ..schemas import AnswerOut, AnswerRequest, QuestionOut
from ..state import AppServices

router = APIRouter(tags=["questions"])


@router.get("/api/campaigns/{campaign_id}/questions", response_model=list[QuestionOut])
def list_questions(
    response: Response,
    campaign: Campaign = Depends(get_campaign),
    runtime: CampaignRuntime = Depends(get_runtime),
) -> list[dict]:
    tag_source(response, runtime.source_of("questions"))
    return runtime.list_questions()


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
