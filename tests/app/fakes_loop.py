"""Offline fakes for the app-level loop wiring tests (``tests/app``).

The fakes use the **real** types of the neighbouring pieces (``GameState`` from a
snapshot payload, ``Answer``/``DecisionResult`` of the JEV client, ``LLMResult``
of the DeepSeek client, ``ExecutionResult``/``TimeAdvanceResult`` of the loop), so
a wiring test exercises the real ``AgentLoop`` against the real contracts —
without network, without the game and without CoALA (the memory is the shared
``FakeCoalaCli`` from ``tests/app/fakes.py``).

``FakeLoop`` is the *other* kind of fake: a stand-in for the whole loop, used to
test the runtime/control plumbing (start/stop/restart, event mirroring) without
any decision machinery.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence

from system_one_dwarf.agent.llm import LLMResult, LLMUsage
from system_one_dwarf.agent.types import ExecutionResult, TimeAdvanceResult
from system_one_dwarf.game.models import GameState
from system_one_dwarf.jev.client import Answer, DecisionResult, Usage

DEFAULT_QUESTION_JSON = json.dumps(
    {
        "question": "Atravessar o rio a nadar ou procurar uma ponte?",
        "options": ["Nadar", "Procurar uma ponte a leste"],
        "why": "As duas alternativas ficaram empatadas.",
    },
    ensure_ascii=False,
)


# ------------------------------------------------------------------ game state


def state_payload(
    *,
    hostiles: Sequence[Mapping[str, Any]] = (),
    region: str = "The Hills",
    surroundings: str = "a road runs north",
    hour: float | None = 12.0,
    screen_text: str = "You see a goblin skulker here.",
    events: Sequence[str] = ("You arrive at a clearing.",),
    adventurer: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Raw ``snapshot`` payload (the ``df_bridge`` contract), minimal but valid."""
    payload: dict[str, Any] = {
        "schema_version": 1,
        "ts": "2026-10-08T12:00:00.000Z",
        "mode": "adventure",
        "focus": "Adventure",
        "screen_text": screen_text,
        "warnings": [],
        "gamelog_events": [{"text": str(event)} for event in events],
        "metrics": {
            "health_injuries": {
                "status": "healthy",
                "status_conditions": [],
                "wounds": [],
                "wound_count": 0,
                "pain": None,
                "blood_current": 100.0,
                "blood_max": 100.0,
            },
            "needs_state": {
                "hunger": "none",
                "thirst": "none",
                "sleep": "rested",
                "hunger_timer": None,
                "thirst_timer": None,
                "sleepiness_timer": None,
                "stress_level": None,
            },
            "inventory": {
                "items": [],
                "item_count": 0,
                "total_weight_kg": 0.0,
                "capacity_kg": None,
                "equipped_weapon": None,
                "equipped_armor": [],
            },
            "threats": {
                "count": len(hostiles),
                "level": "high" if hostiles else "none",
                "hostiles": [dict(item) for item in hostiles],
            },
            "location": {
                "x": 88,
                "y": 72,
                "z": 0,
                "world": "Testworld",
                "region": region,
                "site": None,
                "site_type": None,
                "surroundings": surroundings,
                "tile": None,
                "travel_destination": None,
            },
            "game_clock": {
                "date_text": "15th Granite 125",
                "year": 125,
                "month": "Granite",
                "day": 15,
                "season": "Spring",
                "tick": None,
                "hour": hour,
            },
            "units_nearby": {"count": 0, "units": []},
            "events": [],
        },
    }
    if adventurer is not None:
        payload["adventurer"] = dict(adventurer)
    return payload


def game_state(**kwargs: Any) -> GameState:
    """Typed ``GameState`` (goes through the real snapshot parser)."""
    return GameState.from_dict(state_payload(**kwargs))


def hostile(name: str = "goblin skulker", distance: float = 6.0, direction: str = "NE") -> dict[str, Any]:
    return {"name": name, "distance": distance, "direction": direction, "hostile": True,
            "visible": True, "great_danger": False}


# ------------------------------------------------------------------------ game


class FakeGame:
    """``GameStateClient`` fake: hands out snapshots from a queue (last repeats)."""

    def __init__(self, *states: GameState, bridge_path: str = "/repo/dfhack/df_bridge.py") -> None:
        self.states = list(states) or [game_state()]
        self.bridge_path = bridge_path
        self.python = "python3"
        self.df_path = None
        self.port = None
        self.via = "auto"
        self.calls = 0

    def snapshot(self) -> GameState:
        self.calls += 1
        index = min(self.calls - 1, len(self.states) - 1)
        return self.states[index]


# ------------------------------------------------------------------------- JEV


