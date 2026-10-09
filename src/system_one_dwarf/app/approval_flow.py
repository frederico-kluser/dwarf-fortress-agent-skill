"""Deciding a checkpoint approval — one flow shared by the REST routes (and WS).

``uxui-audit-ui.json`` finding #4: the ``checkpoint`` mode promises human
approval of every action, but the loop's flow is *question based* — in checkpoint
mode :meth:`AgentLoop._raise_question` publishes an approval question
(``source_data.answer_mode == "approval"``) and holds the action.  The app mirrors
those questions in the approvals queue (:mod:`~system_one_dwarf.app.runtime`) and
this module turns an approve/reject into the **existing** question-answer path:

1. the approval is marked ``approved``/``rejected`` (with the human note);
2. the underlying question is answered (``option_id`` ``yes``/``no`` when the
   loop offered those options, plus a PT text the existing flow translates into
   EN and persists in the campaign memory — the same record a queue answer
   produces, which is what the loop re-reads on its next retrieval);
3. clients are told: ``question_answered`` + ``memory_update`` (from the answer
   flow) and the additive ``approval_updated`` event.

Honest limit, documented in ``docs/api.md``: the app **records and releases** the
decision, but the loop has no callback to re-dispatch a held action — its next
step re-decides with the answer already in memory.  Wiring a true "release this
action" seam is a change in ``agent/loop.py`` (out of scope here; reported).
"""

from __future__ import annotations

import logging
from typing import Any

from ..campaign import Campaign
from .runtime import APPROVAL_APPROVED, APPROVAL_PENDING, APPROVAL_REJECTED
from .state import AppServices

log = logging.getLogger("system_one_dwarf.app.approval_flow")

APPROVE_TEXT = "Aprovado pelo humano no modo checkpoint."
REJECT_TEXT = "Rejeitado pelo humano no modo checkpoint."
NOTE_SUFFIX = " Nota: {note}"

#: Option ids the loop's deterministic approval question offers.
APPROVE_OPTIONS = ("yes",)
REJECT_OPTIONS = ("no", "reject", "n")


class ApprovalNotFound(LookupError):
    """No approval with that id in the campaign's queue."""


class ApprovalAlreadyDecided(RuntimeError):
    """The approval was already approved/rejected."""

    def __init__(self, status: str) -> None:
        super().__init__(f"approval already {status}")
        self.status = status


def _question_options(runtime: Any, question_id: str) -> set[str]:
    question = runtime.get_question(question_id)
    if not question:
        return set()
    return {
        str(option.get("id"))
        for option in (question.get("options") or [])
        if isinstance(option, dict) and option.get("id")
    }


def _answer_text(approve: bool, note: str | None) -> str:
    text = APPROVE_TEXT if approve else REJECT_TEXT
    note = (note or "").strip()
    return text + (NOTE_SUFFIX.format(note=note) if note else "")


def mirror_answer(runtime: Any, question_id: str, option_id: str | None) -> dict[str, Any] | None:
    """Mirror a queue answer onto a pending approval (the "mirrors it" rule).

    Answering the question directly (REST ``.../questions/{qid}/answer`` or the WS
    ``answer_question`` message) decides the approval too: an explicit refusal
    (``no``/``reject``) rejects it, anything else approves it.  Returns the
    updated approval, or ``None`` when there is nothing to mirror.
    """
    approval = runtime.get_approval(question_id)
    if approval is None or approval.get("status") != APPROVAL_PENDING:
        return None
    option = str(option_id or "").strip().lower()
    status = APPROVAL_REJECTED if option in REJECT_OPTIONS else APPROVAL_APPROVED
    return runtime.decide_approval(question_id, status=status)


async def decide_approval(
    services: AppServices,
    campaign: Campaign,
    approval_id: str,
    *,
    approve: bool,
    note: str | None = None,
) -> dict[str, Any]:
    """Approve or reject an approval; returns the updated approval record.

    Raises :class:`ApprovalNotFound` (404) and :class:`ApprovalAlreadyDecided`
    (409) for the two client errors.
    """
    runtime = services.runtime(campaign.id)
    approval = runtime.get_approval(approval_id)
    if approval is None:
        raise ApprovalNotFound(approval_id)
    if approval.get("status") != APPROVAL_PENDING:
        raise ApprovalAlreadyDecided(str(approval.get("status")))

    status = APPROVAL_APPROVED if approve else APPROVAL_REJECTED
    runtime.decide_approval(approval_id, status=status, note=(note or None))

    question_id = str(approval.get("question_id") or approval_id)
    options = _question_options(runtime, question_id)
    wanted = APPROVE_OPTIONS if approve else REJECT_OPTIONS
    option_id = next((candidate for candidate in wanted if candidate in options), None)
    answer_text = _answer_text(approve, note)

    # Imported here: question_flow imports this module's ``mirror_answer``.
    from .question_flow import answer_question

    answered = await answer_question(
        services, campaign, question_id, answer_text=answer_text, option_id=option_id
    )
    if answered is None:  # the question vanished: the approval decision still stands
        log.warning("approval %s decided but its question %s is gone", approval_id, question_id)

    payload = runtime.get_approval(approval_id) or dict(approval)
    await services.publish_event(campaign.id, "approval_updated", dict(payload))
    return dict(payload)


__all__ = [
    "APPROVE_OPTIONS",
    "APPROVE_TEXT",
    "REJECT_OPTIONS",
    "REJECT_TEXT",
    "ApprovalAlreadyDecided",
    "ApprovalNotFound",
    "decide_approval",
    "mirror_answer",
]