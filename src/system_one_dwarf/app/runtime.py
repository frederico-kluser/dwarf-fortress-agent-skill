"""Live per-campaign state consumed by the REST reads (and fed by the bus).

The agent loop is a *separate* piece.  Until it runs, the endpoints that
depend on it must still answer with **contract-correct** payloads: empty/zeroed
data plus a ``"source": "stub"`` marker (object payloads) and an ``X-Source:
stub`` response header (list payloads, which cannot carry a marker without
breaking the array shape).

When the loop runs, it calls :meth:`AppServices.publish_event` — the runtime
store is updated first, then the event fans out on the WebSocket channel — and
the very same endpoints start answering with live data (``"source": "live"``).

Two live surfaces are owned here:

* the **question queue** — every ``question_new`` payload is normalised so the
  provenance the loop publishes (``band``, ``action_id``, ``why_pt``,
  ``source_data``) survives the API response model, plus the ``role`` the loop
  does not publish (imputed from the held action: ``JEV-A`` when there is one,
  otherwise ``JEV-D``).  Fields the event does not carry are **omitted**, so a
  client written against the old 6-field queue entry keeps working;
* the **approvals queue** (``uxui-audit-ui.json`` finding #4) — in the
  ``checkpoint`` mode the loop raises an *approval* question for every held
  action; the app mirrors those questions here as ``pending`` approvals, each
  with an approve/reject decision.  In autopilot nothing is mirrored, so
  ``GET .../approvals`` is empty by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..campaign.clock import parse_iso, utc_now_iso

SOURCE_LIVE = "live"
SOURCE_STUB = "stub"

#: How many decisions we keep in memory per campaign (ring buffer).
DECISIONS_LIMIT = 5000

#: How many questions we keep in memory per campaign (ring buffer).  Overflow
#: evicts **answered** entries first — the pending asks are the live queue.
QUESTIONS_LIMIT = 2000

#: How many inputs we keep in memory per campaign (ring buffer).
INPUTS_LIMIT = 500

#: Back-compat alias of the old single ring size (inputs keep it).
HISTORY_LIMIT = INPUTS_LIMIT

#: Cap of the *pending* approvals queue (the oldest are dropped past it).  The
#: checkpoint loop re-asks the same held action every step: without both the
#: action dedupe (see :meth:`CampaignRuntime.add_approval`) and this cap the
#: queue grew to hundreds of identical pending entries.
APPROVAL_PENDING_LIMIT = 20

#: Additive repeat-tracking fields: a re-ask of the same ``action_id`` bumps
#: them on the pending entry instead of piling up a new one.
ATTEMPTS = "attempts"
LAST_SEEN_TS = "last_seen_ts"

#: Additive outcome of a decision record (``docs/api.md``): what happened to
#: the action it chose.  Omitted while unknown (auxiliary JEV-R/M/D records).
DECISION_EXECUTED = "executed"
DECISION_QUESTION = "question"
DECISION_ERROR = "error"
DECISION_STATUSES: tuple[str, ...] = (DECISION_EXECUTED, DECISION_QUESTION, DECISION_ERROR)

#: Action bands of ``docs/api.md`` (a question outside them is dropped).
BANDS: tuple[str, str, str] = ("auto", "hitl", "abstain")

#: JEV roles of the decision log (``meta.role`` of the request).
ROLES: tuple[str, ...] = ("JEV-R", "JEV-A", "JEV-M", "JEV-D")

#: The loop's marker for a question that asks *permission for an action*.
APPROVAL_ANSWER_MODE = "approval"
APPROVAL_CHECKPOINT_REASON = "checkpoint_mode"

#: Id prefix of an approval mirrored from a JEV-A hold without approval
#: provenance (see :func:`is_action_hold`) — distinct from the question id so the
#: explicit ``answer_mode == "approval"`` path keeps its documented id.
APPROVAL_ID_PREFIX = "appr-"

#: Audit mark of a decision whose execution was released by a human approval
#: (``docs/api.md``: the JEV-A record carries it in ``issues``).
RELEASED_BY_APPROVAL = "released_by_approval"

APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_REJECTED = "rejected"
APPROVAL_STATUSES: tuple[str, str, str] = (APPROVAL_PENDING, APPROVAL_APPROVED, APPROVAL_REJECTED)

#: Provenance fields of a queue entry (additive; all of them optional).
PROVENANCE_FIELDS: tuple[str, ...] = ("band", "role", "action_id", "why_pt", "source_data")


def _clean_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def mark_repeat(item: dict[str, Any], ts: str | None = None) -> dict[str, Any]:
    """Bump the additive ``attempts``/``last_seen_ts`` of an already pending ask.

    This is the **dedupe** answer to a re-ask of the same ``action_id``: the
    pending entry is kept and counts the repeat instead of a second
    question/approval being created (the checkpoint loop publishes one ask per
    step for the same held action).
    """
    item[ATTEMPTS] = int(item.get(ATTEMPTS) or 1) + 1
    item[LAST_SEEN_TS] = ts or utc_now_iso()
    return item


def _page(
    items: list[dict[str, Any]], *, limit: int | None = None, before: str | None = None
) -> list[dict[str, Any]]:
    """The ``?limit=&before=`` page of ``items`` (kept in their given order).

    ``before`` is an exclusive ISO-8601 cursor: only entries with a strictly
    older ``ts`` stay.  ``limit`` then keeps the **most recent** entries of what
    remains (still oldest-first in the result), which is what makes
    ``before=<ts of the first item>`` walk history backwards.
    """
    if before is not None:
        cutoff = parse_iso(before)
        if cutoff is None:
            raise ValueError(f"before must be an ISO-8601 timestamp, got {before!r}")
        picked: list[dict[str, Any]] = []
        for item in items:
            stamp = parse_iso(str(item.get("ts") or ""))
            if stamp is not None and stamp < cutoff:
                picked.append(item)
        items = picked
    if limit is not None and len(items) > limit:
        items = items[-limit:]
    return list(items)


def _source_action_id(item: Mapping[str, Any]) -> str | None:
    direct = _clean_str(item.get("action_id"))
    if direct:
        return direct
    source = item.get("source_data")
    if isinstance(source, Mapping):
        return _clean_str(source.get("action_id"))
    return None


def _is_approval_source(source: Mapping[str, Any] | None) -> bool:
    if not isinstance(source, Mapping):
        return False
    return (
        str(source.get("answer_mode") or "") == APPROVAL_ANSWER_MODE
        or str(source.get("reason") or "") == APPROVAL_CHECKPOINT_REASON
        or str(source.get("route_reason") or "") == APPROVAL_CHECKPOINT_REASON
    )


#: Source-data keys that say *why* the loop asked (routing provenance).
ROUTING_SOURCE_KEYS: tuple[str, ...] = ("reason", "answer_mode", "route_reason")


def is_approval_question(item: Mapping[str, Any]) -> bool:
    """True when a queue entry asks permission to execute an action.

    The loop's payload says so either explicitly (``source_data.answer_mode ==
    "approval"``) or through the checkpoint routing reason
    (``source_data.reason``/``route_reason == "checkpoint_mode"``).  A question
    without an ``action_id`` is never an approval — there is no action to approve.
    """
    if not isinstance(item, Mapping):
        return False
    source = item.get("source_data")
    if not _is_approval_source(source if isinstance(source, Mapping) else None):
        return False
    return _source_action_id(item) is not None


def is_action_hold(item: Mapping[str, Any]) -> bool:
    """True for a JEV-A question that holds an action and has no routing provenance.

    Fallback for a ``question_new`` payload that carries the provenance fields
    (``role == "JEV-A"``, a non-empty ``action_id``) but whose ``source_data`` is
    empty or absent — no ``answer_mode``/``reason``/``route_reason`` to recognise.
    In ``checkpoint`` mode a held action *is* a request for permission, so the
    question is mirrored too (id :data:`APPROVAL_ID_PREFIX` + question id).

    A question whose provenance says it is something else (a ``band_hitl`` doubt,
    a ``needs_human`` escalation, …) is **not** an approval and stays out.
    """
    if not isinstance(item, Mapping):
        return False
    if _clean_str(item.get("role")) != "JEV-A":
        return False
    if _source_action_id(item) is None:
        return False
    source = item.get("source_data")
    if not isinstance(source, Mapping) or not source:
        return True
    return not any(_clean_str(source.get(key)) for key in ROUTING_SOURCE_KEYS)


def _valid_band(value: Any) -> str | None:
    """A band from an event, or ``None`` (unknown junk never reaches the API)."""
    text = _clean_str(value)
    if text is None:
        return None
    text = text.lower()
    return text if text in BANDS else None


def normalize_question(question: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise a ``question_new`` payload into the queue entry of the contract.

    The first six fields are frozen by ``docs/api.md``; the provenance fields
    (``band``, ``role``, ``action_id``, ``why_pt``, ``source_data``) are
    **additive** and only present when they are actually known — an event that
    carries none of them produces exactly the original six-field entry.
    """
    item = dict(question)

    source = item.get("source_data")
    source = dict(source) if isinstance(source, Mapping) else None

    # A banda pode vir no campo próprio (o loop põe lá) ou só no ``source_data``
    # (o formatador põe lá); a primeira válida ganha, lixo é descartado.
    band = _valid_band(item.get("band")) or _valid_band((source or {}).get("band"))
    if band:
        item["band"] = band
    else:
        item.pop("band", None)

    action_id = _source_action_id(item)
    if action_id:
        item["action_id"] = action_id
    else:
        item.pop("action_id", None)

    why_pt = _clean_str(item.get("why_pt"))
    if why_pt:
        item["why_pt"] = why_pt
    else:
        item.pop("why_pt", None)

    role = _clean_str(item.get("role"))
    if role not in ROLES:
        # The loop does not publish the role; the app imputes it from what the
        # question is about: a held action (JEV-A) or the doubt escalation
        # (JEV-D).  Only done when there is provenance at all, so an event
        # without any stays byte-identical to the frozen six fields.
        role = None
        if band or action_id or why_pt or source is not None:
            role = "JEV-A" if action_id else "JEV-D"
    if role:
        item["role"] = role
    else:
        item.pop("role", None)

    if source is not None:
        item["source_data"] = source
    else:
        item.pop("source_data", None)
    return item