def _choice(name: str, value: Any, confidence: float | None,
            probabilities: Mapping[str, float] | None = None) -> Answer:
    from system_one_dwarf.jev.bands import band_for_choice_or_score

    try:
        band = band_for_choice_or_score(confidence)
    except Exception:  # a bogus confidence is low confidence, never auto
        band = "hitl"
    return Answer(name=name, type="choice", value=value, confidence=confidence,
                  probabilities=dict(probabilities) if probabilities else None, legend=None,
                  band=band, chosen_probability=(probabilities or {}).get(value))


def _noul(name: str, value: float | None) -> Answer:
    from system_one_dwarf.jev.bands import band_for_noul

    return Answer(name=name, type="noul", value=value, confidence=None, probabilities=None,
                  legend=None, band=band_for_noul(value), chosen_probability=None)


class FakeJev:
    """JEV client fake: answers by ``meta.role`` with real ``Answer``/``DecisionResult``."""

    def __init__(
        self,
        *,
        action_id: str | None = None,
        action_confidence: float | None = 0.62,
        needs_human: float = 0.02,
        update_memory: float = 0.0,
        ask_human: float = 0.5,
        question_format: str = "binary",
    ) -> None:
        self.action_id = action_id
        self.action_confidence = action_confidence
        self.needs_human = needs_human
        self.update_memory = update_memory
        self.ask_human = ask_human
        self.question_format = question_format
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def decide_request(self, request: Mapping[str, Any], session_id: str | None = None, **kw: Any) -> DecisionResult:
        role = str((request.get("meta") or {}).get("role") or "JEV-X")
        self.requests.append((role, dict(request)))
        return DecisionResult(
            answers=self._answers(role, request),
            usage=Usage(input_tokens=800, output_tokens=40, cost_usd=0.0001),
            latency_ms=120,
            model="typesafe/jev-1.13",
            raw={"role": role},
            provider="typesafe",
        )

    def _answers(self, role: str, request: Mapping[str, Any]) -> dict[str, Answer]:
        questions = request.get("questions") or {}
        if role == "JEV-A":
            criteria = list((questions.get("action") or {}).get("criteria") or {})
            chosen = self.action_id or next((key for key in criteria if key != "other"), "other")
            probabilities = {key: 0.02 for key in criteria}
            probabilities[chosen] = self.action_confidence or 0.0
            return {
                "action": _choice("action", chosen, self.action_confidence, probabilities),
                "urgency": _choice("urgency", 0.66, 0.8),
                "needs_human": _noul("needs_human", self.needs_human),
            }
        if role == "JEV-M":
            return {
                "update_memory": _noul("update_memory", self.update_memory),
                "narrator_effort": _choice("narrator_effort", "low", 0.8),
                "record_type": _choice("record_type", "episode", 0.8),
            }
        if role == "JEV-D":
            return {
                "ask_human": _noul("ask_human", self.ask_human),
                "question_format": _choice("question_format", self.question_format, 0.7),
            }
        if role == "JEV-R":
            return {name: _noul(name, 0.05) for name in questions if str(name).startswith("include_")}
        return {}

    @property
    def roles(self) -> list[str]:
        return [role for role, _request in self.requests]


# ------------------------------------------------------------------------- LLM


class FakeLLM:
    """DeepSeek client fake: returns one canned JSON payload per call."""

    def __init__(self, text: str = DEFAULT_QUESTION_JSON) -> None:
        self.text = text
        self.calls: list[dict[str, Any]] = []

    def complete(self, system: str, user: str, *, reasoning: Any = None, max_tokens: int | None = None,
                 temperature: float | None = None, purpose: str | None = None) -> LLMResult:
        self.calls.append({"system": system, "user": user, "reasoning": reasoning,
                           "purpose": purpose})
        return LLMResult(text=self.text, usage=LLMUsage(input_tokens=200, output_tokens=60,
                                                        cost_usd=0.00002),
                         latency_ms=90, model="deepseek/deepseek-v4.1-flash", reasoning="off")

    def is_configured(self) -> bool:
        return True


# -------------------------------------------------------------------- executor


