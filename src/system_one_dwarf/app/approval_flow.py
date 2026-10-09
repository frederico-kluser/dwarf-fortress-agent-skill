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

Two layers decide an approval:

* :func:`decide_approval` **records** it (approve/reject + note) and mirrors the
  decision onto the underlying question through the **existing** question-answer
  path, so the answer lands in the campaign memory with the audit trail;
* :class:`ApprovalChecker` **releases** it: it is the loop's
  ``approval_checker``, backed by this campaign's approvals store.
  ``was_approved(action_id)`` consumes the oldest ``approved`` approval for that
  exact action (marking ``released_at``/``released_action_id`` so it can never be
  used twice) and answers *yes*, which lets the loop execute the held action as if
  the band were ``auto``.  Pending and rejected approvals are never returned, so a
  refusal (or a missing decision) keeps the loop asking.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from ..campaign import Campaign
from ..campaign.clock import utc_now_iso
from .runtime import (
    APPROVAL_APPROVED,
    APPROVAL_ID_PREFIX,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
)
from .state import AppServices

log = logging.getLogger("system_one_dwarf.app.approval_flow")

APPROVE_TEXT = "Aprovado pelo humano no modo checkpoint."
REJECT_TEXT = "Rejeitado pelo humano no modo checkpoint."
NOTE_SUFFIX = " Nota: {note}"

#: Option ids the loop's deterministic approval question offers.
APPROVE_OPTIONS = ("yes",)
REJECT_OPTIONS = ("no", "reject", "n")

#: Additive fields marking that an approval was **used** to release an execution.
RELEASED_AT = "released_at"
RELEASED_ACTION_ID = "released_action_id"


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
    updated approval, or ``None`` when there is nothing to mirror.  The approval
    of a question is found under the question id (explicit approval questions) or
    under :data:`~.runtime.APPROVAL_ID_PREFIX` + question id (bare JEV-A holds).
    """
    approval = runtime.get_approval(question_id)
    if approval is None:
        approval = runtime.get_approval(f"{APPROVAL_ID_PREFIX}{question_id}")
    if approval is None or approval.get("status") != APPROVAL_PENDING:
        return None
    option = str(option_id or "").strip().lower()
    status = APPROVAL_REJECTED if option in REJECT_OPTIONS else APPROVAL_APPROVED
    return runtime.decide_approval(str(approval.get("id") or question_id), status=status)


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


# ------------------------------------------------------------------- release


def release_approved_action(
    runtime: Any, action_id: str, *, ts: str | None = None
) -> dict[str, Any] | None:
    """Consume the oldest ``approved`` approval matching ``action_id``.

    Only an **approved and still unused** approval releases an execution: the
    entry is marked with :data:`RELEASED_AT`/:data:`RELEASED_ACTION_ID` and is
    never returned again (one approval = one release).  Pending and rejected
    approvals never release; an empty ``action_id`` never matches.
    """
    wanted = str(action_id or "").strip()
    if not wanted:
        return None
    for approval in runtime.list_approvals(status=APPROVAL_APPROVED):
        if str(approval.get("action_id") or "") != wanted:
            continue
        if approval.get(RELEASED_AT):
            continue
        approval[RELEASED_AT] = ts or utc_now_iso()
        approval[RELEASED_ACTION_ID] = wanted
        return approval
    return None


class ApprovalChecker:
    """The loop's ``approval_checker`` over one campaign's approvals store.

    ``was_approved(action_id)`` **consumes** the matching approval and returns
    ``True``; ``on_release`` (when given) publishes the updated record so the UI
    sees the release.  A missing store call simply answers ``False`` — the loop
    then behaves exactly as it did before this seam existed.
    """

    def __init__(
        self,
        runtime: Any,
        *,
        on_release: Callable[[dict[str, Any]], Any] | None = None,
        clock: Callable[[], str] = utc_now_iso,
    ) -> None:
        self.runtime = runtime
        self.on_release = on_release
        self.clock = clock

    def was_approved(self, action_id: str) -> bool:
        approval = release_approved_action(self.runtime, action_id, ts=self.clock())
        if approval is None:
            return False
        if self.on_release is not None:
            try:
                self.on_release(dict(approval))
            except Exception:
                # A UI que não receba o evento não pode travar a execução já
                # aprovada: o consumo está feito e é isso que impede o duplo uso.
                log.warning(
                    "could not publish the release of approval %s", approval.get("id"),
                    exc_info=True,
                )
        return True


__all__ = [
    "APPROVE_OPTIONS",
    "APPROVE_TEXT",
    "REJECT_OPTIONS",
    "REJECT_TEXT",
    "RELEASED_ACTION_ID",
    "RELEASED_AT",
    "ApprovalAlreadyDecided",
    "ApprovalChecker",
    "ApprovalNotFound",
    "decide_approval",
    "mirror_answer",
    "release_approved_action",
]
