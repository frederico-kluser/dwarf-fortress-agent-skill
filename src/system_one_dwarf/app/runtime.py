"""Live per-campaign state consumed by the REST reads (and fed by the bus).

The agent loop is a *separate* piece.  Until it exists, the endpoints that
depend on it must still answer with **contract-correct** payloads: empty/zeroed
data plus a ``"source": "stub"`` marker (object payloads) and an ``X-Source:
stub`` response header (list payloads, which cannot carry a marker without
breaking the array shape).

When the loop is wired, it calls :meth:`AppServices.publish_event` — the runtime
store is updated first, then the event fans out on the WebSocket channel — and
the very same endpoints start answering with live data (``"source": "live"``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..campaign.clock import utc_now_iso

SOURCE_LIVE = "live"
SOURCE_STUB = "stub"

#: How many decisions/inputs we keep in memory per campaign (ring buffer).
HISTORY_LIMIT = 500


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
    """Live store of one campaign: decisions, questions, inputs, metrics, screen."""

    campaign_id: str
    decisions: list[dict[str, Any]] = field(default_factory=list)
    questions: dict[str, dict[str, Any]] = field(default_factory=dict)
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

    # ------------------------------------------------------------------ questions

    def next_question_id(self) -> str:
        self._question_seq += 1
        return f"q-{self._question_seq}"

    def add_question(self, question: dict[str, Any]) -> dict[str, Any]:
        item = dict(question)
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
        return SOURCE_STUB
