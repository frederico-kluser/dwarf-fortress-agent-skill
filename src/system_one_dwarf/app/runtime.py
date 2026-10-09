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

from ..campaign.clock import utc_now_iso

SOURCE_LIVE = "live"
SOURCE_STUB = "stub"

#: How many decisions/inputs we keep in memory per campaign (ring buffer).
HISTORY_LIMIT = 500

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
        del self.decisions[:-HISTORY_LIMIT]
        return item

    def list_decisions(self) -> list[dict[str, Any]]:
        return list(self.decisions)

    def mark_decision_released(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        """Mirror :data:`RELEASED_BY_APPROVAL` onto the stored JEV-A decision.

        The loop publishes the ``decision`` event before the release is known
        (step 6) and the marked record only inside the additive
        ``approval_released`` event (step 7) — without this mirror,
        ``GET .../decisions`` could never show the documented
        ``released_by_approval`` audit mark.  The entry is found by the ``ts`` of
        the released record (the same record the event carries) and the mark is
        added at most once.
        """
        decision = payload.get("decision")
        decision = decision if isinstance(decision, Mapping) else {}
        ts = _clean_str(decision.get("ts"))
        agent = _clean_str(decision.get("agent")) or "JEV-A"
        for item in reversed(self.decisions):
            if str(item.get("agent") or "") != agent:
                continue
            if ts and str(item.get("ts") or "") != ts:
                continue
            issues = [str(value) for value in item.get("issues") or ()]
            if RELEASED_BY_APPROVAL not in issues:
                issues.append(RELEASED_BY_APPROVAL)
                item["issues"] = issues
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
        """
        item = normalize_question(question)
        qid = str(item.get("id") or self.next_question_id())
        item["id"] = qid
        item.setdefault("ts", utc_now_iso())
        item.setdefault("question", "")
        item["options"] = list(item.get("options") or [])
        item.setdefault("free_text_allowed", True)
        item.setdefault("status", "pending")
        self.questions[qid] = item
        return item

    def get_question(self, question_id: str) -> dict[str, Any] | None:
        return self.questions.get(question_id)

    def list_questions(self, *, status: str | None = None) -> list[dict[str, Any]]:
        items = list(self.questions.values())
        if status:
            items = [q for q in items if q.get("status") == status]
        items.sort(key=lambda q: str(q.get("ts") or ""))
        return items

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
        """
        if not (is_approval_question(question) or is_action_hold(question)):
            return None
        entry = approval_from_question(question)
        if not entry["id"] or not entry["question_id"]:
            return None
        existing = self.approvals.get(entry["id"])
        if existing is not None and existing.get("status") != APPROVAL_PENDING:
            return existing
        self.approvals[entry["id"]] = entry
        return entry

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