class FakeExecutor:
    """``MacroExecutor`` fake: never touches the bridge, records the actions."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.action_ids: list[str] = []
        self.advanced: list[float] = []

    def execute(self, action: Any, state: Any = None) -> ExecutionResult:
        action_id = getattr(action, "id", str(action))
        self.action_ids.append(action_id)
        return ExecutionResult(action_id=action_id, status="executed", ok=True, dsl="wait short",
                               executed_dsl="wait short", steps_planned=1, steps_executed=1)

    def advance_time(self, seconds: float) -> TimeAdvanceResult:
        self.advanced.append(float(seconds))
        return TimeAdvanceResult(seconds=float(seconds), ok=True, unpaused=True, paused=True)


# ------------------------------------------------------------------ whole loop


class FakeLoop:
    """Stand-in for the whole ``AgentLoop`` (runtime/control plumbing tests).

    ``run()`` publishes its configured events through the sink and then blocks
    until :meth:`stop` — exactly the shape of the real ``run()`` (a loop that
    only ends on the kill-switch).  With ``exit_after_events`` it returns at
    once, which is how a loop that ended on its own is simulated.
    """

    def __init__(
        self,
        *,
        sink: Any = None,
        events: Sequence[tuple[str, dict[str, Any]]] = (),
        exit_after_events: bool = False,
        step_delay: float = 0.005,
    ) -> None:
        self.sink = sink
        self.events = list(events)
        self.exit_after_events = exit_after_events
        self.step_delay = float(step_delay)
        self.run_calls = 0
        self.stop_calls = 0
        self.closed = False
        self._stopped = threading.Event()

    def publish(self, event_type: str, payload: Mapping[str, Any]) -> None:
        if self.sink is not None:
            self.sink.publish(event_type, dict(payload))

    def run(self, max_steps: int | None = None, until: Any = None) -> Any:
        self.run_calls += 1
        for event_type, payload in self.events:
            self.publish(event_type, payload)
        if self.exit_after_events:
            return SimpleNamespace(steps=1, as_dict=lambda: {"steps": 1, "stopped_reason": "scripted"})
        while not self._stopped.is_set():
            time.sleep(self.step_delay)
        return SimpleNamespace(steps=0, as_dict=lambda: {"steps": 0, "stopped_reason": "stopped"})

    def stop(self) -> None:
        self.stop_calls += 1
        self._stopped.set()

    def close(self) -> None:
        self.closed = True


class LoopFactory:
    """Builds :class:`FakeLoop` instances and remembers every one of them."""

    def __init__(
        self,
        *,
        events: Sequence[tuple[str, dict[str, Any]]] = (),
        exit_after_events: bool = False,
        fails: Exception | None = None,
    ) -> None:
        self.events = list(events)
        self.exit_after_events = exit_after_events
        self.fails = fails
        self.loops: list[FakeLoop] = []
        self.calls = 0

    def __call__(self, sink: Any = None) -> FakeLoop:
        self.calls += 1
        if self.fails is not None:
            raise self.fails
        loop = FakeLoop(sink=sink, events=self.events, exit_after_events=self.exit_after_events)
        self.loops.append(loop)
        return loop

    @property
    def last(self) -> FakeLoop | None:
        return self.loops[-1] if self.loops else None


def approval_question(
    *,
    qid: str = "q-3-1",
    action_id: str = "goto:river_bank",
    action_en: str = "Go to the river bank",
    band: str = "auto",
    ts: str = "2026-10-08T12:00:05.000Z",
) -> dict[str, Any]:
    """The exact ``question_new`` payload of a checkpoint approval (see ``loop.py``)."""
    return {
        "id": qid,
        "ts": ts,
        "question": f"Posso executar «{action_id}»?",
        "options": [
            {"id": "yes", "text": "Sim, executar"},
            {"id": "no", "text": "Não, escolher outra ação"},
            {"id": "other", "text": "Outra resposta (escrever abaixo)"},
        ],
        "free_text_allowed": True,
        "status": "pending",
        "format": "multiple",
        "why_pt": "o modo checkpoint exige aprovação humana para todas as ações (banda auto)",
        "source_data": {
            "action_id": action_id,
            "action_en": action_en,
            "description_en": "Travel to the river bank",
            "band": band,
            "reason": "checkpoint_mode",
            "answer_mode": "approval",
        },
        "band": band,
        "action_id": action_id,
    }


def hitl_question(
    *,
    qid: str = "q-1-1",
    action_id: str = "goto:river_bank",
    band: str = "hitl",
    ts: str = "2026-10-08T12:00:05.000Z",
) -> dict[str, Any]:
    """The ``question_new`` payload of a band ``hitl`` question (formatter shape)."""
    return {
        "id": qid,
        "ts": ts,
        "question": "Atravessar o rio a nadar ou procurar uma ponte?",
        "options": [
            {"id": "opt-1", "text": "Nadar"},
            {"id": "opt-2", "text": "Procurar uma ponte a leste"},
        ],
        "free_text_allowed": True,
        "status": "pending",
        "format": "binary",
        "why_pt": "a confiança ficou na banda hitl (0.50–0.89): confirmar antes de executar",
        "source_data": {
            "reason": "band_hitl",
            "ask": None,
            "band": band,
            "confidence_available": True,
            "action_confidence": 0.62,
            "context": "The agent is unsure how to proceed.",
        },
        "band": band,
        "action_id": action_id,
    }


__all__ = [
    "DEFAULT_QUESTION_JSON",
    "FakeExecutor",
    "FakeGame",
    "FakeJev",
    "FakeLLM",
    "FakeLoop",
    "LoopFactory",
    "approval_question",
    "game_state",
    "hitl_question",
    "hostile",
    "state_payload",
]