def approval_from_question(question: Mapping[str, Any]) -> dict[str, Any]:
    """An approval entry mirrored from an approval question.

    For an explicit approval question (``answer_mode == "approval"`` /
    checkpoint routing) the question id *is* the approval id: approving/rejecting
    an approval is answering that question, which is exactly how the loop's
    checkpoint flow consumes the decision (through the question-answer path,
    into memory).  A bare JEV-A hold (see :func:`is_action_hold`) gets the
    additive id :data:`APPROVAL_ID_PREFIX` + question id — same idempotence
    (one question = one approval), a different question to answer.
    """
    qid = str(question.get("id") or "")
    source = question.get("source_data")
    source = source if isinstance(source, Mapping) else {}
    action_id = _source_action_id(question) or ""
    explicit = is_approval_question(question)
    entry: dict[str, Any] = {
        "id": qid if explicit else f"{APPROVAL_ID_PREFIX}{qid}",
        "question_id": qid,
        "ts": question.get("ts") or utc_now_iso(),
        "action_id": action_id,
        "action_name_en": _clean_str(source.get("action_en")) or action_id,
        "question": str(question.get("question") or ""),
        "band": _clean_str(question.get("band")) or _clean_str(source.get("band")) or "hitl",
        "status": APPROVAL_PENDING,
        "note": None,
        "decided_at": None,
    }
    description = _clean_str(source.get("description_en"))
    if description:
        entry["description_en"] = description
    # A pergunta já pode ter sido repetida (dedupe na fila): a aprovação espelha
    # a contagem, senão parecia uma pergunta nova aos olhos do humano.
    if question.get(ATTEMPTS):
        entry[ATTEMPTS] = int(question[ATTEMPTS])
    if question.get(LAST_SEEN_TS):
        entry[LAST_SEEN_TS] = question[LAST_SEEN_TS]
    return entry


def stub_metrics(ts: str | None = None) -> dict[str, Any]:
    """Zeroed payload with the 8 contract groups and the ``stub`` marker."""
    return {
        "ts": ts or utc_now_iso(),
        "health": {"wounds": [], "status": "healthy"},
        "needs": {"hunger": "none", "thirst": "none", "sleep": "rested"},
        "inventory": {"weight_kg": 0.0, "items": []},
        "threats": {"level": "low", "combat": False, "hostiles": []},
        "location": {"region": "", "path": []},
        "objective_progress": {"percent": 0.0, "active_subgoal_id": None},
        "combat_history": [],
        "agent": {
            "decisions_per_min": 0.0,
            "confidence_avg": 0.0,
            "calls": 0,
            "cost_usd": 0.0,
            "latency_ms": 0,
        },
        "source": SOURCE_STUB,
    }


def stub_screen(ts: str | None = None) -> dict[str, Any]:
    """Empty screen translation (no original text is ever shown)."""
    return {"translated_pt": "", "ts": ts or utc_now_iso(), "source": SOURCE_STUB}


@dataclass
class CampaignRuntime:
    """Live store of one campaign: decisions, questions, approvals, inputs, metrics."""

    campaign_id: str
    decisions: list[dict[str, Any]] = field(default_factory=list)
    questions: dict[str, dict[str, Any]] = field(default_factory=dict)
    approvals: dict[str, dict[str, Any]] = field(default_factory=dict)
    inputs: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] | None = None
    screen: dict[str, Any] | None = None
    _question_seq: int = 0
    _input_seq: int = 0

    # ------------------------------------------------------------------ decisions

    def add_decision(self, decision: dict[str, Any]) -> dict[str, Any]:
        item = dict(decision)
        item.setdefault("id", f"d-{len(self.decisions) + 1}")
        item.setdefault("ts", utc_now_iso())
        self.decisions.append(item)
        del self.decisions[:-DECISIONS_LIMIT]
        return item

    def list_decisions(self, *, limit: int | None = None, before: str | None = None) -> list[dict[str, Any]]:
        """The decision log, paginated (``?limit=&before=`` of ``docs/api.md``)."""
        return _page(self.decisions, limit=limit, before=before)

    def mark_decision_released(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        """Mirror :data:`RELEASED_BY_APPROVAL` onto the stored JEV-A decision.

        The loop publishes the ``decision`` event before the release is known
        (step 6) and the marked record only inside the additive
        ``approval_released`` event (step 7) — without this mirror,
        ``GET .../decisions`` could never show the documented
        ``released_by_approval`` audit mark.  The entry is found by the ``ts`` of
        the released record (the same record the event carries) and the mark is
        added at most once.  The same mirror also records the additive
        ``status: "executed"`` (``docs/api.md``): the approval released this
        exact execution.
        """
        item = self._decision_of(payload)
        if item is None:
            return None
        issues = [str(value) for value in item.get("issues") or ()]
        if RELEASED_BY_APPROVAL not in issues:
            issues.append(RELEASED_BY_APPROVAL)
            item["issues"] = issues
        item["status"] = DECISION_EXECUTED
        return item

    def mark_decision_executed(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        """Mirror ``status: "executed"`` (additive) onto the matching decision.

        Fired by the loop's additive ``action_executed`` event: the action the
        record chose was actually dispatched, so the log can tell an executed
        decision from one that only raised a question.
        """
        item = self._decision_of(payload)
        if item is None:
            return None
        item["status"] = DECISION_EXECUTED
        return item

    def mark_decision_question(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        """Mirror ``status: "question"`` onto the decision the question is about.

        The match is by ``action_id``: the latest undecided JEV-A record whose
        ``answers`` chose that exact action (the question would not exist if the
        action had not been held).  A question without an ``action_id`` (a JEV-D
        doubt) matches nothing and nothing is marked.
        """
        action_id = _source_action_id(payload)
        if not action_id:
            return None
        for item in reversed(self.decisions):
            if item.get("status") or str(item.get("agent") or "") != "JEV-A":
                continue
            answers = item.get("answers")
            if isinstance(answers, Mapping) and any(
                str(value) == action_id for value in answers.values()
            ):
                item["status"] = DECISION_QUESTION
                return item
        return None

    def mark_decision_error(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        """Mirror ``status: "error"`` onto the latest undecided action decision.

        The loop publishes ``error`` when a step failed (the held action never
        ran): the newest JEV-A record that never got a verdict is the one that
        died with the step.
        """
        for item in reversed(self.decisions):
            if item.get("status") or str(item.get("agent") or "") != "JEV-A":
                continue
            item["status"] = DECISION_ERROR
            return item
        return None

    def _decision_of(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        """The stored decision an event's ``decision`` ref points at (ts + agent)."""
        decision = payload.get("decision")
        decision = decision if isinstance(decision, Mapping) else {}
        ts = _clean_str(decision.get("ts"))
        agent = _clean_str(decision.get("agent")) or "JEV-A"
        for item in reversed(self.decisions):
            if str(item.get("agent") or "") != agent:
                continue
            if ts and str(item.get("ts") or "") != ts:
                continue
            return item
        return None

    # ------------------------------------------------------------------ questions

    def next_question_id(self) -> str:
        self._question_seq += 1
        return f"q-{self._question_seq}"

    def add_question(self, question: dict[str, Any]) -> dict[str, Any]:
        """Store a queue entry, keeping the provenance the loop published.

        Frozen fields get the same defaults as before; the additive provenance
        (``band``/``role``/``action_id``/``why_pt``/``source_data``) is kept when
        present and omitted when not (see :func:`normalize_question`).

        **Dedupe**: while an *unanswered* question for the same ``action_id`` is
        in the queue, a re-ask never creates another entry — the pending one
        gets its additive ``attempts``/``last_seen_ts`` bumped (one action held =
        one question to answer).  The store is capped at :data:`QUESTIONS_LIMIT`,
        evicting answered entries before pending ones.
        """
        item = normalize_question(question)
        qid = str(item.get("id") or self.next_question_id())
        item["id"] = qid
        item.setdefault("ts", utc_now_iso())
        item.setdefault("question", "")
        item["options"] = list(item.get("options") or [])
        item.setdefault("free_text_allowed", True)
        item.setdefault("status", "pending")
        pending = self._pending_question_for(_source_action_id(item))
        if pending is not None:
            # O re-pedido traz a proveniência **atual** (ex.: a ação passou de
            # dúvida ``hitl`` para pedido de aprovação do ``checkpoint``): a
            # entrada pendente é atualizada — sem isto, um ask herdado de outro
            # modo nunca seria espelhado como aprovação.
            for key in (*PROVENANCE_FIELDS, "format"):
                if key in item:
                    pending[key] = item[key]
            return mark_repeat(pending, item.get("ts"))
        self.questions[qid] = item
        self._evict_questions()
        return item

    def _pending_question_for(self, action_id: str | None) -> dict[str, Any] | None:
        """The unanswered question already asking about ``action_id`` (if any)."""
        if not action_id:
            return None
        for item in self.questions.values():
            if item.get("status") != "pending":
                continue
            if _source_action_id(item) == action_id:
                return item
        return None

    def _evict_questions(self) -> None:
        """Ring-buffer the queue past :data:`QUESTIONS_LIMIT`.

        Answered entries are the first to go (they are the proofs of asks long
        gone); the pending queue — what the human still has to answer — is only
        touched when the overflow leaves no other choice.
        """
        overflow = len(self.questions) - QUESTIONS_LIMIT
        if overflow <= 0:
            return
        answered = [item for item in self.questions.values() if item.get("status") != "pending"]
        pending = [item for item in self.questions.values() if item.get("status") == "pending"]
        for item in (answered + pending)[:overflow]:
            self.questions.pop(str(item.get("id") or ""), None)

    def get_question(self, question_id: str) -> dict[str, Any] | None:
        return self.questions.get(question_id)

    def list_questions(
        self,
        *,
        status: str | None = None,
        limit: int | None = None,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        """Questions oldest-first, optionally filtered and paginated."""
        items = list(self.questions.values())
        if status:
            items = [q for q in items if q.get("status") == status]
        items.sort(key=lambda q: str(q.get("ts") or ""))
        return _page(items, limit=limit, before=before)

    def answer_question(
        self,
        question_id: str,
        *,
        answer_text: str | None = None,
        option_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Mark a question answered (in memory) and return the stored question."""
        question = self.questions.get(question_id)
        if question is None:
            return None
        question["status"] = "answered"
        question["answer_text"] = answer_text
        question["option_id"] = option_id
        question["answered_at"] = utc_now_iso()
        return question

    def question_answer(self, question_id: str) -> dict[str, Any] | None:
        question = self.questions.get(question_id)
        if question is None:
            return None
        return {
            "id": question_id,
            "status": question.get("status", "pending"),
            "answer_text": question.get("answer_text"),
            "option_id": question.get("option_id"),
        }

    # ----------------------------------------------------------------- approvals

    def add_approval(self, question: dict[str, Any]) -> dict[str, Any] | None:
        """Mirror an approval question as a ``pending`` approval (``None`` if not one).

        Recognises both an explicit approval question (``answer_mode``/checkpoint
        routing) and a bare JEV-A hold (``role == "JEV-A"`` + ``action_id``, no
        routing provenance) — see :func:`is_approval_question` /
        :func:`is_action_hold`.  Idempotent: one question maps to one entry, and a
        re-published question never downgrades an already decided/released one.

        **Dedupe**: while a ``pending`` approval for the same ``action_id``
        exists, a re-ask bumps its additive ``attempts``/``last_seen_ts`` instead
        of creating a second one (the checkpoint loop re-publishes the same held
        action every step).  The pending queue is capped at
        :data:`APPROVAL_PENDING_LIMIT` — the oldest pending entries are dropped.
        """
        if not (is_approval_question(question) or is_action_hold(question)):
            return None
        entry = approval_from_question(question)
        if not entry["id"] or not entry["question_id"]:
            return None
        # O re-pedido chega pela pergunta (que já foi "vista" agora): é esse o
        # momento do bump, não o ts de criação da pergunta original.
        seen_ts = _clean_str(question.get(LAST_SEEN_TS)) or entry.get("ts")
        pending = self._pending_approval_for(str(entry.get("action_id") or ""))
        if pending is not None:
            return mark_repeat(pending, seen_ts)
        existing = self.approvals.get(entry["id"])
        if existing is not None:
            return mark_repeat(existing, seen_ts)
        self.approvals[entry["id"]] = entry
        self._trim_pending_approvals()
        return entry

    def _pending_approval_for(self, action_id: str) -> dict[str, Any] | None:
        """The ``pending`` approval already covering ``action_id`` (if any)."""
        if not action_id:
            return None
        for item in self.approvals.values():
            if item.get("status") != APPROVAL_PENDING:
                continue
            if str(item.get("action_id") or "") == action_id:
                return item
        return None

    def _trim_pending_approvals(self) -> None:
        """Keep the pending queue at :data:`APPROVAL_PENDING_LIMIT` (oldest out)."""
        pending = self.list_approvals(status=APPROVAL_PENDING)
        for item in pending[: max(0, len(pending) - APPROVAL_PENDING_LIMIT)]:
            self.drop_approval(str(item.get("id") or ""))

    def drop_approval(self, approval_id: str) -> dict[str, Any] | None:
        """Remove an approval from the store (dropped duplicates / cap overflow)."""
        return self.approvals.pop(str(approval_id or ""), None)

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        return self.approvals.get(approval_id)

    def list_approvals(self, *, status: str | None = APPROVAL_PENDING) -> list[dict[str, Any]]:
        """Approvals, oldest first.  ``status=None`` returns the whole history."""
        items = list(self.approvals.values())
        if status:
            items = [item for item in items if item.get("status") == status]
        items.sort(key=lambda item: str(item.get("ts") or ""))
        return items

    def decide_approval(
        self,
        approval_id: str,
        *,
        status: str,
        note: str | None = None,
    ) -> dict[str, Any] | None:
        """Mark an approval ``approved``/``rejected``; ``None`` when unknown.

        ``status`` outside :data:`APPROVAL_STATUSES` is rejected with
        :class:`ValueError` (a programming error, not a client one).
        """
        if status not in APPROVAL_STATUSES:
            raise ValueError(f"unknown approval status {status!r}")
        entry = self.approvals.get(approval_id)
        if entry is None:
            return None
        entry["status"] = status
        entry["note"] = note
        entry["decided_at"] = utc_now_iso()
        return entry

    # --------------------------------------------------------------------- inputs

    def next_input_id(self) -> str:
        self._input_seq += 1
        return f"in-{self._input_seq}"

    def add_input(self, text: str, *, ts: str | None = None) -> dict[str, Any]:
        item = {"id": self.next_input_id(), "text": text, "ts": ts or utc_now_iso()}
        self.inputs.append(item)
        del self.inputs[:-HISTORY_LIMIT]
        return item

    def list_inputs(self) -> list[dict[str, Any]]:
        return list(self.inputs)

    # ------------------------------------------------------------ metrics / screen

    def set_metrics(self, metrics: dict[str, Any]) -> dict[str, Any]:
        payload = dict(metrics)
        payload.setdefault("ts", utc_now_iso())
        payload["source"] = SOURCE_LIVE
        self.metrics = payload
        return payload

    def get_metrics(self) -> dict[str, Any]:
        if self.metrics is None:
            return stub_metrics()
        payload = dict(self.metrics)
        payload["source"] = SOURCE_LIVE
        return payload

    def set_screen(self, translated_pt: str, *, ts: str | None = None) -> dict[str, Any]:
        self.screen = {
            "translated_pt": translated_pt or "",
            "ts": ts or utc_now_iso(),
            "source": SOURCE_LIVE,
        }
        return self.screen

    def get_screen(self) -> dict[str, Any]:
        return dict(self.screen) if self.screen else stub_screen()

    # -------------------------------------------------------------------- helpers

    def source_of(self, what: str) -> str:
        if what == "metrics":
            return SOURCE_LIVE if self.metrics else SOURCE_STUB
        if what == "screen":
            return SOURCE_LIVE if self.screen else SOURCE_STUB
        if what == "decisions":
            return SOURCE_LIVE if self.decisions else SOURCE_STUB
        if what == "questions":
            return SOURCE_LIVE if self.questions else SOURCE_STUB
        if what == "approvals":
            return SOURCE_LIVE if self.approvals else SOURCE_STUB
        return SOURCE_STUB
